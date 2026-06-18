# Voice Avatar — Project Analysis

## Stack

**Backend:** Python 3.11 + FastAPI + uvicorn (port 8765)  
**Frontend:** Vite 8 + Three.js (port 3005, proxied to backend)  
**Models:** Faster-Whisper medium (STT), SMaLL-100 (translation), OmniVoice (TTS)  
**Container:** Docker with multi-arch (CPU + GPU/CUDA 12.4)

---

## Directory Layout

```
V3/
├── server/                    # Python backend package
│   ├── main.py                # FastAPI app — 7 endpoints (776 lines)
│   ├── stt_engine.py          # Faster-Whisper medium (CTranslate2)
│   ├── llm_engine.py          # Hardcoded emotion responses (no LLM model)
│   ├── translation_engine.py  # SMaLL-100 (100 languages)
│   ├── tts_engine.py          # OmniVoice with streaming + ref_audio fallback
│   ├── pantomatrix.py         # Audio→52 ARKit blendshapes (v5 linear cross-fade)
│   ├── download_models.py     # Pre-downloads 3 models at Docker build time
│   ├── requirements.txt       # Dependencies
│   ├── __init__.py            # Empty — makes server/ a Python package
│   ├── ref_audio.wav          # Reference voice audio (425 KB)
│   └── ref_text.txt           # Reference text transcript
├── src/                       # Frontend JS modules
│   ├── avatar3d.js            # Three.js avatar class (521 lines)
│   ├── behavior.js            # Idle animation: breathing, blinking, saccades (244 lines)
│   └── stt.js                 # STTManager: mic recording + WAV blob generation (243 lines)
├── main.js                    # App entry: UI binds, WS comms, audio playback (631 lines)
├── avatar-widget.js           # Standalone widget for third-party embedding
├── index.html                 # UI layout (236 lines)
├── index.css                  # Styling
├── vite.config.js             # Vite config with proxy rules
├── nginx.conf                 # Nginx config for Docker production
├── package.json               # vite + three.js dependencies
├── Dockerfile.server          # Python backend Dockerfile (69 lines)
├── Dockerfile.frontend        # Vite build + nginx serve (30 lines)
├── docker-compose.yml         # GPU server + frontend services
├── .dockerignore              # Excludes venv, node_modules, pycache
├── .env / .env.example        # Server host config
├── AGENTS.md                  # Session log and known issues
├── API.md                     # Endpoint documentation
├── INTEGRATION.md             # Widget embedding guide
└── PROJECT_ANALYSIS.md        # This file
```

---

## Backend Engine Files

### `server/stt_engine.py` (123 lines)

**Model:** `Systran/faster-whisper-medium` (CTranslate2 backend, ~700 MB)

- Device: CUDA → float16, CPU → int8 (MPS not supported by CTranslate2 → falls back to CPU)
- Function: `transcribe(audio_np) → (text, lang_iso639, "neutral")`
- Auto-detects language from 99 languages
- Emotion label always `"neutral"` — Whisper doesn't detect emotion
- Thread-pooled execution to avoid blocking the event loop

### `server/llm_engine.py` (83 lines)

**No model — hardcoded responses.** Replaced Granite 4.0 Nano LLM to save ~2.5 GB disk space.

- `generate_response(user_text, session_id, user_emotion) → {intent, emotion, response}`
- Emotion detection from text keywords (angry/sad/happy/fearful/surprised/neutral)
- Intent detection from keywords (greeting/farewell/thanks/help/consent/denial)
- If `user_emotion` is provided (from STT mic path), it takes priority over keyword detection
- `load_model()` is a no-op (just logs)

**Hardcoded response table:**

| Emotion | Response |
|---------|----------|
| neutral | "I understand. How can I help you today?" |
| happy | "That is great to hear! I am glad you feel that way." |
| sad | "I am sorry to hear that. I hope things get better soon." |
| angry | "I understand your frustration. Let me see what I can do to help." |
| surprised | "Wow, that is quite something! I can see why you are surprised." |
| fearful | "That sounds concerning. Do not worry, I am here to help." |

### `server/translation_engine.py` (312 lines)

**Model:** `alirezamsh/small100` (~1.2 GB, 100 languages)

