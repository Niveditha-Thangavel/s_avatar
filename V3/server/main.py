"""
main.py — Voice Avatar Server  (port 8765)

Pipeline:
  🎤 audio
    → Whisper large-v3-turbo   STT + LID
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

# ── CUDA global tuning (no-op on CPU/MPS) ────────────────────────────────────
import torch
if torch.cuda.is_available():
    torch.backends.cudnn.benchmark      = True
    torch.backends.cuda.matmul.allow_tf32 = True   # Ampere+: free 10-20% speedup
    torch.backends.cudnn.allow_tf32       = True

import soundfile as sf
from fastapi import FastAPI, File, HTTPException, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from pydantic import BaseModel

from server.tts_engine         import get_model    as load_tts,     synthesize_stream
from server.stt_engine         import load_stt_models,              transcribe
from server.llm_engine         import load_model   as load_llm,     generate_response
from server.translation_engine import load_translation_model,       translate_from_english, translate_to_english
from server.pantomatrix        import extract_blendshapes

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
    # Sequential loading — asyncio.gather() causes a double-free / MPS allocator
    # race on macOS when multiple models compete for the MPS memory pool at startup.
    await load_tts()
    await load_stt_models()
    await load_llm()
    await load_translation_model()
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
    from server.tts_engine         import _model          as tts_m
    from server.stt_engine         import _model          as stt_m
    from server.llm_engine         import _model          as llm_m
    from server.translation_engine import _model          as trans_m
    return {
        "status":    "ok",
        "timestamp": time.time(),
        "omnivoice":  "loaded" if tts_m   else "loading",
        "whisper":    "loaded" if stt_m   else "loading",
        "granite":    "loaded" if llm_m   else "loading",
        "small100":   "loaded" if trans_m else "loading",
    }


# ── /chat — typed text input ──────────────────────────────────────────────────

class ChatRequest(BaseModel):
    text:       str
    session_id: str   = "default"
    lang:       str   = "en"


@app.post("/chat")
async def chat_endpoint(req: ChatRequest):
    if not req.text.strip():
        return {"reply": "", "native_text": "", "emotion": "happy"}

    payload = await generate_response(req.text.strip(), session_id=req.session_id)
    eng     = payload.get("response", "")
    emotion = payload.get("emotion", "happy")
    intent  = payload.get("intent",  "unknown")

    native, _ = await translate_from_english(eng, req.lang)

    return {
        "reply":       native,
        "native_text": native,
        "emotion":     emotion,
        "intent":      intent,
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
                instruct = msg.get("instruct") or None
                speed    = float(msg.get("speed", 1.0))
                steps    = int(msg.get("numStep", 16))
                emotion  = msg.get("emotion", "happy")   # passed by frontend

                if not text:
                    await _j(websocket, {"type": "error", "message": "Empty text"})
                    continue

                await _j(websocket, {"type": "status", "data": "generating"})
                tts_audio_chunks = []
                try:
                    async for chunk in synthesize_stream(
                        text=text,
                        instruct=instruct, speed=speed, num_step=steps,
                    ):
                        await _j(websocket, {
                            "type":       "chunk",
                            "text":       chunk["text"],
                            "sampleRate": chunk["sample_rate"],
                            "byteLength": len(chunk["audio"]),
                        })
                        await websocket.send_bytes(chunk["audio"])
                        tts_audio_chunks.append(chunk["audio"])

                    # Compute animation matrix from accumulated audio (in thread)
                    animation_matrix = []
                    if tts_audio_chunks:
                        tts_sr     = 24000
                        full_audio = b"".join(tts_audio_chunks)
                        try:
                            loop = asyncio.get_event_loop()
                            animation_matrix = await loop.run_in_executor(
                                None, extract_blendshapes, full_audio, tts_sr
                            )
                            # Parse inline tokens from the text and apply emotion
                            _, inline_tokens = _parse_emotion_tokens(text)
                            animation_matrix = _apply_emotion_to_matrix(
                                animation_matrix, emotion, inline_tokens
                            )
                        except Exception as exc:
                            logger.warning("[TTS-WS] PantoMatrix/emotion failed: %s", exc)

                    await _j(websocket, {
                        "type":             "status",
                        "data":             "complete",
                        "animation_matrix": animation_matrix,
                        "emotion":          emotion,
                    })
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
                        original, user_lang, user_emotion = await transcribe(audio)
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

                    logger.info("[STT-WS] lang=%s emotion=%s | original=%s | english=%s",
                                user_lang, user_emotion, original[:60], english[:60])

                    await _j(websocket, {"type": "status", "data": "thinking"})
                    try:
                        payload = await generate_response(english, session_id=session_id, user_emotion=user_emotion)
                    except Exception as exc:
                        logger.exception("[STT-WS] LLM failed: %s", exc)
                        await _j(websocket, {"type": "error", "message": f"LLM error: {exc}"})
                        continue

                    eng_response = payload.get("response", "")
                    emotion      = payload.get("emotion", "happy")
                    intent       = payload.get("intent",  "unknown")

                    if not eng_response.strip():
                        eng_response = "I'm sorry, I didn't understand that."

                    try:
                        native, _ = await translate_from_english(eng_response, user_lang)
                    except Exception as exc:
                        logger.warning("[STT-WS] Response translation failed (%s) — using English", exc)
                        native = eng_response

                    if not native.strip():
                        logger.warning("[STT-WS] Response translation empty for %s, using English", user_lang)
                        native = eng_response

                    logger.info("[STT-WS] lang=%s intent=%s emotion=%s reply=%s",
                                user_lang, intent, emotion, native[:80])

                    await _j(websocket, {
                        "type":        "reply",
                        "text":        native,
                        "native_text": native,
                        "emotion":     emotion,
                        "intent":      intent,
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

_EMOTION_BLENDSHAPES = {
    # Each emotion maps to a dict of {shape: target_weight}.
    # These are BLENDED into every animation frame at a fixed strength
    # so the avatar holds the emotional expression throughout speech.
    # Weights are additive on top of the phoneme-driven shapes.
    "neutral": {},
    "happy": {
        "mouthSmileLeft":   0.50,
        "mouthSmileRight":  0.50,
        "cheekSquintLeft":  0.30,
        "cheekSquintRight": 0.30,
        "browOuterUpLeft":  0.20,
        "browOuterUpRight": 0.20,
    },
    "sad": {
        "mouthFrownLeft":   0.50,
        "mouthFrownRight":  0.50,
        "browInnerUp":      0.45,
        "browDownLeft":     0.15,
        "browDownRight":    0.15,
    },
    "angry": {
        "browDownLeft":     0.65,
        "browDownRight":    0.65,
        "eyeSquintLeft":    0.40,
        "eyeSquintRight":   0.40,
        "mouthFrownLeft":   0.30,
        "mouthFrownRight":  0.30,
        "noseSneerLeft":    0.25,
        "noseSneerRight":   0.25,
    },
    "surprised": {
        "eyeWideLeft":      0.60,
        "eyeWideRight":     0.60,
        "browInnerUp":      0.55,
        "browOuterUpLeft":  0.45,
        "browOuterUpRight": 0.45,
        "mouthShrugUpper":  0.20,
    },
    "fearful": {
        "eyeWideLeft":      0.45,
        "eyeWideRight":     0.45,
        "browInnerUp":      0.50,
        "browOuterUpLeft":  0.30,
        "browOuterUpRight": 0.30,
        "mouthFrownLeft":   0.25,
        "mouthFrownRight":  0.25,
    },
}

# Inline OmniVoice emotion tokens — floor-clamp specific shapes
# (these fire only when the LLM embeds [laughter] etc. in the response text)
_INLINE_TOKEN_CLAMPS = {
    "laughter": {
        "mouthSmileLeft":   0.80,
        "mouthSmileRight":  0.80,
        "cheekSquintLeft":  0.55,
        "cheekSquintRight": 0.55,
        "eyeSquintLeft":    0.50,
        "eyeSquintRight":   0.50,
    },
    "sigh": {
        "jawOpen":    0.30,
        "browInnerUp": 0.20,
    },
    "surprise-oh": {
        "jawOpen":         0.60,
        "browInnerUp":     0.50,
        "browOuterUpLeft":  0.35,
        "browOuterUpRight": 0.35,
        "eyeWideLeft":     0.55,
        "eyeWideRight":    0.55,
    },
    "dissatisfaction-hnn": {
        "mouthFrownLeft":  0.45,
        "mouthFrownRight": 0.45,
        "browDownLeft":    0.35,
        "browDownRight":   0.35,
    },
}

_INLINE_PATTERN = re.compile(r'\[(.*?)\]', re.IGNORECASE)


def _parse_emotion_tokens(text: str) -> tuple:
    """
    Parse inline OmniVoice emotion tokens from LLM response text.
    Returns (original_text_with_brackets, list_of_inline_token_names).
    Tokens stay in the text so OmniVoice can render them as vocal effects.
    """
    tokens  = _INLINE_PATTERN.findall(text)
    inlines = [t.strip().lower() for t in tokens if t.strip().lower() in _INLINE_TOKEN_CLAMPS]
    return text, inlines


def _sanitize_for_omni(text: str) -> str:
    """Strip whitespace around bracketed tokens for OmniVoice tokenizer."""
    result = re.sub(r'\s*(\[.*?\])\s*', r'\1', text)
    result = re.sub(r'\s+', ' ', result)
    return result.strip()


def _apply_emotion_to_matrix(matrix: list, llm_emotion: str, inline_tokens: list) -> list:
    """
    Bake LLM emotion AND inline token shapes into every frame of the matrix.

    Two passes:
    1. Blend LLM emotion shapes additively into every frame (persistent expression).
    2. Floor-clamp inline token shapes (fire at their peak moments).
    """
    if not matrix:
        return matrix

    # ── Pass 1: blend persistent emotion expression into every frame ────────
    emotion_shapes = _EMOTION_BLENDSHAPES.get(llm_emotion, {})
    if emotion_shapes:
        for frame in matrix:
            bs = frame["blendshapes"]
            for shape, target in emotion_shapes.items():
                current = bs.get(shape, 0.0)
                # Additive blend: take the max so phoneme shapes aren't suppressed
                bs[shape] = min(1.0, max(current, current + (target - current) * 0.6))

    # ── Pass 2: floor-clamp inline token shapes ──────────────────────────────
    if inline_tokens:
        clamps = {}
        for token in inline_tokens:
            clamps.update(_INLINE_TOKEN_CLAMPS.get(token, {}))
        if clamps:
            for frame in matrix:
                bs = frame["blendshapes"]
                for shape, min_val in clamps.items():
                    bs[shape] = max(bs.get(shape, 0.0), min_val)

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
        original, user_lang, user_emotion = await transcribe(audio_16k)
    except Exception as exc:
        raise HTTPException(500, f"STT failed: {exc}")

    if not original.strip():
        raise HTTPException(400, "No speech detected in audio")

    logger.info("[API] STT: lang=%s emotion=%s transcript=%s", user_lang, user_emotion, original[:80])

    # ── 3. SMaLL-100 → English ──────────────────────────────────────────────
    try:
        english = await translate_to_english(original, user_lang)
    except Exception as exc:
        logger.warning("[API] Translation to EN failed (%s), using original", exc)
        english = original

    if not english.strip():
        english = original

    # ── 4. Granite LLM with emotion tokens + user emotion context ───────────
    try:
        payload = await generate_response(english, session_id=f"api_{uuid.uuid4().hex[:8]}", user_emotion=user_emotion)
    except Exception as exc:
        raise HTTPException(500, f"LLM failed: {exc}")

    eng_response = payload.get("response", "")
    llm_emotion  = payload.get("emotion", "happy")
    if not eng_response.strip():
        eng_response = "I'm sorry, I didn't understand that."

    logger.info("[API] LLM response: %s", eng_response[:120])

    # ── 5. Parse emotion tokens from LLM response ───────────────────────────
    eng_with_tokens, inline_tokens = _parse_emotion_tokens(eng_response)
    if inline_tokens:
        logger.info("[API] Inline emotion tokens: %s", inline_tokens)

    # ── 6. SMaLL-100 → native (preserving emotion tokens) ───────────────────
    if user_lang == "en":
        native = eng_with_tokens
    else:
        try:
            native, _ = await translate_from_english(eng_with_tokens, user_lang)
        except Exception as exc:
            logger.warning("[API] Translation failed (%s), using English", exc)
            native = eng_with_tokens

    if not native.strip():
        native = eng_with_tokens

    # ── Sanitize for OmniVoice: strip whitespace around bracketed tokens ───
    tts_input = _sanitize_for_omni(native)

    logger.info("[API] TTS input: %s", tts_input[:80])

    # ── 7. OmniVoice TTS → audio bytes ──────────────────────────────────────
    tts_audio_chunks = []
    try:
        async for chunk in synthesize_stream(text=tts_input):
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
    sf.write(wav_buffer, tts_audio_f32, tts_sr, format="WAV", subtype="FLOAT")
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

    # ── 8. PantoMatrix blendshape extraction (in thread — pure NumPy) ──────────
    loop = asyncio.get_event_loop()
    try:
        matrix = await loop.run_in_executor(
            None, extract_blendshapes, tts_audio_f32.tobytes(), tts_sr
        )
    except Exception as exc:
        logger.error("[API] PantoMatrix failed: %s", exc)
        matrix = []

    # ── 9. Apply emotion blendshapes to every frame ─────────────────────────
    matrix = _apply_emotion_to_matrix(matrix, llm_emotion, inline_tokens)
    logger.info("[API] emotion=%s inline=%s frames=%d", llm_emotion, inline_tokens, len(matrix))

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
        "emotion": payload.get("emotion", "neutral"),
        "intent":  payload.get("intent",  "unknown"),
    }


# ── /speak/{emotion} — TTS with forced emotion ────────────────────────────────

class SpeakRequest(BaseModel):
    text:  str     = ""
    lang:  str     = "en"
    speed: float   = 1.0


@app.post("/speak/{emotion}")
async def speak_with_emotion(emotion: str, req: SpeakRequest, request: Request):
    """
    Forced-emotion TTS endpoint.

    Emotion is NOT driven by voice prosody (OmniVoice does not support it).
    Instead, it is expressed via ARKit blendshapes in the animation matrix
    using _EMOTION_BLENDSHAPES + _apply_emotion_to_matrix().

    Accepts JSON: { "text": "...", "lang": "en", "speed": 1.0 }
    Returns:      { "audio_url", "animation_matrix", "emotion" }
    """
    t_start = time.perf_counter()

    if not req.text.strip():
        raise HTTPException(400, "Empty text")

    emotion = emotion.lower()
    if emotion not in _EMOTION_BLENDSHAPES:
        raise HTTPException(422, f"Invalid emotion. Valid: {list(_EMOTION_BLENDSHAPES.keys())}")

    # ── 1. Translate to target language if needed ────────────────────────────
    tts_input = req.text.strip()
    if req.lang != "en":
        try:
            translated, _ = await translate_from_english(tts_input, req.lang)
            tts_input = translated or tts_input
        except Exception as exc:
            logger.warning("[Speak] Translation to %s failed: %s", req.lang, exc)

    # ── 2. Sanitize for OmniVoice (strip whitespace around bracketed tokens) ─
    tts_input = _sanitize_for_omni(tts_input)

    # ── 3. OmniVoice TTS → audio bytes ───────────────────────────────────────
    tts_chunks = []
    try:
        async for chunk in synthesize_stream(text=tts_input, speed=req.speed):
            tts_chunks.append(chunk["audio"])
    except Exception as exc:
        raise HTTPException(500, f"TTS failed: {exc}")

    if not tts_chunks:
        raise HTTPException(500, "TTS produced no audio")

    tts_sr = 24000
    audio_f32 = np.frombuffer(b"".join(tts_chunks), dtype=np.float32)

    # ── 4. Write WAV to bytes ───────────────────────────────────────────────
    wav_buf = io.BytesIO()
    sf.write(wav_buf, audio_f32, tts_sr, format="WAV", subtype="FLOAT")
    wav_bytes = wav_buf.getvalue()

    audio_id = uuid.uuid4().hex
    _audio_store[audio_id] = wav_bytes

    scheme = request.url.scheme
    host = request.url.hostname
    port = request.url.port
    if port and port not in (80, 443):
        audio_url = f"{scheme}://{host}:{port}{_AUDIO_BASE_URL}/{audio_id}"
    else:
        audio_url = f"{scheme}://{host}{_AUDIO_BASE_URL}/{audio_id}"

    logger.info("[Speak/%s] %.2fs audio → %s", emotion, len(audio_f32) / tts_sr, audio_id)

    # ── 5. PantoMatrix blendshapes (in thread) ───────────────────────────────
    loop = asyncio.get_event_loop()
    try:
        matrix = await loop.run_in_executor(
            None, extract_blendshapes, audio_f32.tobytes(), tts_sr
        )
    except Exception as exc:
        logger.error("[Speak] PantoMatrix failed: %s", exc)
        matrix = []

    # ── 6. Apply emotion blendshapes to every frame ──────────────────────────
    _, inline_tokens = _parse_emotion_tokens(tts_input)
    matrix = _apply_emotion_to_matrix(matrix, emotion, inline_tokens)

    # ── 7. Cleanup old audio entries ─────────────────────────────────────────
    if len(_audio_store) > 100:
        oldest = sorted(_audio_store.keys())[:50]
        for k in oldest:
            _audio_store.pop(k, None)

    t_total = time.perf_counter() - t_start
    logger.info("[Speak/%s] ✅ Done in %.2fs (frames=%d)", emotion, t_total, len(matrix))

    return {
        "audio_url": audio_url,
        "animation_matrix": matrix,
        "emotion": emotion,
    }


# ── Helpers ───────────────────────────────────────────────────────────────────

async def _j(ws: WebSocket, data: dict):
    await ws.send_text(json.dumps(data))
