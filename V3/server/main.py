"""
main.py — Voice Avatar Server  (port 8765)

Pipeline:
  🎤 audio
    → Whisper v3-Turbo        STT + language detection
    → SMaLL-100               translate native → English
    → Granite 4.0 Nano        LLM: intent, emotion, English response
    → SMaLL-100               translate English → native (with emotion tokens)
    → OmniVoice               TTS: speak the response
    → PantoMatrix             extract 52 ARKit blendshape frames @30fps
    → JSON payload             { audio_url, animation_matrix }

Endpoints:
  GET   /health                — model readiness status
  POST  /chat                  — typed text → LLM → { native_text, romanized_text, emotion }
  WS    /ws/tts                — legacy TTS streaming (text → PCM)
  WS    /ws/stt                — legacy STT pipeline
  POST  /api/v1/chat           — unified: audio → blendshape matrix payload
  GET   /api/v1/audio/{id}     — serve generated audio bytes
"""

import asyncio
import io
import json
import logging
import os
import re
import time
import uuid
import warnings
from contextlib import asynccontextmanager
from typing import Optional

import numpy as np

os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
warnings.filterwarnings("ignore", message=".*clean_up_tokenization.*")
warnings.filterwarnings("ignore", message=".*forced_decoder_ids.*")
warnings.filterwarnings("ignore", message=".*SuppressTokens.*")
warnings.filterwarnings("ignore", message=".*multilingual Whisper.*")

import soundfile as sf
from fastapi import FastAPI, File, HTTPException, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from pydantic import BaseModel

from tts_engine         import get_model    as load_tts,     synthesize_stream
from stt_engine         import load_stt_models,              transcribe
from llm_engine         import load_model   as load_llm,     generate_response
from translation_engine import load_translation_model,       translate_from_english, translate_to_english
from pantomatrix        import extract_blendshapes

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
)
logger = logging.getLogger(__name__)

STT_RATE = 16_000

# In-memory audio store: { id: bytes }
_audio_store: dict = {}
_AUDIO_BASE_URL = "/api/v1/audio"


# ── Audio buffer ──────────────────────────────────────────────────────────────

class AudioBuffer:
    def __init__(self):
        self._chunks: list = []

    def push(self, f32: np.ndarray):
        self._chunks.append(f32)

    def flush(self) -> Optional[np.ndarray]:
        if not self._chunks:
            return None
        audio = np.concatenate(self._chunks).astype(np.float32)
        self._chunks = []
        logger.info("[Buf] %.2fs flushed", len(audio) / STT_RATE)
        return audio

    def reset(self):
        self._chunks = []


# ── Startup ───────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("[Server] Loading: Whisper + Granite + SMaLL-100 + OmniVoice + PantoMatrix …")
    await asyncio.gather(
        load_tts(),
        load_stt_models(),
        load_llm(),
        load_translation_model(),
    )
    logger.info("[Server] ✅ All models ready")
    yield
    logger.info("[Server] Shutdown")