- `translate_to_english(text, src_lang) → str` — native → English
- `translate_from_english(english_text, target_lang) → (native_text, romanized_text)`
- Device: CUDA → float16 + `torch.compile`, MPS/CPU → float32
- Patches `tokenization_small100.py` for transformers 5.x compatibility
- Romanisation using `uroman` + `anyascii` for non-Latin scripts
- Language code mapping: Whisper→SMaLL-100 (e.g. `jw`→`jv`)
- Falls back to English for unsupported languages

### `server/tts_engine.py` (194 lines)

**Model:** `k2-fsa/OmniVoice` (~1.8 GB)

- `synthesize_stream(text, instruct, speed, num_step) → AsyncIterator[{audio, sample_rate, text}]`
- Three fallback strategies: `ref_audio` → `instruct` → `default`
- Device: CUDA → float16 + `torch.compile`, MPS → float16, CPU → float32
- Sentence splitting: splits on `. ! ?` or chunks every 60 words
- Single-thread executor to serialise GPU work and avoid CUDA context races
- `ref_audio.wav` used as voice reference (if available)

### `server/pantomatrix.py` (436 lines)

**Audio → 52 ARKit Blendshapes (v5)**

- 30 FPS output, 3-frame linear cross-fade between phoneme transitions
- 7 phoneme classes: SILENCE, LOW_VOWEL, MID_VOWEL, HIGH_VOWEL, FRICATIVE, SIBILANT, BILABIAL
- Feature extraction: RMS energy, ZCR, 3 sub-band energy ratios (0–500 Hz / 500–2 kHz / 2 kHz+)
- 95th-percentile energy normalisation
- Independent eye tracks: procedural blinking (3.2 s interval, 15% double-blink chance), gaze saccades, brow motion tied to energy
- Jaw micro-tremor and lateral drift to reduce robotic feel

### `server/main.py` (776 lines)

**FastAPI app — 7 endpoints:**

| # | Endpoint | Method | Description |
|---|----------|--------|-------------|
| 1 | `/api/v1/audio/{audio_id}` | GET | Serve WAV audio bytes from in-memory store |
| 2 | `/health` | GET | Model readiness status (OmniVoice / Whisper / SMaLL-100) |
| 3 | `/chat` | POST | Typed text → hardcoded response + translation + emotion |
| 4 | `/ws/tts` | WS | Legacy streaming TTS (text → PCM chunks → animation matrix) |
| 5 | `/ws/stt` | WS | Full pipeline: mic PCM → STT → translate → response → translate → reply |
| 6 | `/api/v1/chat` | POST | Unified: WAV upload → STT → translate → response → translate → TTS → PantoMatrix → JSON |
| 7 | `/speak/{emotion}` | POST | Forced-emotion TTS → audio + blendshape matrix |

**Blendshape emotion system:** `_EMOTION_BLENDSHAPES` dict (line 367) defines per-emotion ARKit shape weights. `_apply_emotion_to_matrix()` does two passes:
1. Additive blend of persistent emotion shapes into every frame
2. Floor-clamp inline token shapes (`[laughter]`, `[sigh]`, `[surprise-oh]`, `[dissatisfaction-hnn]`)

**Audio store:** In-memory dict with LRU eviction (max 100 entries)

---

## Frontend Files

### `main.js` (631 lines)

- **Two audio paths:**
  - **Speak button:** `POST /chat` → get response + emotion → `_speakText()` → `_speakViaLegacyTTS()` via `/ws/tts`
  - **Mic button:** `stt.start()` → record → `stt.stop()` → `getRecordingBlob()` → `POST /api/v1/chat` → play audio + apply animation matrix
- `_playAudioBuffer()` — Web Audio API + `avatar.setAnimationMatrix()` atomically with `source.start(0)`
- `_applyEmotion()` — `behavior.currentEmotion = e` + `avatar.setEmotion(e)` + UI dropdown
- Console HUD overlay (green-on-black debug panel)
- Calibration sliders for arm bone rotations

### `src/avatar3d.js` (521 lines)

- Three.js scene with OrbitControls, RoomEnvironment lighting, ACESFilmic tone mapping
- Loads GLB/GLTF models (Ready Player Me / Mixamo compatible)
- **Animation matrix playback:** Binary search for floor frame → linear interpolation between frames at 60/120 Hz
- Morph target matching with case-insensitive fallback + `jawOpen`↔`mouthOpen` alias
- Separate eye bone rotation + morph target gaze (8 directions)
- Independent arm sway system with calibration values
- Emotion morph targets applied only during idle (`!this.isSpeaking`)

