"""
pipeline_orchestrator.py — Low-latency S2S pipeline orchestrator

Orchestrates 5 services:
  1. Vexyl STT (WebSocket streaming ASR)
  2. IndicTrans2 → English (CTranslate2)
  3. External LLM (OpenAI-compatible /v1/chat/completions)
  4. IndicTrans2 → Indic (CTranslate2)
  5. vLLM-Omni TTS (OpenAI-compatible /v1/audio/speech)

Concurrency model:
  ┌──────────┐    ┌──────────┐    ┌──────────┐    ┌──────────┐    ┌──────────┐
  │  Vexyl   │───▶│ Indic→EN│───▶│   LLM    │───▶│  EN→Indic│───▶│ OmniVoice│
  │   STT    │    │  Trans   │    │          │    │   Trans  │    │   TTS    │
  └──────────┘    └──────────┘    └──────────┘    └──────────┘    └──────────┘
       ▲               ▲              ▲               ▲               │
  audio in         sentence       english        response         audio out
       │            complete       ready          indic           chunks
       └──────────────────────── pipeline ───────────────────────────┘

Key latency strategies:
  - Sentence-level chunking (no waiting for full utterance)
  - Async pipeline stages connected by asyncio.Queue
  - Parallel processing: sentence N+1 translates while sentence N plays
  - Cross-fade hints sent to frontend for gap-free playback
"""

import asyncio
import itertools
import json
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Optional

from server.sentence_buffer import SentenceBuffer
from server.pantomatrix import extract_blendshapes
from server.local_tts import LocalTTS

logger = logging.getLogger(__name__)

# ── Default service URLs (override via env) ──────────────────────────────────
VEXYL_STT_URL = os.environ.get("VEXYL_STT_URL", "ws://localhost:8080")
VEXYL_STT_API_KEY = os.environ.get("VEXYL_STT_API_KEY", "")

LLM_BASE_URL = os.environ.get("LLM_BASE_URL", "http://localhost:8000/v1")
LLM_API_KEY = os.environ.get("LLM_API_KEY", "sk-no-key-required")
LLM_MODEL = os.environ.get("LLM_MODEL", "granite-4.0-nano")

TTS_SAMPLE_RATE = 24000

# Local TTS placeholder – generates silent PCM for testing
# The actual model download and inference can be added later.


INDIC_TRANS2_EN_INDIC = os.environ.get(
    "INDIC_TRANS2_EN_INDIC",
    os.path.join(os.path.expanduser("~"), ".cache", "ctranslate2", "en-indic-1B"),
)
INDIC_TRANS2_INDIC_EN = os.environ.get(
    "INDIC_TRANS2_INDIC_EN",
    os.path.join(os.path.expanduser("~"), ".cache", "ctranslate2", "indic-en-1B"),
)

TRANSLATION_DEVICE = os.environ.get("TRANSLATION_DEVICE", "cpu")

# ── Static responses for local testing ────────────────────────────────────────
_STATIC_RESPONSES = [
    "Hello! I am your voice avatar. How can I help you today?",
    "That is a really interesting question. Let me think about it.",
    "I understand. I am here to assist you with whatever you need.",
    "I am so sorry to hear that. I hope things improve soon.",
    "That is completely unacceptable! I will make sure this is fixed.",
    "Please do not worry. Everything will be just fine."
]
_static_response_cycle = itertools.cycle(_STATIC_RESPONSES)



# ── Data types ────────────────────────────────────────────────────────────────

@dataclass
class SentenceEvent:
    """A complete sentence flowing through the pipeline."""
    seq: int
    source_text: str           # original Indic text from STT
    english_text: str = ""     # after Indic→English translation
    response_text: str = ""    # after LLM
    indic_text: str = ""       # after English→Indic translation
    lang: str = ""
    stage_timing: dict = field(default_factory=dict)


@dataclass
class TTSChunk:
    """A chunk of generated audio with metadata."""
    seq: int
    audio_bytes: bytes
    sample_rate: int
    is_last: bool = False


# ── FLORES language code helpers (IndicTrans2 uses FLORES-200) ────────────────