app = FastAPI(title="Voice Avatar Server", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


# ── In-memory audio serving ───────────────────────────────────────────────────

@app.get("/api/v1/audio/{audio_id}")
async def serve_audio(audio_id: str):
    data = _audio_store.get(audio_id)
    if data is None:
        raise HTTPException(404, "Audio not found")
    return Response(content=data, media_type="audio/wav")


# ── Health ────────────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    from tts_engine         import _model          as tts_m
    from stt_engine         import _model          as stt_m
    from llm_engine         import _model          as llm_m
    from translation_engine import _model          as trans_m
    return {
        "status":    "ok",
        "timestamp": time.time(),
        "omnivoice": "loaded" if tts_m   else "loading",
        "whisper":   "loaded" if stt_m   else "loading",
        "granite":   "loaded" if llm_m   else "loading",
        "small100":  "loaded" if trans_m else "loading",
    }


# ── /chat — typed text input ──────────────────────────────────────────────────

class ChatRequest(BaseModel):
    text:       str
    session_id: str   = "default"
    lang:       str   = "en"


@app.post("/chat")
async def chat_endpoint(req: ChatRequest):
    if not req.text.strip():
        return {"reply": "", "native_text": "", "romanized_text": "", "emotion": "neutral"}

    payload = await generate_response(req.text.strip(), session_id=req.session_id)
    eng     = payload.get("response", "")
    emotion = payload.get("emotion", "neutral")
    intent  = payload.get("intent",  "unknown")

    native, roman = await translate_from_english(eng, req.lang)

    return {
        "reply":          native,
        "native_text":    native,
        "romanized_text": roman,
        "emotion":        emotion,
        "intent":         intent,
    }


# ── /ws/tts — text → streamed PCM ────────────────────────────────────────────

@app.websocket("/ws/tts")
async def ws_tts(websocket: WebSocket):
    await websocket.accept()
    logger.info("[TTS-WS] Connected: %s", websocket.client)
    try:
        while True:
            raw = await websocket.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                await _j(websocket, {"type": "error", "message": "Invalid JSON"})
                continue

            if msg.get("type") == "speak":
                text     = msg.get("text", "").strip()
                roman    = msg.get("romanized_text") or msg.get("romanizedText") or None
                instruct = msg.get("instruct") or None
                speed    = float(msg.get("speed", 1.0))
                steps    = int(msg.get("numStep", 16))

                if not text:
                    await _j(websocket, {"type": "error", "message": "Empty text"})
                    continue

                await _j(websocket, {"type": "status", "data": "generating"})
                try:
                    async for chunk in synthesize_stream(
                        text=text, romanized_text=roman,
                        instruct=instruct, speed=speed, num_step=steps,
                    ):
                        await _j(websocket, {
                            "type":          "chunk",
                            "text":          chunk["text"],
                            "romanized_text": chunk.get("romanized_text", ""),
                            "sampleRate":    chunk["sample_rate"],
                            "byteLength":    len(chunk["audio"]),
                        })
                        await websocket.send_bytes(chunk["audio"])
                    await _j(websocket, {"type": "status", "data": "complete"})
                except Exception as exc:
                    logger.exception("[TTS-WS] Synthesis error: %s", exc)
                    await _j(websocket, {"type": "error", "message": str(exc)})

            elif msg.get("type") == "stop":
                await _j(websocket, {"type": "status", "data": "stopped"})

    except WebSocketDisconnect:
        logger.info("[TTS-WS] Disconnected: %s", websocket.client)
    except Exception as exc:
        logger.exception("[TTS-WS] Error: %s", exc)


# ── /ws/stt — full pipeline (legacy) ──────────────────────────────────────────

@app.websocket("/ws/stt")
async def ws_stt(websocket: WebSocket):
    await websocket.accept()
    addr       = str(websocket.client)
    session_id = addr
    logger.info("[STT-WS] Connected: %s", addr)
    buf = AudioBuffer()

    try:
        while True:
            try:
                msg = await websocket.receive()
            except RuntimeError:
                break

            if msg["type"] == "websocket.disconnect":
                logger.info("[STT-WS] Client disconnected: %s", addr)
                break

            if msg["type"] == "websocket.receive" and msg.get("bytes"):
                pcm = np.frombuffer(msg["bytes"], dtype=np.int16).astype(np.float32) / 32768.0
                buf.push(pcm)

            elif msg["type"] == "websocket.receive" and msg.get("text"):
                try:
                    ctrl = json.loads(msg["text"])
                except json.JSONDecodeError:
                    continue

                t = ctrl.get("type")

                if t == "config":
                    await _j(websocket, {"type": "status", "data": "listening"})

                elif t == "stop":
                    audio = buf.flush()
                    if audio is None or len(audio) / STT_RATE < 0.3:
                        await _j(websocket, {"type": "status", "data": "stopped"})
                        continue

                    await _j(websocket, {"type": "status", "data": "transcribing"})
                    try:
                        original, user_lang = await transcribe(audio)
                    except Exception as exc:
                        logger.exception("[STT-WS] Whisper failed: %s", exc)
                        await _j(websocket, {"type": "error", "message": f"STT error: {exc}"})
                        continue

                    if not original.strip():
                        await _j(websocket, {"type": "status", "data": "stopped"})
                        continue

                    await _j(websocket, {"type": "transcript", "text": original})

                    try:
                        english = await translate_to_english(original, user_lang)
                    except Exception as exc:
                        logger.warning("[STT-WS] Translation to EN failed (%s), using original", exc)
                        english = original

                    if not english.strip():
                        logger.warning("[STT-WS] SMaLL-100 returned empty for %s → en, using original", user_lang)
                        english = original

                    logger.info("[STT-WS] lang=%s | original=%s | english=%s",
                                user_lang, original[:60], english[:60])

                    await _j(websocket, {"type": "status", "data": "thinking"})
                    try:
                        payload = await generate_response(english, session_id=session_id)
                    except Exception as exc:
                        logger.exception("[STT-WS] LLM failed: %s", exc)
                        await _j(websocket, {"type": "error", "message": f"LLM error: {exc}"})
                        continue

                    eng_response = payload.get("response", "")
                    emotion      = payload.get("emotion", "neutral")
                    intent       = payload.get("intent",  "unknown")

                    if not eng_response.strip():
                        eng_response = "I'm sorry, I didn't understand that."

                    try:
                        native, roman = await translate_from_english(eng_response, user_lang)
                    except Exception as exc:
                        logger.warning("[STT-WS] Response translation failed (%s) — using English", exc)
                        native, roman = eng_response, eng_response

                    if not native.strip():
                        logger.warning("[STT-WS] Response translation empty for %s, using English", user_lang)
                        native, roman = eng_response, eng_response

                    logger.info("[STT-WS] lang=%s intent=%s emotion=%s reply=%s",
                                user_lang, intent, emotion, native[:80])

                    await _j(websocket, {
                        "type":           "reply",
                        "text":           native,
                        "native_text":    native,
                        "romanized_text": roman,
                        "emotion":        emotion,
                        "intent":         intent,
                    })
                    await _j(websocket, {"type": "status", "data": "stopped"})

                elif t == "cancel":
                    buf.reset()
                    await _j(websocket, {"type": "status", "data": "cancelled"})

    except WebSocketDisconnect:
        logger.info("[STT-WS] Disconnected: %s", addr)
    except Exception as exc:
        logger.exception("[STT-WS] Error: %s", exc)


# ── /api/v1/chat — unified endpoint ──────────────────────────────────────────

_EMOTION_CLAMPS = {
    "laughter": {
        "mouthSmileLeft": 0.8,
        "mouthSmileRight": 0.8,
        "cheekSquintLeft": 0.5,
        "cheekSquintRight": 0.5,
        "eyeSquintLeft": 0.5,
        "eyeSquintRight": 0.5,
    },
    "sigh": {
        "jawOpen": 0.3,
        "browInnerUp": 0.2,
    },
    "surprise-oh": {
        "jawOpen": 0.6,
        "browInnerUp": 0.5,
        "browOuterUpLeft": 0.3,
        "browOuterUpRight": 0.3,
        "eyeWideLeft": 0.5,
        "eyeWideRight": 0.5,
    },
    "dissatisfaction-hnn": {
        "mouthFrownLeft": 0.4,
        "mouthFrownRight": 0.4,
        "browDownLeft": 0.3,
        "browDownRight": 0.3,
    },
}

_EMOTION_PATTERN = re.compile(r'\[(.*?)\]', re.IGNORECASE)


def _parse_emotion_tokens(text: str) -> tuple:
    """Parse emotion tokens from text.

    Returns (original_text_with_brackets, list_of_emotion_names).
    Tokens are NOT stripped — they remain in the text for translation.
    Names are returned case-insensitively for clamping.
    """
    tokens = _EMOTION_PATTERN.findall(text)
    emotions = [t.strip().lower() for t in tokens if t.strip().lower() in _EMOTION_CLAMPS]
    return text, emotions


def _sanitize_for_omni(text: str) -> str:
    """Strip whitespace wrapping bracketed tokens for OmniVoice tokenizer.
    E.g. 'Hello [laughter] text' → 'Hello[laughter]text'
    """
    result = re.sub(r'\s*(\[.*?\])\s*', r'\1', text)
    result = re.sub(r'\s+', ' ', result)
    return result.strip()


def _apply_emotion_clamps(matrix: list, emotions: list) -> list:
    """Apply emotion-based blend shape clamps to the animation matrix."""
    if not emotions:
        return matrix

    clamps = {}
    for em in emotions:
        clamps.update(_EMOTION_CLAMPS.get(em, {}))

    if not clamps:
        return matrix

    for frame in matrix:
        bs = frame["blendshapes"]
        for shape_name, min_val in clamps.items():
            current = bs.get(shape_name, 0.0)
            bs[shape_name] = max(current, min_val)

    return matrix


@app.post("/api/v1/chat")
async def api_v1_chat(
    request: Request,
    file: UploadFile = File(...),
):
    """
    Unified chat endpoint.

    Accepts a WAV audio file upload. Returns:
    {
      "audio_url": "https://host/api/v1/audio/<id>",
      "animation_matrix": [ { "time": 0.00, "blendshapes": { ... } }, ... ]
    }

    Pipeline: audio → Whisper STT → SMaLL-100 (→ EN) → Granite LLM
            → SMaLL-100 (→ native) → OmniVoice TTS → PantoMatrix → JSON
    """
    t_start = time.perf_counter()

    # ── 1. Read & prepare audio ──────────────────────────────────────────────
    audio_bytes = await file.read()
    try:
        raw, orig_sr = sf.read(io.BytesIO(audio_bytes), dtype="float32")
    except Exception as exc:
        raise HTTPException(400, f"Invalid audio file: {exc}")

    if raw.ndim > 1:
        raw = raw.mean(axis=1)

    # Resample to 16kHz for Whisper
    if orig_sr != STT_RATE:
        from scipy import signal
        duration = len(raw) / orig_sr
        num_out = int(round(duration * STT_RATE))
        audio_16k = signal.resample(raw, num_out).astype(np.float32)
    else:
        audio_16k = raw

    logger.info("[API] Received %.2fs audio @ %dHz → resampled to %dHz",
                len(raw) / orig_sr, orig_sr, STT_RATE)

    # ── 2. Whisper STT ──────────────────────────────────────────────────────
    try:
        original, user_lang = await transcribe(audio_16k)
    except Exception as exc:
        raise HTTPException(500, f"STT failed: {exc}")

    if not original.strip():
        raise HTTPException(400, "No speech detected in audio")

    logger.info("[API] STT: lang=%s transcript=%s", user_lang, original[:80])

    # ── 3. SMaLL-100 → English ──────────────────────────────────────────────
    try:
        english = await translate_to_english(original, user_lang)
    except Exception as exc:
        logger.warning("[API] Translation to EN failed (%s), using original", exc)
        english = original

    if not english.strip():
        english = original

    # ── 4. Granite LLM with emotion tokens ──────────────────────────────────
    try:
        payload = await generate_response(english, session_id=f"api_{uuid.uuid4().hex[:8]}")
    except Exception as exc:
        raise HTTPException(500, f"LLM failed: {exc}")

    eng_response = payload.get("response", "")
    if not eng_response.strip():
        eng_response = "I'm sorry, I didn't understand that."

    logger.info("[API] LLM response: %s", eng_response[:120])

    # ── 5. Parse emotion tokens from LLM response ───────────────────────────
    eng_with_tokens, emotions = _parse_emotion_tokens(eng_response)
    if emotions:
        logger.info("[API] Detected emotion tokens: %s", emotions)

    # ── 6. SMaLL-100 → native (preserving emotion tokens) ───────────────────
    if user_lang == "en":
        native = eng_with_tokens
        roman = eng_with_tokens
    else:
        try:
            native, roman = await translate_from_english(eng_with_tokens, user_lang)
        except Exception as exc:
            logger.warning("[API] Translation failed (%s), using English", exc)
            native, roman = eng_with_tokens, eng_with_tokens

    if not native.strip():
        native, roman = eng_with_tokens, eng_with_tokens

    # ── Sanitize for OmniVoice: strip whitespace around bracketed tokens ───
    tts_input = _sanitize_for_omni(native)

    logger.info("[API] TTS input: %s", tts_input[:80])

    # ── 7. OmniVoice TTS → audio bytes ──────────────────────────────────────
    tts_audio_chunks = []
    try:
        async for chunk in synthesize_stream(text=tts_input, romanized_text=roman):
            tts_audio_chunks.append(chunk["audio"])
        tts_sample_rate = 24000
    except Exception as exc:
        raise HTTPException(500, f"TTS failed: {exc}")

    if not tts_audio_chunks:
        raise HTTPException(500, "TTS produced no audio")

    tts_sr = 24000
    tts_audio_f32 = np.frombuffer(b"".join(tts_audio_chunks), dtype=np.float32)

    # Write WAV to bytes in memory
    wav_buffer = io.BytesIO()
    sf.write(wav_buffer, tts_audio_f32, tts_sr, format="WAV")
    wav_bytes = wav_buffer.getvalue()

    # Store in memory
    audio_id = uuid.uuid4().hex
    _audio_store[audio_id] = wav_bytes

    # Build URL (use request to infer host)
    scheme = request.url.scheme
    host = request.url.hostname
    port = request.url.port
    if port and port not in (80, 443):
        audio_url = f"{scheme}://{host}:{port}{_AUDIO_BASE_URL}/{audio_id}"
    else:
        audio_url = f"{scheme}://{host}{_AUDIO_BASE_URL}/{audio_id}"

    logger.info("[API] TTS generated %.2fs audio → stored as %s",
                len(tts_audio_f32) / tts_sr, audio_id)

    # ── 8. PantoMatrix blendshape extraction ────────────────────────────────
    try:
        matrix = extract_blendshapes(tts_audio_f32.tobytes(), tts_sr)
    except Exception as exc:
        logger.error("[API] PantoMatrix failed: %s", exc)
        matrix = []

    # ── 9. Apply emotion clamps ────────────────────────────────────────
    if emotions:
        matrix = _apply_emotion_clamps(matrix, emotions)

    # ── 10. Cleanup: remove old entries from store ───────────────────────────
    if len(_audio_store) > 100:
        oldest = sorted(_audio_store.keys())[:50]
        for k in oldest:
            _audio_store.pop(k, None)

    t_total = time.perf_counter() - t_start
    logger.info("[API] ✅ Pipeline complete in %.2fs (lang=%s, frames=%d)",
                t_total, user_lang, len(matrix))

    return {
        "audio_url": audio_url,
        "animation_matrix": matrix,
    }


# ── Helpers ───────────────────────────────────────────────────────────────────

async def _j(ws: WebSocket, data: dict):
    await ws.send_text(json.dumps(data))