### `src/behavior.js` (244 lines)

- **Idle procedural animation:** breathing (sine on head rotation), blinking (state machine with double-blink), gaze saccades
- **Emotion-driven body motion:** `_emotionBody` dict maps emotions to breathSpeed/breathAmp/swayAmp/headBiasY
- Emotion weight interpolation via `lerp(1-exp(-speed * dt))`
- No speech-driven motion (removed in earlier session)

### `src/stt.js` (243 lines)

- Two flows in one class:
  - **Legacy:** streams PCM via WebSocket to `/ws/stt`
  - **Unified:** accumulates PCM locally, `getRecordingBlob()` returns WAV Blob for `POST /api/v1/chat`
- AudioWorklet-based PCM capture at 16 kHz, Int16 format
- WAV header construction in JS (44-byte header + raw PCM data)

---

## Infrastructure Files

### `Dockerfile.server` (69 lines)

- Base: `python:3.11-slim` (CPU) / `pytorch/pytorch:2.5.1-cuda12.4-cudnn9-runtime` (GPU)
- Installs system deps (`ffmpeg`, `libsndfile1`), Python deps, pre-downloads models
- `ENV PIP_ROOT_USER_ACTION=ignore` silences pip root warnings
- `DEBIAN_FRONTEND=noninteractive` silences debconf warnings
- CMD: `uvicorn server.main:app --host 0.0.0.0 --port 8765 --loop asyncio`

### `Dockerfile.frontend` (30 lines)

- Two-stage: `node:22-alpine` builds → `nginx:stable-alpine` serves
- Copies `nginx.conf` for API/WS reverse proxy

### `docker-compose.yml` (53 lines)

- **server:** GPU build with nvidia runtime, cache volumes for huggingface + faster-whisper, healthcheck
- **frontend:** depends on server health, port 3005:80

### `nginx.conf` (75 lines)

- Proxies: `/api/`, `/chat`, `/health`, `/speak`, `/ws` → `server:8765`
- Serves static files from `/usr/share/nginx/html`
- WebSocket support with Upgrade/Connection headers

### `vite.config.js` (48 lines)

- Dev server on port 3005 with proxy rules for all backend endpoints
- Excludes `server/` directory from file watcher

### `download_models.py` (130 lines)

Downloads 3 models sequentially at Docker build time:
1. Faster-Whisper medium (int8, CPU) — sanity check with silence
2. SMaLL-100 — patches tokenizer, en→hi sanity check
3. OmniVoice — loads with CPU device map

Creates placeholder `ref_audio.wav` / `ref_text.txt` if missing.

---

## Endpoint Pipeline Matrix

| Step | `/chat` | `/ws/tts` | `/ws/stt` | `/api/v1/chat` | `/speak/{emotion}` |
|------|:-------:|:---------:|:---------:|:--------------:|:------------------:|
| Whisper STT | | | ✓ | ✓ | |
| Translate → EN | | | ✓ | ✓ | |
| Hardcoded response | ✓ | | ✓ | ✓ | |
| Translate → native | ✓ | | ✓ | ✓ | ✓ |
| OmniVoice TTS | | ✓ | | ✓ | ✓ |
| PantoMatrix | | ✓ | | ✓ | ✓ |
| Emotion blend | | ✓ | | ✓ | ✓ |

---

## Build & Run

**Local dev (two terminals):**

```bash
./start_server.sh    # Backend on :8765
npm run dev          # Frontend on :3005
```

**Docker (CPU):**

```bash
docker build -f Dockerfile.server -t avatar-server .
docker build -f Dockerfile.frontend -t avatar-frontend .
docker run -d -p 8765:8765 avatar-server
docker run -d -p 3005:80 avatar-frontend
```

**Docker (GPU):**

```bash
docker compose up -d --build
```

---

## Known Issues

1. `--reload` flag broken on macOS — uvicorn multiprocessing + MPS semaphore race
2. Bare `uvicorn` segfaults on macOS — use `start_server.sh` with `OBJC_DISABLE_INITIALIZE_FORK_SAFETY=YES`
3. "No space left on device" during Docker build — server `/tmp` disk capacity, not a code bug
