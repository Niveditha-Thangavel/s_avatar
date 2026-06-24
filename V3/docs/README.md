# S2S Voice Avatar

A low-latency Speech-to-Speech (S2S) pipeline that converts voice in Indian languages into a spoken, lip-synced 3D avatar response.

**Input:** Microphone audio (any of 14 Indian languages)  
**Output:** Synthesized speech + 3D avatar with real-time ARKit blendshape animation (52 blendshapes at 30 FPS)

---

## Quick Start

### Prerequisites

- Docker & Docker Compose
- ~7 GB disk space for model weights (persisted in Docker volumes)
- A [Hugging Face token](https://huggingface.co/settings/tokens) (`HF_TOKEN`) with access to [ai4bharat/indic-conformer-600m-multilingual](https://huggingface.co/ai4bharat/indic-conformer-600m-multilingual) — only needed on FIRST RUN for model download

### Run the Full Stack

```bash
# First run — downloads ~7 GB of models:
HF_TOKEN=hf_xxx docker compose -f compose.yml up --build -d

# Subsequent runs — models cached in volumes, no token needed:
docker compose -f compose.yml up --build -d
```

This starts:

| Service | Port | Description |
|---|---|---|
| Server | `8765` | FastAPI — WebSocket S2S pipeline, health, debug, TTS endpoints |
| Vexyl STT | `8080` | Streaming speech-to-text (WebSocket + batch REST API) |

Models download at container **startup** (not build time) and persist in Docker volumes. First startup may take 5-15 minutes depending on bandwidth. The orchestrator depends on Vexyl STT being healthy — Docker Compose handles this with health checks.

### Also Required (run separately)

| Service | Port | Description | Start Command |
|---|---|---|---|
| vLLM-Omni TTS | `8091` | Text-to-speech inference | `vllm serve k2-fsa/OmniVoice --omni --port 8091 --trust-remote-code` |
| LLM endpoint | `8000` | OpenAI-compatible LLM | Any OpenAI-compatible API |

### Run Locally (Debug)

```bash
cd server
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
# Download models before first run:
python download_models.py
uvicorn main:app --host 0.0.0.0 --port 8765 --loop asyncio
```

Models are cached locally; see `download_models.py` for details.

### Validate Stack Health

```bash
python server/test_stack.py
```

Or check the health endpoint:

```bash
curl http://localhost:8765/health
```

Response:

```json
{
  "status": "ok",
  "version": "2.0-s2s",
  "services": {
    "translator": "loaded",
    "vexyl_stt": "ws://localhost:8080",
    "tts": "loaded (local omnivoice)"
  }
}
```

---

## Project Structure

```
V3/
├── server/                    # Python FastAPI S2S Server
│   ├── main.py                # FastAPI — /ws/s2s, /health, /chat, /tts, /speak/{emotion}
│   ├── pipeline_orchestrator.py  # PipelineOrchestrator + IndicTrans2Engine (CTranslate2)
│   ├── local_tts.py           # OmniVoice TTS wrapper with silent fallback
│   ├── pantomatrix.py         # Audio→52 ARKit blendshapes at 30 FPS
│   ├── sentence_buffer.py     # Streaming sentence segmenter (auto-flush)
│   ├── download_models.py     # Model downloader (local dev only)
│   ├── entrypoint.sh          # Startup model downloader (Docker)
│   └── test_stack.py          # Dashboard script for stack health
│   └── vexyl_stt/             # Vexyl STT engine (nested for shared context)
│       ├── vexyl_stt_server.py  # Streaming WebSocket + batch REST API
│       ├── Dockerfile           # Container — downloads model at startup
│       └── entrypoint.sh        # Startup model downloader
├── client/                    # AvatarWidget — single-file embeddable 3D avatar
│   ├── avatar-widget.js       # AvatarWidget — Three.js scene, emotion body, lip-sync, idle anim
│   └── widget-demo.html       # Full demo page with S2S WebSocket integration
└── docs/                      # Documentation
```

---

## Supported Languages

| BCP-47 Code | Language | BCP-47 Code | Language |
|---|---|---|---|
| `hi-IN` | Hindi | `mr-IN` | Marathi |
| `ta-IN` | Tamil | `gu-IN` | Gujarati |
| `te-IN` | Telugu | `bn-IN` | Bengali |
| `ml-IN` | Malayalam | `pa-IN` | Punjabi |
| `kn-IN` | Kannada | `or-IN` | Odia |
| | | `as-IN` | Assamese |
| | | `ur-IN` | Urdu |

14 languages total across the pipeline. Vexyl STT also supports `sa-IN` (Sanskrit) and `ne-IN` (Nepali).

---

## Environment Variables

### Orchestrator

| Variable | Default | Description |
|---|---|---|
| `HF_TOKEN` | — | Hugging Face token — only needed on first run to download gated STT model |
| `VEXYL_STT_URL` | `ws://localhost:8080` | Vexyl STT WebSocket endpoint |
| `VEXYL_STT_API_KEY` | _(empty)_ | Shared secret for STT authentication |
| `LLM_BASE_URL` | `http://localhost:8000/v1` | OpenAI-compatible LLM endpoint |
| `LLM_API_KEY` | `sk-no-key-required` | LLM API key |
| `LLM_MODEL` | `granite-4.0-nano` | LLM model name |
| `INDIC_TRANS2_EN_INDIC` | `~/.cache/ctranslate2/ct2-rotary-indictrans2-en-indic-dist-200M` | English→Indic model path |
| `INDIC_TRANS2_INDIC_EN` | `~/.cache/ctranslate2/ct2-rotary-indictrans2-indic-en-dist-200M` | Indic→English model path |
| `TRANSLATION_DEVICE` | `auto` | `auto`, `cuda`, or `cpu` |

### Vexyl STT

| Variable | Default | Description |
|---|---|---|
| `VEXYL_STT_HOST` | `0.0.0.0` | Bind address |
| `VEXYL_STT_PORT` | `8080` | Server port |
| `VEXYL_STT_DECODE` | `ctc` | `ctc` (faster) or `rnnt` (more accurate) |
| `VEXYL_STT_DEVICE` | `auto` | `auto`, `cpu`, or `cuda` |
| `VEXYL_STT_MAX_CONN` | `50` | Max concurrent WebSocket connections |
| `VEXYL_STT_API_KEY` | _(empty)_ | Shared secret for authentication |

---

## Port Reference

| Service | Port |
|---|---|
| Backend orchestrator | 8765 |
| Vexyl STT | 8080 |
| vLLM-Omni TTS | 8091 |
| LLM endpoint | 8000 |
| Frontend (Vite dev) | 3005 |

---

## Removed / Deprecated

These legacy modules were replaced in v2.0+:

- `stt_engine.py` — old Faster-Whisper STT (replaced by Vexyl STT)
- `translation_engine.py` — old SMaLL-100 (replaced by IndicTrans2 via CTranslate2)
- `llm_engine.py` — old hardcoded responses (replaced by external LLM)
- `tts_engine.py` — old direct OmniVoice integration (replaced by vLLM-Omni HTTP API / local TTS)
- Legacy endpoints: `/ws/tts`, `/ws/stt`, `/api/v1/chat`, `/speak/{emotion}` — all removed
