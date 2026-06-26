# S2S Voice Avatar

A high-performance, low-latency Speech-to-Speech (S2S) pipeline that converts user speech in Indian languages into a spoken, lip-synced 3D avatar response.

**Input:** Microphone audio (14 Indian languages)  
**Output:** Synthesized local voice + 3D avatar driven by real-time ARKit blendshapes (52 targets at 30 FPS)

---

## Architecture Overview

The system runs entirely locally/in-process within a dual-container Docker stack:

```
[Client Audio Stream]
       │ (16kHz Int16 PCM via WebSocket)
       ▼
┌──────────────┐
│  Vexyl STT   │ ──► [Indic Transcripts]
└──────────────┘
       │
       ▼
┌──────────────┐
│  Server      │
│  Orchestrator│
│  (FastAPI)   │ ──► [Select Static Response (English)]
└──────────────┘
       │
       ▼
┌──────────────┐
│ IndicTrans2  │ ──► [Translate to Target Indic Language]
└──────────────┘
       │
       ▼
┌──────────────┐
│  LocalTTS    │ ──► [Synthesize Audio (OmniVoice)]
└──────────────┘
       │
       ▼
┌──────────────┐
│ PantoMatrix  │ ──► [Extract 52 ARKit Blendshapes]
└──────────────┘
       │
       ▼
[Client Output Stream]
         (PCM Audio + JSON Blendshapes via WebSocket)
```

---

## Quick Start & Deployment

### Prerequisites

- **Nvidia GPU** (CUDA 12.4 compatible) with Nvidia Container Toolkit installed.
- **Docker & Docker Compose**.
- **Hugging Face Token** (`HF_TOKEN`) with access to [ai4bharat/indic-conformer-600m-multilingual](https://huggingface.co/ai4bharat/indic-conformer-600m-multilingual) (only needed on the **first run** to download the gated STT model weights).

### Deploying the Stack

1. **Configure Environment:**
   Create a `.env` file in the `V3` root directory matching the format of `.env.example`:
   ```bash
   HF_TOKEN=your_hugging_face_token_here
   VITE_SERVER_HOST=your_server_ip_or_localhost
   VITE_SERVER_PORT=8765
   ```

2. **Launch with Docker Compose:**
   Deploy the stack using the compose file. The first run will automatically pull base images, build CUDA-specific environments, and download the model weights (approx. ~7 GB total) into persistent cache volumes:
   ```bash
   # Run with token (required on first launch for Conformer STT download)
   HF_TOKEN=hf_your_token docker compose -f compose.yml up --build -d
   ```

3. **Subsequent Runs:**
   Once model weights are downloaded and cached in the local `./model_cache` volumes, you do not need the Hugging Face token:
   ```bash
   docker compose -f compose.yml up -d
   ```

4. **Verify Health:**
   Check the orchestrator service readiness by querying the health endpoint:
   ```bash
   curl http://localhost:8765/health
   ```
   **Expected Response:**
   ```json
   {
     "status": "ok",
     "version": "2.0-s2s",
     "services": {
       "translator": "loaded",
       "vexyl_stt": "ws://vexyl-stt:8080",
       "tts": "loaded (local omnivoice)"
     }
   }
   ```

---

## Local Bare-Metal Execution (CPU / macOS / Linux)

If running without Docker (such as on Apple Silicon Macs or CPU-only Linux machines), you can configure and run both Vexyl STT and the Orchestrator Server concurrently with a single command script:

1. **Launch Stack:**
   Provide your Hugging Face access token to download the gated Conformer STT model weights on the first run, and execute the launcher script:
   ```bash
   HF_TOKEN=hf_your_token_here ./run_local_cpu.sh
   ```
2. **Subsequent Starts:**
   Once dependencies are compiled and model caches are populated, you can start the stack without the token:
   ```bash
   ./run_local_cpu.sh
   ```

*Note: The script creates virtual environments (`venv`), installs required PyTorch packages, downloads translation model weights, and manages starting/terminating both processes concurrently. Vexyl STT logs will be written to `server/vexyl_stt/vexyl_stt.log`.*



---

## Project Directory Structure

```
V3/
├── server/                    # Python FastAPI S2S Server
│   ├── main.py                # FastAPI — S2S WS interface, /health, /tts
│   ├── pipeline_orchestrator.py # Pipeline Orchestrator + CTranslate2 translation engine
│   ├── local_tts.py           # Local OmniVoice TTS wrapper with silent fallback
│   ├── pantomatrix.py         # Audio → 52 ARKit blendshapes extractor
│   ├── sentence_buffer.py     # Streaming sentence accumulator (auto-flush)
│   ├── download_models.py     # Model downloader script (for bare metal setup)
│   ├── requirements.txt       # Server dependencies
│   ├── Dockerfile.server      # Server container build (configured for CUDA 12.4 wheels)
│   └── vexyl_stt/             # Vexyl STT Engine
│       ├── vexyl_stt_server.py# Streaming ASR server
│       ├── Dockerfile         # STT container build
│       └── entrypoint.sh      # STT download wrapper
├── client/                    # Embeddable 3D avatar client
│   ├── avatar-widget.js       # Three.js 3D avatar loader, emotion driver, viseme playback
│   └── widget-demo.html       # Client HTML debug demo page
└── compose.yml                # Docker Compose orchestration
```

---

## Supported Languages

The system supports speech-to-speech interaction across **14 Indian languages**:

| BCP-47 Code | Language | BCP-47 Code | Language |
|---|---|---|---|
| `hi-IN` | Hindi | `mr-IN` | Marathi |
| `ta-IN` | Tamil | `gu-IN` | Gujarati |
| `te-IN` | Telugu | `bn-IN` | Bengali |
| `ml-IN` | Malayalam | `pa-IN` | Punjabi |
| `kn-IN` | Kannada | `or-IN` | Odia |
| `ur-IN` | Urdu | `as-IN` | Assamese |

*Note: Vexyl STT also accepts `sa-IN` (Sanskrit) and `ne-IN` (Nepali).*

---

## Active Environment Variables

### Server (Orchestrator)

| Variable | Default | Description |
|---|---|---|
| `VEXYL_STT_URL` | `ws://localhost:8080` | WebSocket endpoint to connect to Vexyl STT |
| `TRANSLATION_DEVICE` | `cuda` | CUDA GPU translation acceleration (`cuda` / `cpu`) |
| `INDIC_TRANS2_EN_INDIC` | `/root/.cache/ctranslate2/...` | Path to English-to-Indic translation model |
| `INDIC_TRANS2_INDIC_EN` | `/root/.cache/ctranslate2/...` | Path to Indic-to-English translation model |

### Vexyl STT

| Variable | Default | Description |
|---|---|---|
| `VEXYL_STT_PORT` | `8080` | Port for the ASR server |
| `VEXYL_STT_DEVICE` | `cuda` | Hardware accelerator for speech recognition |
| `VEXYL_STT_SILENCE_DURATION` | `1.2` | VAD silence duration timeout before flushing |
| `VEXYL_STT_SILENCE_THRESHOLD` | `0.015` | Energy threshold coefficient for silence VAD |
