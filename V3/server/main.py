"""
main.py — Voice Avatar Server  (port 8765)

Pipeline:
  🎤 audio
    → Whisper v3-Turbo   STT + translate to English
    → Granite 4.0 Nano   LLM: intent, emotion, English response
    → SMaLL-100          translate English response → user's language
    → OmniVoice          TTS: speak the response
    → 🔊 avatar

3 models: Whisper (STT+translate) · Granite (LLM) · OmniVoice (TTS)
1 utility: SMaLL-100 (output translation, ~300M, same process)

Endpoints:
  GET  /health   — model readiness status
  POST /chat     — typed text → LLM → { native_text, romanized_text, emotion }
  WS   /ws/tts   — { type:"speak", text } → streamed Float32-LE PCM
  WS   /ws/stt   — streamed Int16 PCM → full pipeline → transcript + reply
"""

import asyncio
import json
import logging
import os
import time
import warnings
from contextlib import asynccontextmanager
from typing import Optional

import numpy as np

os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
warnings.filterwarnings("ignore", message=".*clean_up_tokenization.*")
warnings.filterwarnings("ignore", message=".*forced_decoder_ids.*")
warnings.filterwarnings("ignore", message=".*SuppressTokens.*")
warnings.filterwarnings("ignore", message=".*multilingual Whisper.*")

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from tts_engine         import get_model    as load_tts,     synthesize_stream
from stt_engine         import load_stt_models,              transcribe
from llm_engine         import load_model   as load_llm,     generate_response
from translation_engine import load_translation_model,       translate_from_english, translate_to_english

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
)
logger = logging.getLogger(__name__)

STT_RATE = 16_000


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
    logger.info("[Server] Loading: Whisper + Granite + SMaLL-100 + OmniVoice …")
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
    lang:       str   = "en"   # target language for response (default English)


@app.post("/chat")
async def chat_endpoint(req: ChatRequest):
    if not req.text.strip():
        return {"reply": "", "native_text": "", "romanized_text": "", "emotion": "neutral"}

    # LLM generates English response
    payload = await generate_response(req.text.strip(), session_id=req.session_id)
    eng     = payload.get("response", "")
    emotion = payload.get("emotion", "neutral")
    intent  = payload.get("intent",  "unknown")

    # Translate to target language if not English
    native, roman = await translate_from_english(eng, req.lang)

    return {
        "reply":          native,
        "native_text":    native,
        "romanized_text": roman,   # English for lipsync (Latin chars → visemes)
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


# ── /ws/stt — full pipeline ───────────────────────────────────────────────────

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
                # Starlette raises RuntimeError if the client already
                # disconnected between loop iterations.
                break

            # ASGI disconnect event (low-level receive() returns a dict,
            # does NOT raise WebSocketDisconnect).
            if msg["type"] == "websocket.disconnect":
                logger.info("[STT-WS] Client disconnected: %s", addr)
                break

            # Binary: raw Int16 PCM at 16 kHz mono from the browser AudioWorklet
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

                    # ── Step 1: Whisper STT → original transcript ─────────────
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

                    # Send original transcript to UI immediately
                    await _j(websocket, {"type": "transcript", "text": original})

                    # ── Step 2: SMaLL-100 → translate to English for LLM ──────
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

                    # ── Step 2: Granite LLM → English response ────────────────
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

                    # ── Step 3: SMaLL-100 → translate response to user's language
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

                    # ── Send reply to frontend ────────────────────────────────
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


async def _j(ws: WebSocket, data: dict):
    await ws.send_text(json.dumps(data))
