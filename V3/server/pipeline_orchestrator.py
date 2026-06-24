"""
pipeline_orchestrator.py — Low-latency S2S pipeline orchestrator

Orchestrates 5 services:
  1. Vexyl STT (WebSocket streaming ASR)
  2. IndicTrans2 → English (CTranslate2)
  3. External LLM (OpenAI-compatible /v1/chat/completions) [currently: static mock]
  4. IndicTrans2 → Indic (CTranslate2)
  5. OmniVoice TTS (local k2-fsa/OmniVoice inference)

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
    os.path.join(os.path.expanduser("~"), ".cache", "ctranslate2", "ct2-rotary-indictrans2-en-indic-dist-200M"),
)
INDIC_TRANS2_INDIC_EN = os.environ.get(
    "INDIC_TRANS2_INDIC_EN",
    os.path.join(os.path.expanduser("~"), ".cache", "ctranslate2", "ct2-rotary-indictrans2-indic-en-dist-200M"),
)

TRANSLATION_DEVICE = os.environ.get("TRANSLATION_DEVICE", "auto")

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
    response_text: str = ""    # static English response
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

# Templates for the "(You said: {text})" suffix in target languages
_YOU_SAID_TEMPLATES = {
    "hi-IN": "(आपने कहा: {text})",
    "ta-IN": "(நீங்கள் சொன்னீர்கள்: {text})",
    "te-IN": "(మీరు చెప్పారు: {text})",
    "ml-IN": "(നിങ്ങൾ പറഞ്ഞു: {text})",
    "kn-IN": "(ನೀವು ಹೇಳಿದ್ದೀರಿ: {text})",
    "mr-IN": "(आपण म्हणालात: {text})",
    "gu-IN": "(તમે કહ્યું: {text})",
    "bn-IN": "(আপনি বলেছেন: {text})",
    "pa-IN": "(ਤੁਸੀਂ ਕਿਹਾ: {text})",
    "or-IN": "(ଆପଣ କହିଲେ: {text})",
    "as-IN": "(আপুনি কৈছিল: {text})",
    "ur-IN": "(آپ نے کہا: {text})",
}

# Default to Hindi if unknown
_DEFAULT_FLORES_SRC = "hin_Deva"


def _to_flores(bcp47: str) -> str:
    return _LANG_TO_FLORES.get(bcp47, _DEFAULT_FLORES_SRC)


def _from_flores(flores: str) -> str:
    return _FLORES_TO_LANG.get(flores, "hi-IN")


# ── IndicTrans2 Engine (CTranslate2) ─────────────────────────────────────────

class IndicTrans2Engine:
    """
    Wraps two specialized distilled IndicTrans2 CTranslate2 models
    for fast commercial-friendly translation (English <-> Indic).
    """

    def __init__(
        self,
        en_indic_path: str = INDIC_TRANS2_EN_INDIC,
        indic_en_path: str = INDIC_TRANS2_INDIC_EN,
        device: str = "auto",
    ):
        self._en_indic_path = en_indic_path
        self._indic_en_path = indic_en_path
        if device == "auto":
            import torch
            self._device = "cuda" if torch.cuda.is_available() else "cpu"
        else:
            self._device = device
        
        # Translators and Tokenizers
        self._en_indic_translator: Optional["ctranslate2.Translator"] = None
        self._en_indic_sp_src: Optional["sentencepiece.SentencePieceProcessor"] = None
        self._en_indic_sp_tgt: Optional["sentencepiece.SentencePieceProcessor"] = None

        self._indic_en_translator: Optional["ctranslate2.Translator"] = None
        self._indic_en_sp_src: Optional["sentencepiece.SentencePieceProcessor"] = None
        self._indic_en_sp_tgt: Optional["sentencepiece.SentencePieceProcessor"] = None

        self._lock = asyncio.Lock()
        self._loaded = False

    async def load(self):
        """Load both IndicTrans2 models and tokenizers (idempotent, thread-safe)."""
        if self._loaded:
            return
        async with self._lock:
            if self._loaded:
                return
            logger.info(
                "[IndicTrans2] Loading models (en-indic from %s, indic-en from %s) on %s",
                self._en_indic_path,
                self._indic_en_path,
                self._device
            )
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, self._load_sync)
            self._loaded = True
            logger.info("[IndicTrans2] Models ready")

    def _load_sync(self):
        import ctranslate2
        import sentencepiece as spm

        # Load EN-INDIC
        self._en_indic_sp_src = spm.SentencePieceProcessor()
        self._en_indic_sp_src.load(os.path.join(self._en_indic_path, "vocab", "model.SRC"))
        self._en_indic_sp_tgt = spm.SentencePieceProcessor()
        self._en_indic_sp_tgt.load(os.path.join(self._en_indic_path, "vocab", "model.TGT"))
        self._en_indic_translator = ctranslate2.Translator(
            self._en_indic_path, device=self._device
        )

        # Load INDIC-EN
        self._indic_en_sp_src = spm.SentencePieceProcessor()
        self._indic_en_sp_src.load(os.path.join(self._indic_en_path, "vocab", "model.SRC"))
        self._indic_en_sp_tgt = spm.SentencePieceProcessor()
        self._indic_en_sp_tgt.load(os.path.join(self._indic_en_path, "vocab", "model.TGT"))
        self._indic_en_translator = ctranslate2.Translator(
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
            None, self._translate_sync, text, src_lang, "eng_Latn", False
        )

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
            None, self._translate_sync, text, "eng_Latn", tgt_lang, True
        )

    def _translate_sync(self, text: str, src_lang: str, tgt_lang: str, is_en_to_indic: bool) -> str:
        if is_en_to_indic:
            sp = self._en_indic_sp_src
            sp_tgt = self._en_indic_sp_tgt
            translator = self._en_indic_translator
        else:
            sp = self._indic_en_sp_src
            sp_tgt = self._indic_en_sp_tgt
            translator = self._indic_en_translator

        # Prepend source language token and target language token, append EOS token
        subwords = sp.encode(text, out_type=str)
        source_tokens = [src_lang, tgt_lang] + subwords + ["</s>"]

        # Translate without target prefix for IndicTrans2
        results = translator.translate_batch(
            [source_tokens],
            beam_size=4,
            max_batch_size=1,
        )
        output_tokens = results[0].hypotheses[0]

        # Decode using target tokenizer
        decoded = sp_tgt.decode(output_tokens)
        return decoded.strip()


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
        trans_engine: Optional[IndicTrans2Engine] = None,
        tts_engine: Optional[LocalTTS] = None,
    ):
        self._vexyl_url = vexyl_url
        self._vexyl_api_key = vexyl_api_key
        self._local_tts = tts_engine or LocalTTS()
        self._trans = trans_engine or IndicTrans2Engine()

        # Pipeline queues
        self._audio_out: asyncio.Queue = asyncio.Queue()     # bytes → client
        self._stt_out: asyncio.Queue = asyncio.Queue()       # str ← Vexyl
        self._sentences: asyncio.Queue = asyncio.Queue()     # SentenceEvent
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
            asyncio.create_task(self._pipeline_worker()),
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
                        await vexyl_ws.send(raw["bytes"])
                    elif raw.get("text"):
                        try:
                            msg = json.loads(raw["text"])
                        except json.JSONDecodeError:
                            continue
                        if msg.get("type") == "stop":
                            await vexyl_ws.send(json.dumps({"type": "stop"}))
                            break
                        elif msg.get("type") == "cancel":
                            await vexyl_ws.send(json.dumps({"type": "cancel"}))
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

    # ── Stage 2: Pipeline Worker (Static Translation Response) ──────────────

    async def _pipeline_worker(self):
        """Take input sentences, retrieve static response, translate directly to Indic, and push to TTS."""
        try:
            while not self._cancel.is_set():
                try:
                    event = await asyncio.wait_for(
                        self._sentences.get(), timeout=0.3
                    )
                except asyncio.TimeoutError:
                    continue

                try:
                    t0 = time.monotonic()
                    # 1. Select the next static response in English
                    resp_text = next(_static_response_cycle)
                    event.response_text = resp_text

                    # 2. Translate English response -> Indic response directly
                    indic_response = await self._trans.eng_to_indic(
                        resp_text, self._flores_src
                    )

                    # 3. Format suffix to show user's input
                    suffix = _YOU_SAID_TEMPLATES.get(
                        self._lang_bcp47, "(You said: {text})"
                    ).format(text=event.source_text)
                    event.indic_text = f"{indic_response} {suffix}"
                    event.stage_timing["pipeline_done"] = time.monotonic() - t0
                except Exception as ex:
                    logger.error("[Pipeline] Error in translation/processing: %s", ex, exc_info=True)
                    event.indic_text = resp_text

                # 4. Push directly to TTS queue
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
                chunk_size = int(TTS_SAMPLE_RATE * 0.02 * 4)  # 32-bit float PCM => 4 bytes per sample
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
