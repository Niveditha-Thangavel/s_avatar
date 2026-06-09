# ================================================================
# backend/main.py  (production-hardened version)
# ================================================================

import asyncio, json, uuid, os, subprocess, base64, logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import soundfile as sf
import numpy as np

logging.basicConfig(level=os.getenv("LOG_LEVEL", "info").upper())
log = logging.getLogger("avatar")

# ── Globals loaded once at startup ───────────────────────────
_kokoro   = None
_whisper  = None
_rhubarb  = "./rhubarb"

@asynccontextmanager
async def lifespan(app: FastAPI):
    global _kokoro, _whisper
    log.info("Loading TTS model...")
    from kokoro_onnx import Kokoro
    _kokoro = Kokoro("/app/models/kokoro-v0_19.onnx",
                     "/app/models/voices.bin")
    log.info("✅ Kokoro ready")

    log.info("Loading Whisper model...")
    import whisper
    _whisper = whisper.load_model("base")
    log.info("✅ Whisper ready")

    yield   # app runs here

    log.info("Shutting down...")


app = FastAPI(title="Avatar API", version="1.0.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Viseme map ────────────────────────────────────────────────
RHUBARB_TO_ARKIT = {
    "A": {"jawOpen": 0.7,  "viseme_aa": 1.0},
    "B": {"jawOpen": 0.0,  "viseme_PP": 1.0},
    "C": {"jawOpen": 0.4,  "viseme_I":  1.0},
    "D": {"jawOpen": 0.3,  "viseme_E":  0.8},
    "E": {"jawOpen": 0.5,  "viseme_O":  1.0},
    "F": {"jawOpen": 0.0,  "viseme_U":  1.0},
    "G": {"jawOpen": 0.3,  "viseme_FF": 1.0},
    "H": {"jawOpen": 0.15, "viseme_TH": 0.8},
    "X": {"jawOpen": 0.0},
}

# ── TTS ───────────────────────────────────────────────────────
async def synthesize(text: str, out_path: str, voice: str = "af_heart"):
    samples, sr = _kokoro.create(text, voice=voice, speed=1.0)
    sf.write(out_path, samples, sr)
    log.info(f"TTS done → {out_path}")

# ── Viseme extraction ─────────────────────────────────────────
def extract_rhubarb(audio_path: str, text: str) -> list:
    result = subprocess.run(
        [_rhubarb, "-f", "json", "--recognizer", "phonetic", "-d", text, audio_path],
        capture_output=True, text=True, timeout=30
    )
    if result.returncode != 0:
        log.warning(f"Rhubarb failed: {result.stderr[-200:]}, falling back to Whisper")
        return extract_whisper(audio_path)

    data = json.loads(result.stdout)
    return [
        {
            "time"    : c["start"],
            "duration": round(c["end"] - c["start"], 3),
            "shape"   : c["value"],
            "blends"  : RHUBARB_TO_ARKIT.get(c["value"], {"jawOpen": 0.0})
        }
        for c in data["mouthCues"]
    ]

def extract_whisper(audio_path: str) -> list:
    PMAP = {
        "a":"A","e":"D","i":"C","o":"E","u":"F",
        "m":"B","b":"B","p":"B","f":"G","v":"G"
    }
    result  = _whisper.transcribe(audio_path, word_timestamps=True)
    visemes = []
    for seg in result["segments"]:
        for word in seg.get("words", []):
            t   = word["start"]
            dur = (word["end"] - word["start"]) / max(len(word["word"].strip()), 1)
            for ch in word["word"].lower().strip():
                shape = PMAP.get(ch, "X")
                visemes.append({
                    "time"    : round(t, 3),
                    "duration": round(dur, 3),
                    "shape"   : shape,
                    "blends"  : RHUBARB_TO_ARKIT.get(shape, {"jawOpen": 0.0})
                })
                t += dur
    return visemes

# ── Request / Response models ─────────────────────────────────
class SpeakRequest(BaseModel):
    text  : str
    voice : str = "af_heart"
    method: str = "rhubarb"     # rhubarb | whisper

class SpeakResponse(BaseModel):
    audio_b64: str
    visemes  : list
    duration : float
    job_id   : str

# ── Endpoints ─────────────────────────────────────────────────
@app.get("/health")
def health():
    return {
        "status" : "ok",
        "tts"    : _kokoro  is not None,
        "whisper": _whisper is not None,
    }

@app.get("/voices")
def voices():
    return {
        "voices": [
            "af_heart", "af_bella", "af_nicole",
            "am_adam",  "am_michael",
            "bf_emma",  "bm_george",
        ]
    }

@app.post("/speak", response_model=SpeakResponse)
async def speak(req: SpeakRequest):
    if not req.text.strip():
        raise HTTPException(400, "text cannot be empty")

    job_id   = str(uuid.uuid4())[:8]
    wav_path = f"/tmp/{job_id}.wav"

    try:
        await synthesize(req.text, wav_path, req.voice)

        visemes  = (extract_rhubarb(wav_path, req.text)
                    if req.method == "rhubarb"
                    else extract_whisper(wav_path))

        info     = sf.info(wav_path)
        audio_b64= base64.b64encode(open(wav_path,"rb").read()).decode()

        return SpeakResponse(
            audio_b64=audio_b64,
            visemes=visemes,
            duration=info.duration,
            job_id=job_id
        )
    finally:
        if os.path.exists(wav_path):
            os.remove(wav_path)


@app.websocket("/ws/speak")
async def ws_speak(websocket: WebSocket):
    await websocket.accept()
    log.info("WS client connected")
    try:
        while True:
            data     = await websocket.receive_json()
            job_id   = str(uuid.uuid4())[:8]
            wav_path = f"/tmp/{job_id}.wav"

            await synthesize(data.get("text",""), wav_path,
                             data.get("voice","af_heart"))
            visemes = extract_rhubarb(wav_path, data.get("text",""))

            # Send visemes first so frontend can prepare
            await websocket.send_json({"type":"visemes","data":visemes})

            # Stream audio in 4KB chunks
            with open(wav_path,"rb") as f:
                while chunk := f.read(4096):
                    await websocket.send_bytes(chunk)

            await websocket.send_json({"type":"done"})
            os.remove(wav_path)

    except WebSocketDisconnect:
        log.info("WS client disconnected")