# Mapping from BCP-47 / Vexyl language codes to FLORES-200 format
_LANG_TO_FLORES = {
    "as-IN": "asm_Beng", "bn-IN": "ben_Beng", "brx-IN": "brx_Deva",
    "doi-IN": "doi_Deva", "en-IN": "eng_Latn", "gu-IN": "guj_Gujr",
    "hi-IN": "hin_Deva", "kn-IN": "kan_Knda", "ks-IN": "kas_Arab",
    "ks-Arab": "kas_Arab", "ks-Deva": "kas_Deva",
    "kok-IN": "kok_Deva", "mai-IN": "mai_Deva", "ml-IN": "mal_Mlym",
    "mni-IN": "mni_Beng", "mni-Beng": "mni_Beng", "mni-Deva": "mni_Deva",
    "mr-IN": "mar_Deva", "ne-IN": "npi_Deva", "or-IN": "ory_Orya",
    "pa-IN": "pan_Guru", "sa-IN": "san_Deva", "sat-IN": "sat_Olck",
    "sd-IN": "snd_Arab", "sd-Arab": "snd_Arab", "sd-Deva": "snd_Deva",
    "ta-IN": "tam_Taml", "te-IN": "tel_Telu", "ur-IN": "urd_Arab",
}

_FLORES_TO_LANG = {v: k for k, v in _LANG_TO_FLORES.items()}

# Default to Hindi if unknown
_DEFAULT_FLORES_SRC = "hin_Deva"


def _to_flores(bcp47: str) -> str:
    return _LANG_TO_FLORES.get(bcp47, _DEFAULT_FLORES_SRC)


def _from_flores(flores: str) -> str:
    return _FLORES_TO_LANG.get(flores, "hi-IN")


# ── IndicTrans2 Engine (CTranslate2) ─────────────────────────────────────────

class IndicTrans2Engine:
    """
    Wraps two CTranslate2 translators for Indic→English and English→Indic.

    Tokenization uses sentencepiece BPE models shipped with the CT2 model
    directories. Language prefixes (FLORES codes) are prepended automatically.

    Model directories expected layout:
      <model_dir>/
        model.bin
        config.json
        vocabulary.json          # or shared_vocabulary.json
        sentencepiece.model      # BPE tokenizer model
    """

    def __init__(
        self,
        en_indic_path: str = INDIC_TRANS2_EN_INDIC,
        indic_en_path: str = INDIC_TRANS2_INDIC_EN,
        device: str = "cpu",
    ):
        self._en_indic_path = en_indic_path
        self._indic_en_path = indic_en_path
        self._device = device
        self._en_indic: Optional["ctranslate2.Translator"] = None
        self._indic_en: Optional["ctranslate2.Translator"] = None
        self._sp_en_indic: Optional["sentencepiece.SentencePieceProcessor"] = None
        self._sp_indic_en: Optional["sentencepiece.SentencePieceProcessor"] = None
        self._lock = asyncio.Lock()
        self._loaded = False

    async def load(self):
        """Load both models (idempotent, thread-safe)."""
        if self._loaded:
            return
        async with self._lock:
            if self._loaded:
                return
            logger.info(
                "[IndicTrans2] Loading en→indic from %s", self._en_indic_path
            )
            logger.info(
                "[IndicTrans2] Loading indic→en from %s", self._indic_en_path
            )
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, self._load_sync)
            self._loaded = True
            logger.info("[IndicTrans2] Both models ready on %s", self._device)

    def _load_sync(self):
        import ctranslate2
        import sentencepiece as spm

        # ── English → Indic ──
        self._sp_en_indic = spm.SentencePieceProcessor()
        sp_path = os.path.join(self._en_indic_path, "sentencepiece.model")
        if os.path.exists(sp_path):
            self._sp_en_indic.load(sp_path)
        else:
            logger.warning(
                "[IndicTrans2] No sentencepiece.model in %s; using dummy",
                self._en_indic_path,
            )
        self._en_indic = ctranslate2.Translator(
            self._en_indic_path, device=self._device
        )

        # ── Indic → English ──
        self._sp_indic_en = spm.SentencePieceProcessor()
        sp_path = os.path.join(self._indic_en_path, "sentencepiece.model")
        if os.path.exists(sp_path):
            self._sp_indic_en.load(sp_path)
        self._indic_en = ctranslate2.Translator(
            self._indic_en_path, device=self._device
        )

    async def indic_to_eng(self, text: str, src_lang: str) -> str:
        """
        Translate Indic text → English.
        src_lang: FLORES-200 code, e.g. "hin_Deva".
        """
        if not text.strip():
            return text
        if src_lang == "eng_Latn":
            return text
        await self.load()
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None, self._indic_to_eng_sync, text, src_lang
        )

    def _indic_to_eng_sync(self, text: str, src_lang: str) -> str:
        # Prepend source language token
        prefixed = f"__{src_lang}__ {text}"
        tokens = self._sp_indic_en.encode(prefixed, out_type=str)
        results = self._indic_en.translate_batch(
            [tokens], beam_size=4, max_batch_size=1
        )
        decoded = self._sp_indic_en.decode(results[0].tokens)
        # Strip any residual language tags
        return decoded.replace("__eng_Latn__", "").strip()

    async def eng_to_indic(self, text: str, tgt_lang: str) -> str:
        """
        Translate English → Indic text.
        tgt_lang: FLORES-200 code, e.g. "hin_Deva".
        """
        if not text.strip():
            return text
        if tgt_lang == "eng_Latn":
            return text
        await self.load()
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None, self._eng_to_indic_sync, text, tgt_lang
        )

    def _eng_to_indic_sync(self, text: str, tgt_lang: str) -> str:
        prefixed = f"__eng_Latn__ {text}"
        tokens = self._sp_en_indic.encode(prefixed, out_type=str)
        # BUG FIX: target_prefix must be an f-string so tgt_lang is interpolated
        # e.g. [["__hin_Deva__"]] not [["__{tgt_lang}__"]]
        results = self._en_indic.translate_batch(
            [tokens], beam_size=4, max_batch_size=1,
            target_prefix=[[f"__{tgt_lang}__"]],
        )
        decoded = self._sp_en_indic.decode(results[0].tokens)
        return decoded.replace(f"__{tgt_lang}__", "").strip()


