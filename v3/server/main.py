"""
main.py  –  FastAPI WebSocket + HTTP server

Endpoints:
  GET  /health   – liveness check
  POST /chat     – typed text → reply text (placeholder LLM)
  WS   /ws/tts   – text  → streamed PCM audio (OmniVoice)
  WS   /ws/stt   – PCM audio stream → full transcript + reply (Whisper)

STT flow:
  client streams raw Int16 PCM frames → server accumulates all frames
  → client sends { type: "stop" } → server flushes buffer to Whisper
  → Whisper transcribes the whole utterance at once (no custom VAD)
  → reply text sent back over the same socket

Run with:
  uvicorn main:app --host 0.0.0.0 --port 8765 --reload
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

# Suppress noisy tokenizer / transformers warnings from OmniVoice internals
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
warnings.filterwarnings("ignore", message=".*clean_up_tokenization.*")
warnings.filterwarnings("ignore", message=".*forced_decoder_ids.*")
warnings.filterwarnings("ignore", message=".*SuppressTokens.*")
warnings.filterwarnings("ignore", message=".*multilingual Whisper.*")

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from chat import get_response, get_response_from_audio
from stt_engine import AudioBuffer
from tts_engine import get_model, synthesize_stream

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
)
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Startup — warm up models concurrently
# ---------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("[Server] Warming up models …")
    from chat import load_chat_model
    await asyncio.gather(get_model(), load_chat_model())
    logger.info("[Server] All models ready – accepting connections")
    yield
    logger.info("[Server] Shutdown")


app = FastAPI(title="Voice Avatar Server", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# HTTP endpoints
# ---------------------------------------------------------------------------

@app.get("/health")
async def health():
    return {"status": "ok", "timestamp": time.time()}


class ChatRequest(BaseModel):
    text: str


@app.post("/chat")
async def chat_endpoint(req: ChatRequest):
    if not req.text.strip():
        return {"reply": "", "native_text": "", "romanized_text": ""}
    reply = await get_response(req.text.strip())
    if isinstance(reply, dict):
        return {
            "reply": reply.get("native_text", ""),
            "native_text": reply.get("native_text", ""),
            "romanized_text": reply.get("romanized_text", "")
        }
    return {
        "reply": str(reply),
        "native_text": str(reply),
        "romanized_text": str(reply)
    }


# ---------------------------------------------------------------------------
# WS /ws/tts  –  text → streamed PCM audio
# ---------------------------------------------------------------------------

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
                await _send_json(websocket, {"type": "error", "message": "Invalid JSON"})
                continue

            msg_type = msg.get("type")

            if msg_type == "speak":
                text           = msg.get("text", "").strip()
                romanized_text = msg.get("romanized_text") or msg.get("romanizedText") or None
                instruct       = msg.get("instruct") or None
                speed          = float(msg.get("speed",   1.0))
                num_step       = int(msg.get("numStep", 16))

                if not text:
                    await _send_json(websocket, {"type": "error", "message": "Empty text"})
                    continue

                await _send_json(websocket, {"type": "status", "data": "generating"})

                try:
                    async for chunk in synthesize_stream(
                        text=text,
                        romanized_text=romanized_text,
                        instruct=instruct,
                        speed=speed,
                        num_step=num_step,
                    ):
                        await _send_json(websocket, {
                            "type":          "chunk",
                            "text":          chunk["text"],
                            "romanized_text": chunk.get("romanized_text", ""),
                            "sampleRate":    chunk["sample_rate"],
                            "byteLength":    len(chunk["audio"]),
                        })
                        await websocket.send_bytes(chunk["audio"])

                    await _send_json(websocket, {"type": "status", "data": "complete"})

                except Exception as exc:
                    logger.exception("[TTS-WS] synthesis failed: %s", exc)
                    await _send_json(websocket, {"type": "error", "message": str(exc)})

            elif msg_type == "stop":
                await _send_json(websocket, {"type": "status", "data": "stopped"})

            else:
                await _send_json(websocket, {
                    "type": "error", "message": f"Unknown type: {msg_type}"
                })

    except WebSocketDisconnect:
        logger.info("[TTS-WS] Disconnected: %s", websocket.client)
    except Exception as exc:
        logger.exception("[TTS-WS] Unexpected error: %s", exc)


# ---------------------------------------------------------------------------
# WS /ws/stt  –  streamed audio → transcript + reply
# ---------------------------------------------------------------------------

@app.websocket("/ws/stt")
async def ws_stt(websocket: WebSocket):
    await websocket.accept()
    logger.info("[STT-WS] Connected: %s", websocket.client)

    audio_buf = AudioBuffer()          # accumulates all PCM frames
    language: Optional[str] = None

    try:
        while True:
            msg = await websocket.receive()

            # ── binary: raw Int16 PCM at 16 kHz mono ───────────────────────
            if msg["type"] == "websocket.receive" and msg.get("bytes"):
                pcm_i16 = np.frombuffer(msg["bytes"], dtype=np.int16)
                pcm_f32 = pcm_i16.astype(np.float32) / 32768.0
                audio_buf.push(pcm_f32)
                # Optionally echo back buffer duration so the client can show
                # a recording timer without any extra round-trips
                # (comment out if the extra messages are unwanted)
                # await _send_json(websocket, {
                #     "type": "buffering", "duration": round(audio_buf.duration_s, 1)
                # })

            # ── text: JSON control message ──────────────────────────────────
            elif msg["type"] == "websocket.receive" and msg.get("text"):
                try:
                    ctrl = json.loads(msg["text"])
                except json.JSONDecodeError:
                    continue

                ctrl_type = ctrl.get("type")

                # config — set language hint
                if ctrl_type == "config":
                    language = ctrl.get("language") or None
                    logger.info("[STT-WS] Language set to: %s", language)
                    await _send_json(websocket, {"type": "status", "data": "listening"})

                # stop — flush buffer and transcribe the whole thing
                elif ctrl_type == "stop":
                    audio = audio_buf.flush()

                    if audio is None:
                        await _send_json(websocket, {"type": "status", "data": "stopped"})
                        continue

                    # 1. Feed audio directly to Ultravox model
                    await _send_json(websocket, {"type": "status", "data": "thinking"})
                    response = await get_response_from_audio(audio)

                    # 2. Extract transcript and reply
                    transcript = response.get("user_transcript", "")
                    native_reply = response.get("native_text", "")
                    romanized_reply = response.get("romanized_text", "")

                    # 3. Send transcript back (what the user said)
                    await _send_json(websocket, {"type": "transcript", "text": transcript})

                    # 4. Send reply (what the avatar will say)
                    await _send_json(websocket, {
                        "type": "reply",
                        "text": native_reply,
                        "native_text": native_reply,
                        "romanized_text": romanized_reply
                    })
                    await _send_json(websocket, {"type": "status", "data": "stopped"})

                # cancel — discard buffer without transcribing
                elif ctrl_type == "cancel":
                    audio_buf.reset()
                    await _send_json(websocket, {"type": "status", "data": "cancelled"})

    except WebSocketDisconnect:
        logger.info("[STT-WS] Disconnected: %s", websocket.client)
    except Exception as exc:
        logger.exception("[STT-WS] Unexpected error: %s", exc)


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

async def _send_json(ws: WebSocket, data: dict):
    await ws.send_text(json.dumps(data))
