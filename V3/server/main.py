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
import logging
import os
import time
import warnings
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
warnings.filterwarnings("ignore", message=".*clean_up_tokenization.*")

from server.pipeline_orchestrator import (
    IndicTrans2Engine,
    PipelineOrchestrator,
    VEXYL_STT_URL,
    LLM_BASE_URL,
    LLM_API_KEY,
    LLM_MODEL,
    INDIC_TRANS2_EN_INDIC,
    INDIC_TRANS2_INDIC_EN,
    TRANSLATION_DEVICE,
)
from server.local_tts import LocalTTS

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
            "llm": "disabled (using static mock responses)",
            "tts": ("loaded (local omnivoice)" if (_tts_engine and _tts_engine.is_ready)
                    else "degraded (silent fallback)"),
        },
        "models": {
            "indic_en": INDIC_TRANS2_INDIC_EN,
            "en_indic": INDIC_TRANS2_EN_INDIC,
            "llm": "disabled",
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
    Simple text → translation → TTS (one-shot, no streaming).
    Useful for debugging the translation pipeline.
    """
    if not req.text.strip():
        return {"error": "Empty text"}

    flores_src = "hin_Deva"
    if req.lang == "en":
        flores_src = "eng_Latn"

    # Translate to English if needed
    english = req.text
    if flores_src != "eng_Latn":
        english = await _trans_engine.indic_to_eng(req.text, flores_src)

    return {
        "original": req.text,
        "english": english,
        "stub": "LLM + TTS bypassed in debug mode",
        "note": "Use /ws/s2s for full pipeline",
    }
