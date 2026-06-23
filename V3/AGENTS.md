# AGENTS — Project State (S2S Architecture)

## Architecture (v2.0)

**Pipeline:**
Client mic → Vexyl STT (WebSocket) → SentenceBuffer → IndicTrans2 (→EN) → External LLM → IndicTrans2 (→Indic) → vLLM-Omni TTS → PantoMatrix (server-side) → Client speaker + Avatar blendshapes

```
┌──────────┐    ┌──────────┐    ┌──────────┐    ┌──────────┐    ┌──────────────┐
│  Vexyl   │───▶│ Indic→EN│───▶│   LLM    │───▶│  EN→Indic│───▶│vLLM-Omni TTS │
│   STT    │    │  Trans   │    │(OpenAI)  │    │   Trans  │    │  (streaming) │
└──────────┘    └──────────┘    └──────────┘    └──────────┘    └──────┬───────┘
     ▲               ▲              ▲               ▲                 │
audio in         sentence       english        response          PCM chunks
                  complete       ready          indic                 │
                                                                ┌──────▼───────┐
                                                                │ PantoMatrix  │
                                                                │ (server, via │
                                                                │  executor)   │
                                                                └──┬───────┬───┘
                                                                   │       │
                                                              audio PCM  blendshape
                                                              (streamed)  matrix
                                                                   │       │
                                                                   ▼       ▼
                                                              ┌──────────────┐
                                                              │   Client     │
                                                              │ (buffer ≤500ms│
                                                              │  then flush) │
                                                              └──────┬───────┘
                                                                     │
                                                               ┌─────▼──────┐
                                                               │  Avatar3D  │
                                                               │  (Three.js)│
                                                               └────────────┘
```

**Key latency strategies:**
- **Sentence-level chunking**: `SentenceBuffer` detects `. ? ! | ।` — no waiting for full utterance
- **Pipeline parallelism**: Sentence N+1 translates while sentence N plays via TTS
- **Async stages**: 5 worker tasks connected by `asyncio.Queue` — each runs independently
- **Cross-fade hints**: Frontend gets `tts_start`/`tts_end` + sequence numbers for gap-free queuing
- **Server-side PantoMatrix**: After TTS streaming completes, `pantomatrix.py` runs in a thread executor and sends `blendshape_matrix` to the client. The frontend buffers audio chunks up to 500ms, then starts playback when the matrix arrives (perfect sync) or on timeout (close sync — elapsed time maps correctly because audio and matrix share the same duration).

## Server Files

| File | Description |
|---|---|
| `server/pantomatrix.py` | ARKit blendshape extraction from PCM audio (30 FPS, 52 shapes) |
| `server/main.py` | FastAPI — `/ws/s2s` (S2S pipeline), `/health`, `/chat` (debug) |
| `server/pipeline_orchestrator.py` | `PipelineOrchestrator` + `IndicTrans2Engine` (CT2) + data types |
| `server/sentence_buffer.py` | `SentenceBuffer` — streaming text fragmentation, auto-flush timer |
| `server/download_models.py` | Pre-downloads IndicTrans2 CT2 models (indic-en-1B, en-indic-1B) |

## Frontend Files

| File | Description |
|---|---|
| `main.js` | App entry point — S2SManager wiring, audio buffering (≤500ms for PantoMatrix sync), avatar driving |
| `src/stt.js` | `S2SManager` — `/ws/s2s` WebSocket client (mic PCM → server, audio PCM ← server) |
| `src/avatar3d.js` | `Avatar3D` — Three.js 3D avatar, blendshape animation matrix, emotion morphs |
| `src/behavior.js` | `BehaviorManager` — procedural idle: breathing, blink, gaze, emotion-driven body |
| `avatar-widget.js` | `AvatarWidget` — embeddable standalone widget with full PantoMatrix support |
| `widget-demo.html` | Demo page for the widget — wired to `/ws/s2s` |

## External Services

| Service | Protocol | Default URL | Env Var |
|---|---|---|---|
| Vexyl STT | WebSocket (`ws://`) | `ws://localhost:8080` | `VEXYL_STT_URL` |
| LLM (OpenAI-compatible) | HTTP POST `/v1/chat/completions` | `http://localhost:8000/v1` | `LLM_BASE_URL` |
| vLLM-Omni TTS | HTTP POST `/v1/audio/speech` | `http://localhost:8091/v1` | `TTS_BASE_URL` |
| IndicTrans2 (EN→Indic) | CTranslate2 (in-process) | `~/.cache/ctranslate2/en-indic-1B` | `INDIC_TRANS2_EN_INDIC` |
| IndicTrans2 (Indic→EN) | CTranslate2 (in-process) | `~/.cache/ctranslate2/indic-en-1B` | `INDIC_TRANS2_INDIC_EN` |

