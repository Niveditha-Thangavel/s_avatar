"""
main.py — S2S Voice Avatar Server (port 8765)

Pipeline Orchestrator:
  Client audio → Vexyl STT → IndicTrans2 (→EN) → LLM → IndicTrans2 (→Indic) → OmniVoice TTS → Client audio

Endpoints:
  GET  /health              — service health + model readiness
  WS   /ws/s2s              — full S2S pipeline (audio in → audio out)
  POST /chat                — typed text → direct TTS (for debugging)
"""

import sys

# Polyfill sys.get/set_int_max_str_digits if missing (e.g. in some Python 3.11 builds)
# to prevent torch._dynamo import failures during startup.
# Signatures must match the real stdlib functions exactly so that
# torch._dynamo's @substitute_in_graph decorator doesn't raise TypeError.
if not hasattr(sys, "get_int_max_str_digits"):
    def _get_int_max_str_digits() -> int:
        return 4300
    sys.get_int_max_str_digits = _get_int_max_str_digits

if not hasattr(sys, "set_int_max_str_digits"):
    def _set_int_max_str_digits(maxdigits: int) -> None:
        pass
    sys.set_int_max_str_digits = _set_int_max_str_digits

import json
import io
import re
import uuid
import logging
import os
import time
import warnings
from contextlib import asynccontextmanager
import numpy as np
import soundfile as sf

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Response, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# In-memory audio store: { id: bytes }
_audio_store: dict = {}
_AUDIO_BASE_URL = "/api/v1/audio"

os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
warnings.filterwarnings("ignore", message=".*clean_up_tokenization.*")

from server.pipeline_orchestrator import (
    IndicTrans2Engine,
    PipelineOrchestrator,
    VEXYL_STT_URL,
    INDIC_TRANS2_EN_INDIC,
    INDIC_TRANS2_INDIC_EN,
    TRANSLATION_DEVICE,
    _to_flores,
)
from server.local_tts import LocalTTS
from server.pantomatrix import extract_blendshapes
import asyncio

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
)
logger = logging.getLogger(__name__)

# ── Global engine instances ──────────────────────────────────────────────────
_trans_engine: IndicTrans2Engine = None
_tts_engine: LocalTTS = None


# ── Startup / shutdown ───────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    global _trans_engine, _tts_engine
    logger.info("[Server] Initialising IndicTrans2 engine …")
    _trans_engine = IndicTrans2Engine(
        en_indic_path=INDIC_TRANS2_EN_INDIC,
        indic_en_path=INDIC_TRANS2_INDIC_EN,
        device=TRANSLATION_DEVICE,
    )
    await _trans_engine.load()
    logger.info("[Server] ✅ IndicTrans2 ready")

    logger.info("[Server] Initialising LocalTTS engine (k2-fsa/OmniVoice) …")
    try:
        _tts_engine = LocalTTS()
        if _tts_engine.is_ready:
            logger.info("[Server] ✅ LocalTTS ready")
        else:
            logger.warning("[Server] ⚠️  LocalTTS loaded but model is NOT ready — will return silent audio")
    except Exception as exc:
        logger.error("[Server] ❌ LocalTTS init failed — pipeline will use silent audio: %s", exc)
        _tts_engine = LocalTTS.__new__(LocalTTS)
        _tts_engine.sample_rate = 24000
        _tts_engine._ready = False
        _tts_engine.model = None

    yield
    logger.info("[Server] Shutdown")


app = FastAPI(title="S2S Voice Avatar Server", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── In-memory audio serving ───────────────────────────────────────────────────

@app.get("/api/v1/audio/{audio_id}")
async def serve_audio(audio_id: str):
    data = _audio_store.get(audio_id)
    if data is None:
        raise HTTPException(404, "Audio not found")
    return Response(content=data, media_type="audio/wav")


# ── Health ───────────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {
        "status": "ok",
        "timestamp": time.time(),
        "version": "2.0-s2s",
        "services": {
            "translator": "loaded" if (_trans_engine and _trans_engine._loaded) else "loading",
            "vexyl_stt": VEXYL_STT_URL,
            "tts": ("loaded (local omnivoice)" if (_tts_engine and _tts_engine.is_ready)
                    else "degraded (silent fallback)"),
        },
        "models": {
            "indic_en": INDIC_TRANS2_INDIC_EN,
            "en_indic": INDIC_TRANS2_EN_INDIC,
            "tts": "k2-fsa/OmniVoice",
        },
    }


# ── S2S Pipeline (WebSocket) ────────────────────────────────────────────────

@app.websocket("/ws/s2s")
async def ws_s2s(websocket: WebSocket):
    await websocket.accept()
    addr = str(websocket.client)
    logger.info("[S2S-WS] Connected: %s", addr)

    session_id = f"s2s_{int(time.time())}_{addr.replace('.','_')}"
    lang = "hi-IN"  # default, overridden by client

    try:
        # Wait for the first control message
        raw = await websocket.receive_text()
        msg = json.loads(raw)
        if msg.get("type") == "start":
            lang = msg.get("lang", "hi-IN")
            session_id = msg.get("session_id", session_id)
        else:
            await websocket.send_json({
                "type": "error",
                "message": "Expected {type:'start', lang:'...'}",
            })
            return

        logger.info(
            "[S2S-WS] Starting session %s lang=%s", session_id, lang
        )

        orchestrator = PipelineOrchestrator(
            trans_engine=_trans_engine,
            tts_engine=_tts_engine,
        )
        await orchestrator.run(websocket, session_id, lang)

    except WebSocketDisconnect:
        logger.info("[S2S-WS] Disconnected: %s", addr)
    except json.JSONDecodeError:
        await websocket.send_json({
            "type": "error",
            "message": "Invalid JSON in start message",
        })
    except Exception as exc:
        logger.exception("[S2S-WS] Error: %s", exc)
        try:
            await websocket.send_json({
                "type": "error",
                "message": str(exc),
            })
        except Exception:
            pass
    finally:
        logger.info("[S2S-WS] Session ended: %s", addr)