# ── Pipeline Orchestrator ──────────────────────────────────────────────────────

class PipelineOrchestrator:
    """
    Manages the full S2S pipeline for a single session.

    Usage:
      orchestrator = PipelineOrchestrator(...)
      await orchestrator.run(client_ws, session_id, lang)
    """

    def __init__(
        self,
        vexyl_url: str = VEXYL_STT_URL,
        vexyl_api_key: str = VEXYL_STT_API_KEY,
        llm_base_url: str = LLM_BASE_URL,
        llm_api_key: str = LLM_API_KEY,
        llm_model: str = LLM_MODEL,
        tts_base_url: str = TTS_BASE_URL,
        tts_api_key: str = TTS_API_KEY,
        tts_model: str = TTS_MODEL,
        tts_voice: str = TTS_VOICE,
        trans_engine: Optional[IndicTrans2Engine] = None,
    ):
        self._vexyl_url = vexyl_url
        self._vexyl_api_key = vexyl_api_key
        self._llm_base_url = llm_base_url.rstrip("/")
        self._llm_api_key = llm_api_key
        self._llm_model = llm_model
        self._tts_base_url = tts_base_url.rstrip("/")
        self._tts_api_key = tts_api_key
        self._tts_model = tts_model
        self._tts_voice = tts_voice
        self._local_tts = LocalTTS()  # Initialize local TTS stub
        self._trans = trans_engine or IndicTrans2Engine()

        # Pipeline queues
        self._audio_out: asyncio.Queue = asyncio.Queue()     # bytes → client
        self._stt_out: asyncio.Queue = asyncio.Queue()       # str ← Vexyl
        self._sentences: asyncio.Queue = asyncio.Queue()     # SentenceEvent
        self._english_queue: asyncio.Queue = asyncio.Queue()  # SentenceEvent
        self._response_queue: asyncio.Queue = asyncio.Queue()  # SentenceEvent
        self._indic_queue: asyncio.Queue = asyncio.Queue()    # SentenceEvent
        self._tts_queue: asyncio.Queue = asyncio.Queue()      # TTSChunk

        self._session_id = ""
        self._lang_bcp47 = "hi-IN"
        self._flores_src = "hin_Deva"
        self._sentence_seq = 0
        self._cancel = asyncio.Event()
        self._active_tts_tasks: list[asyncio.Task] = []

    async def run(self, client_ws, session_id: str, lang: str):
        """
        Main entry point. Runs the full S2S pipeline for a WebSocket session.

        Args:
            client_ws: FastAPI WebSocket to the frontend.
            session_id: Unique session identifier.
            lang: BCP-47 language code (e.g. "hi-IN", "ta-IN", "ml-IN").
        """
        self._session_id = session_id
        self._lang_bcp47 = lang
        self._flores_src = _to_flores(lang)
        self._sentence_seq = 0
        self._cancel.clear()

        logger.info(
            "[Pipe:%s] Starting pipeline lang=%s flores=%s",
            session_id, lang, self._flores_src,
        )

        # Ensure translation engine is loaded
        await self._trans.load()

        # Launch pipeline workers
        workers = [
            asyncio.create_task(self._stt_worker(client_ws)),
            asyncio.create_task(self._trans1_worker()),
            asyncio.create_task(self._llm_worker()),
            asyncio.create_task(self._trans2_worker()),
            asyncio.create_task(self._tts_worker(client_ws)),
            asyncio.create_task(self._audio_sender(client_ws)),
            asyncio.create_task(self._monitor_worker(client_ws)),
        ]

        # Forward audio from client to Vexyl STT
        await self._forward_audio(client_ws)

        # Cancel all workers on exit
        self._cancel.set()
        for w in workers:
            w.cancel()
            try:
                await w
            except asyncio.CancelledError:
                pass

        # Cleanup any in-flight TTS tasks
        for t in self._active_tts_tasks:
            t.cancel()
        await asyncio.gather(*self._active_tts_tasks, return_exceptions=True)

        logger.info("[Pipe:%s] Pipeline ended", session_id)

    async def _forward_audio(self, client_ws):
        """Read binary audio chunks from client and relay to Vexyl STT."""
        vexyl_ws = await self._connect_vexyl()
        try:
            while not self._cancel.is_set():
                try:
                    raw = await asyncio.wait_for(
                        client_ws.receive(), timeout=0.5
                    )
                except asyncio.TimeoutError:
                    continue
                except Exception:
                    break

                if raw["type"] == "websocket.disconnect":
                    break

                if raw["type"] == "websocket.receive":
                    if raw.get("bytes"):
                        # Forward PCM to Vexyl STT
                        await vexyl_ws.send_bytes(raw["bytes"])
                    elif raw.get("text"):
                        try:
                            msg = json.loads(raw["text"])
                        except json.JSONDecodeError:
                            continue
                        if msg.get("type") == "stop":
                            await vexyl_ws.send_json({"type": "stop"})
                            break
                        elif msg.get("type") == "cancel":
                            await vexyl_ws.send_json({"type": "cancel"})
                            break
        finally:
            try:
                await vexyl_ws.close()
            except Exception:
                pass

    async def _connect_vexyl(self) -> "websockets.WebSocketClientProtocol":
        """Connect to Vexyl STT and start a session."""
        import websockets

        headers = {}
        if self._vexyl_api_key:
            headers["X-API-Key"] = self._vexyl_api_key

        logger.info(
            "[Pipe:%s] Connecting to Vexyl STT at %s",
            self._session_id, self._vexyl_url,
        )
        ws = await websockets.connect(
            self._vexyl_url, additional_headers=headers,
        )

        # Wait for "ready" message
        ready = json.loads(await ws.recv())
        if ready.get("type") != "ready":
            raise RuntimeError(f"Unexpected Vexyl handshake: {ready}")

        # Start session
        await ws.send(json.dumps({
            "type": "start",
            "lang": self._lang_bcp47,
            "session_id": self._session_id,
        }))
        started = json.loads(await ws.recv())
        if started.get("type") != "started":
            raise RuntimeError(f"Vexyl start failed: {started}")

        # Launch reader that feeds transcripts into stt_out
        asyncio.create_task(self._vexyl_reader(ws))

        logger.info("[Pipe:%s] Vexyl STT session started", self._session_id)
        return ws

    async def _vexyl_reader(self, vexyl_ws):
        """Read JSON messages from Vexyl and push transcripts into pipeline."""
        try:
            async for raw in vexyl_ws:
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    continue

                if msg.get("type") == "final":
                    text = msg.get("text", "").strip()
                    lang = msg.get("lang", self._lang_bcp47)
                    if text:
                        await self._stt_out.put({
                            "text": text,
                            "lang": lang,
                            "latency_ms": msg.get("latency_ms", 0),
                        })
                elif msg.get("type") == "error":
                    logger.error(
                        "[Pipe:%s] Vexyl error: %s",
                        self._session_id, msg.get("message", ""),
                    )
        except Exception as exc:
            logger.warning(
                "[Pipe:%s] Vexyl reader ended: %s", self._session_id, exc,
            )

    # ── Stage 1: STT → SentenceBuffer ───────────────────────────────────────

    async def _stt_worker(self, client_ws):
        """Receive transcripts from Vexyl, buffer into sentences, push downstream."""
        buf = SentenceBuffer()

        async def _send_transcript(text: str):
            try:
                await client_ws.send_json({
                    "type": "transcript",
                    "text": text,
                    "lang": self._lang_bcp47,
                    "session_id": self._session_id,
                })
            except Exception:
                pass

        try:
            while not self._cancel.is_set():
                try:
                    item = await asyncio.wait_for(
                        self._stt_out.get(), timeout=0.3
                    )
                except asyncio.TimeoutError:
                    continue

                text = item["text"]
                buf.push(text)
                await _send_transcript(text)

                # Drain ready sentences from buffer (non-blocking)
                while buf.has_ready():
                    sentence = buf.get_nowait()
                    if not sentence:
                        continue
                    event = SentenceEvent(
                        seq=self._sentence_seq,
                        source_text=sentence,
                        lang=self._lang_bcp47,
                        stage_timing={"stt_ready": time.monotonic()},
                    )
                    self._sentence_seq += 1
                    await self._sentences.put(event)

        except asyncio.CancelledError:
            pass
        finally:
            buf.close()

    # ── Stage 2: Indic → English Translation ────────────────────────────────

    async def _trans1_worker(self):
        """Take Indic sentences, translate to English, push downstream."""
        try:
            while not self._cancel.is_set():
                try:
                    event = await asyncio.wait_for(
                        self._sentences.get(), timeout=0.3
                    )
                except asyncio.TimeoutError:
                    continue

                t0 = time.monotonic()
                english = await self._trans.indic_to_eng(
                    event.source_text, self._flores_src
                )
                event.english_text = english
                event.stage_timing["trans1_done"] = time.monotonic() - t0
                await self._english_queue.put(event)

        except asyncio.CancelledError:
            pass

    # ── Stage 3: LLM ────────────────────────────────────────────────────────

    async def _llm_worker(self):
        """Take English sentences, use a static test response, push downstream."""
        try:
            while not self._cancel.is_set():
                try:
                    event = await asyncio.wait_for(
                        self._english_queue.get(), timeout=0.3
                    )
                except asyncio.TimeoutError:
                    continue

                t0 = time.monotonic()
                resp_text = next(_static_response_cycle)
                event.response_text = f"{resp_text} (You said: {event.english_text})"
                event.stage_timing["llm_done"] = time.monotonic() - t0
                await self._response_queue.put(event)

        except asyncio.CancelledError:
            pass

    # ── Stage 4: English → Indic Translation ────────────────────────────────

    async def _trans2_worker(self):
        """Take English responses, translate to Indic, push downstream."""
        try:
            while not self._cancel.is_set():
                try:
                    event = await asyncio.wait_for(
                        self._response_queue.get(), timeout=0.3
                    )
                except asyncio.TimeoutError:
                    continue

                t0 = time.monotonic()
                indic = await self._trans.eng_to_indic(
                    event.response_text, self._flores_src
                )
                event.indic_text = indic
                event.stage_timing["trans2_done"] = time.monotonic() - t0
                await self._indic_queue.put(event)

        except asyncio.CancelledError:
            pass

    # ── Stage 5: TTS ────────────────────────────────────────────────────────

    async def _tts_worker(self, client_ws):
        """
        Take Indic text, generate PCM locally using LocalTTS, push audio chunks downstream.
        This replaces the previous vLLM-Omni remote call.
        """
        try:
            while not self._cancel.is_set():
                try:
                    event = await asyncio.wait_for(
                        self._indic_queue.get(), timeout=0.3
                    )
                except asyncio.TimeoutError:
                    continue

                t0 = time.monotonic()
                text = event.indic_text or event.response_text or event.source_text

                # Notify client that TTS for this sentence has started
                await client_ws.send_json({
                    "type": "tts_start",
                    "seq": event.seq,
                    "text": text,
                    "lang": self._lang_bcp47,
                })

                # Generate PCM bytes using local TTS
                loop = asyncio.get_event_loop()
                try:
                    pcm_bytes = await loop.run_in_executor(
                        None, self._local_tts.generate, text
                    )
                except Exception as exc:
                    logger.error(
                        "[Pipe:%s] TTS seq=%d generation failed: %s",
                        self._session_id, event.seq, exc,
                    )
                    continue

                tts_elapsed = time.monotonic() - t0
                event.stage_timing["tts_done"] = tts_elapsed
                logger.info(
                    "[Pipe:%s] TTS seq=%d generated %d bytes in %.2fs",
                    self._session_id, event.seq, len(pcm_bytes), tts_elapsed,
                )

                # Split into chunks (e.g., 20ms chunks) for streaming
                chunk_size = int(TTS_SAMPLE_RATE * 0.02 * 2)  # 16-bit PCM => 2 bytes per sample
                for i in range(0, len(pcm_bytes), chunk_size):
                    chunk = pcm_bytes[i:i+chunk_size]
                    await self._tts_queue.put(TTSChunk(
                        seq=event.seq,
                        audio_bytes=chunk,
                        sample_rate=TTS_SAMPLE_RATE,
                        is_last=False,
                    ))

                # Run PantoMatrix in executor (non-blocking)
                if pcm_bytes and not self._cancel.is_set():
                    try:
                        matrix = await loop.run_in_executor(
                            None, extract_blendshapes, pcm_bytes, TTS_SAMPLE_RATE
                        )
                        if matrix:
                            await client_ws.send_json({
                                "type": "blendshape_matrix",
                                "seq": event.seq,
                                "matrix": matrix,
                            })
                            event.stage_timing["panto_done"] = time.monotonic() - t0
                            logger.info(
                                "[Pipe:%s] PantoMatrix seq=%d %d frames",
                                self._session_id, event.seq, len(matrix),
                            )
                    except Exception as pex:
                        logger.warning(
                            "[Pipe:%s] PantoMatrix seq=%d failed: %s",
                            self._session_id, event.seq, pex,
                        )

                # Signal end of this sentence's audio
                await self._tts_queue.put(TTSChunk(
                    seq=event.seq,
                    audio_bytes=b"",
                    sample_rate=TTS_SAMPLE_RATE,
                    is_last=True,
                ))

        except asyncio.CancelledError:
            pass


    # ── Audio sender ─────────────────────────────────────────────────────────

    async def _audio_sender(self, client_ws):
        """
        Pull audio chunks from the TTS queue and send them to the client
        with appropriate cross-fade metadata.
        """
        sent_first = False
        try:
            while not self._cancel.is_set():
                try:
                    chunk = await asyncio.wait_for(
                        self._tts_queue.get(), timeout=0.3
                    )
                except asyncio.TimeoutError:
                    continue

                if chunk.is_last:
                    # Signal end-of-sentence
                    try:
                        await client_ws.send_json({
                            "type": "tts_end",
                            "seq": chunk.seq,
                        })
                    except Exception:
                        pass
                else:
                    # Cross-fade hint: first chunk of each sentence
                    if not sent_first:
                        sent_first = True
                    try:
                        # Send audio metadata + binary
                        await client_ws.send_json({
                            "type": "audio_chunk",
                            "seq": chunk.seq,
                            "sample_rate": chunk.sample_rate,
                            "byte_length": len(chunk.audio_bytes),
                        })
                        await client_ws.send_bytes(chunk.audio_bytes)
                    except Exception:
                        pass

        except asyncio.CancelledError:
            pass

    # ── Monitor / keepalive ──────────────────────────────────────────────────

    async def _monitor_worker(self, client_ws):
        """Periodically report pipeline health to the client."""
        try:
            while not self._cancel.is_set():
                await asyncio.sleep(2.0)
                try:
                    await client_ws.send_json({
                        "type": "pipeline_status",
                        "session_id": self._session_id,
                        "seq": self._sentence_seq,
                        "lang": self._lang_bcp47,
                    })
                except Exception:
                    break
        except asyncio.CancelledError:
            pass