## WebSocket Protocol (`/ws/s2s`)

### Client → Server
| Message | Description |
|---|---|
| `{"type":"start","lang":"hi-IN","session_id":"abc"}` | Start session |
| `[binary Int16 PCM @ 16kHz mono]` | Audio stream |
| `{"type":"stop"}` | End session gracefully |
| `{"type":"cancel"}` | Abort session |

### Server → Client
| Message | Description |
|---|---|
| `{"type":"transcript","text":"...","lang":"..."}` | Real-time STT transcript |
| `{"type":"tts_start","seq":1,"text":"...","lang":"..."}` | TTS beginning for sentence seq |
| `{"type":"audio_chunk","seq":1,"sample_rate":24000,"byte_length":8192}` | Audio metadata → followed by binary float32 PCM |
| `[binary float32 PCM]` | Raw audio data for current sentence |
| `{"type":"blendshape_matrix","seq":1,"matrix":[...]}` | 30 FPS ARKit blendshape frames from server-side PantoMatrix |
| `{"type":"tts_end","seq":1}` | TTS complete for this sentence |
| `{"type":"pipeline_status","session_id":"...","seq":2,"lang":"..."}` | Health heartbeat (every 2s) |
| `{"type":"error","message":"..."}` | Error |

## PantoMatrix (Server-side)

The `extract_blendshapes(audio_bytes, sample_rate)` function in `server/pantomatrix.py` processes the complete PCM audio for a sentence and returns 30 FPS ARKit blendshape frames:
- Sub-band energy classification → 7 phoneme classes per frame
- Each class maps to a fixed 52-blendshape ARKit target pose
- Linear cross-fade between class transitions (3 frames)
- Runs in a `run_in_executor` thread after all TTS chunks have been streamed to the client

The frontend (`main.js`) buffers incoming audio chunks for up to 500ms. When `blendshape_matrix` arrives, the buffer is flushed simultaneously with `avatar.setAnimationMatrix()` — giving perfect audio-animation sync. If the 500ms timeout fires (e.g., matrix delayed), playback starts anyway; the `elapsed = audioContext.currentTime - audioStartTime` calculation naturally maps to the correct position in the matrix since audio duration equals matrix duration.

## Emotion System

All 6 emotions are fully implemented:
- `BehaviorManager` (`src/behavior.js`) — interpolates body params (breathing speed/amplitude, head sway) and blendshape weights per emotion
- `Avatar3D.setEmotion()` (`src/avatar3d.js`) — immediately forces morph targets on the mesh
- Emotions: `neutral`, `happy`, `sad`, `angry`, `surprised`, `fearful`
- During speech: emotion morphs are dormant (server matrix drives all blendshapes)
- During idle: emotion weights lerp smoothly via `BehaviorManager`

## Cache Locations
- IndicTrans2 CT2: `~/.cache/ctranslate2/en-indic-1B/` and `~/.cache/ctranslate2/indic-en-1B/` (~2.4GB total)

## How to Run

### Prerequisites
1. Start **Vexyl STT** server (port 8080)
2. Start **vLLM-Omni** for TTS (port 8091):
   ```bash
   vllm serve k2-fsa/OmniVoice --omni --port 8091 --trust-remote-code
   ```
3. Have an **OpenAI-compatible LLM endpoint** available (port 8000)
4. Download IndicTrans2 models:
   ```bash
   python server/download_models.py
   ```

### Launch Server
```bash
cd V3
./start_server.sh
# Or directly:
uvicorn server.main:app --host 0.0.0.0 --port 8765 --loop asyncio
```

### Launch Frontend
```bash
cd V3
npm run dev
# Open http://localhost:3005
```

## Ports
- Backend orchestrator: 8765 (uvicorn)
- Vexyl STT: 8080
- vLLM-Omni TTS: 8091
- LLM endpoint: 8000
- Frontend (Vite dev): 3005

## Removed / Deprecated
- `server/stt_engine.py` — old Faster-Whisper STT (replaced by Vexyl STT)
- `server/translation_engine.py` — old SMaLL-100 (replaced by IndicTrans2 via CTranslate2)
- `server/llm_engine.py` — old hardcoded responses (replaced by external LLM)
- `server/tts_engine.py` — old direct OmniVoice integration (replaced by vLLM-Omni HTTP API)
- Legacy endpoints: `/ws/tts`, `/ws/stt`, `/api/v1/chat`, `/speak/{emotion}` — all removed