# ── Debug / text endpoint ────────────────────────────────────────────────────


class ChatRequest(BaseModel):
    text: str
    lang: str = "en"


@app.post("/chat")
async def chat_endpoint(req: ChatRequest):
    """
    Simple text → translation debug endpoint.
    Translates English input to Indic language.
    """
    if not req.text.strip():
        return {"error": "Empty text"}

    # Translate from English to target language
    flores_tgt = _to_flores(req.lang)
    translated = await _trans_engine.eng_to_indic(req.text, flores_tgt)

    return {
        "original": req.text,
        "translated": translated,
        "lang": req.lang,
        "flores_tgt": flores_tgt,
    }


# ── Speak with Emotion Endpoint and Helpers ────────────────────────────────────

_EMOTION_BLENDSHAPES = {
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
    """Parse inline OmniVoice emotion tokens from text.
    Returns (original_text, list_of_inline_token_names)."""
    tokens  = _INLINE_PATTERN.findall(text)
    inlines = [t.strip().lower() for t in tokens if t.strip().lower() in _INLINE_TOKEN_CLAMPS]
    return text, inlines


def _sanitize_for_omni(text: str) -> str:
    """Strip whitespace around bracketed tokens for OmniVoice tokenizer."""
    result = re.sub(r'\s*(\[.*?\])\s*', r'\1', text)
    result = re.sub(r'\s+', ' ', result)
    return result.strip()


def _apply_emotion_to_matrix(matrix: list, llm_emotion: str, inline_tokens: list) -> list:
    """Bake LLM emotion AND inline token shapes into every frame of the matrix."""
    if not matrix:
        return matrix
        
    # Pass 1: blend persistent emotion expression into every frame
    emotion_shapes = _EMOTION_BLENDSHAPES.get(llm_emotion, {})
    if emotion_shapes:
        for frame in matrix:
            bs = frame["blendshapes"]
            for shape, target in emotion_shapes.items():
                current = bs.get(shape, 0.0)
                # Keep target format in list of dict objects: {time, blendshapes}
                bs[shape] = min(1.0, max(current, current + (target - current) * 0.6))

    # Pass 2: floor-clamp inline token shapes
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


class SpeakRequest(BaseModel):
    text: str = ""
    lang: str = "en"
    speed: float = 1.0


@app.post("/speak/{emotion}")
async def speak_with_emotion(emotion: str, req: SpeakRequest, request: Request):
    """
    TTS synthesis with forced emotion.
    Appends the emotion bracket (e.g. [happy]) to the synthesized text for the TTS engine.
    """
    if not req.text.strip():
        raise HTTPException(400, "Empty text")

    emotion = emotion.lower()
    if emotion not in _EMOTION_BLENDSHAPES:
        raise HTTPException(422, f"Invalid emotion. Valid: {list(_EMOTION_BLENDSHAPES.keys())}")

    # 1. Translate to target language if needed
    tts_input = req.text.strip()
    if req.lang != "en" and req.lang != "eng_Latn":
        try:
            flores_tgt = _to_flores(req.lang)
            translated = await _trans_engine.eng_to_indic(tts_input, flores_tgt)
            tts_input = translated or tts_input
        except Exception as exc:
            logger.warning("[Speak] Translation to %s failed: %s", req.lang, exc)

    # 2. Automatically prepend emotion bracket style to text for TTS engine
    if emotion != "neutral":
        # Keep it clean: check if there's already a bracket style prepended
        if not tts_input.startswith(f"[{emotion}]"):
            tts_input = f"[{emotion}] {tts_input}"

    # 3. Sanitize for OmniVoice
    tts_input = _sanitize_for_omni(tts_input)

    # 4. Local OmniVoice TTS generation (synchronous call inside thread pool)
    try:
        loop = asyncio.get_event_loop()
        audio_bytes = await loop.run_in_executor(None, _tts_engine.generate, tts_input)
    except Exception as exc:
        raise HTTPException(500, f"TTS failed: {exc}")

    if not audio_bytes:
        raise HTTPException(500, "TTS produced no audio")

    tts_sr = 24000
    audio_f32 = np.frombuffer(audio_bytes, dtype=np.float32)

    # 5. Write WAV to bytes
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

    logger.info("[Speak/%s] Generated %.2fs WAV audio -> %s", emotion, len(audio_f32) / tts_sr, audio_id)

    # 6. PantoMatrix blendshapes extraction (in thread)
    try:
        matrix = await loop.run_in_executor(
            None, extract_blendshapes, audio_bytes, tts_sr
        )
    except Exception as exc:
        logger.error("[Speak] PantoMatrix failed: %s", exc)
        matrix = []

    # 7. Apply emotion blendshapes to matrix
    _, inline_tokens = _parse_emotion_tokens(tts_input)
    matrix = _apply_emotion_to_matrix(matrix, emotion, inline_tokens)

    # 8. Clean up old entries from audio store
    if len(_audio_store) > 100:
        oldest = sorted(_audio_store.keys())[:50]
        for k in oldest:
            _audio_store.pop(k, None)

    return {
        "audio_url": audio_url,
        "animation_matrix": matrix,
        "emotion": emotion,
    }
