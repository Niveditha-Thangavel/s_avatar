# AGENTS — Project State (S2S Architecture)

## Architecture (v2.0)

**Pipeline:**
Client mic → Vexyl STT (WebSocket) → SentenceBuffer → IndicTrans2 (→EN) → External LLM → IndicTrans2 (→Indic) → vLLM-Omni TTS → **PantoMatrix (server-side)** → blendshape_matrix → Avatar3D (Three.js)

```
┌──────────┐    ┌──────────┐    ┌──────────┐    ┌──────────┐    ┌──────────┐
│  Vexyl   │───▶│ Indic→EN│───▶│   LLM    │───▶│  EN→Indic│───▶│ OmniVoice│
│   STT    │    │  Trans   │    │(OpenAI)  │    │   Trans  │    │   TTS    │
└──────────┘    └──────────┘    └──────────┘    └──────────┘    └──────────┘
     ▲               ▲              ▲               ▲               │
audio in         sentence       english        response         PCM audio
                  complete       ready          indic           + all chunks
                                                                     │
                                                               ┌─────▼──────┐
                                                               │PantoMatrix │
                                                               │(server-side│
                                                               │ Python,    │
                                                               │ executor)  │
                                                               └─────┬──────┘
                                                                     │
                                                        {blendshape_matrix}
                                                        over WebSocket
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
- **Server-side PantoMatrix**: After all PCM chunks are received from vLLM-Omni, `extract_blendshapes()` runs in a thread executor (non-blocking), then sends the matrix to the client before `tts_end`
- **Gapless audio**: Frontend schedules PCM chunks via `AudioContext` `nextPlayTime` cursor — no gaps between chunks

## Server Files

| File | Description |
|---|---|
| `server/main.py` | FastAPI — `/ws/s2s` (S2S pipeline), `/health`, `/chat` (debug) |
| `server/pipeline_orchestrator.py` | `PipelineOrchestrator` + `IndicTrans2Engine` (CT2) + data types |
| `server/sentence_buffer.py` | `SentenceBuffer` — streaming text fragmentation, auto-flush timer |
| `server/pantomatrix.py` | `extract_blendshapes()` — 52 ARKit blendshapes at 30 FPS from float32 PCM |
| `server/download_models.py` | Pre-downloads IndicTrans2 CT2 models (indic-en-1B, en-indic-1B) |

## Frontend Files

| File | Description |
|---|---|
| `main.js` | App entry point — S2SManager wiring, audio scheduling, avatar driving |
| `src/stt.js` | `S2SManager` — `/ws/s2s` WebSocket client (mic PCM → server, audio + blendshapes ← server) |
| `src/avatar3d.js` | `Avatar3D` — Three.js 3D avatar, blendshape animation matrix, emotion morphs |
| `src/behavior.js` | `BehaviorManager` — procedural idle: breathing, blink, gaze, emotion-driven body (6 emotions) |
| `avatar-widget.js` | `AvatarWidget` — embeddable standalone widget with full blendshape + emotion support |
| `widget-demo.html` | Demo page for the widget — wired to `/ws/s2s`, receives server blendshape matrix |

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
| `{"type":"transcript","text":"...","lang":"..."}` | Real-time STT transcript from Vexyl |
| `{"type":"tts_start","seq":1,"text":"...","lang":"..."}` | TTS beginning for sentence seq |
| `{"type":"audio_chunk","seq":1,"sample_rate":24000,"byte_length":N}` | Audio metadata → followed by binary float32 PCM |
| `[binary float32 PCM]` | Raw audio data (one chunk per audio_chunk message) |
| `{"type":"blendshape_matrix","seq":1,"matrix":[...]}` | 30 FPS ARKit blendshape matrix for lip-sync, sent after all audio chunks |
| `{"type":"tts_end","seq":1}` | Sentence fully transmitted (audio + blendshapes) |
| `{"type":"pipeline_status","session_id":"...","seq":2,"lang":"..."}` | Health heartbeat every 2s |
| `{"type":"error","message":"..."}` | Pipeline error |

## PantoMatrix (Server-side)

`server/pantomatrix.py` — `extract_blendshapes(audio_bytes: bytes, sample_rate: int) → List[dict]`

- Runs in `loop.run_in_executor(None, ...)` inside `_generate_tts` — does not block the asyncio event loop
- Input: all PCM chunks for a sentence concatenated into one `bytes` buffer (float32 LE)
- Output: `[{"time": float, "blendshapes": {name: weight}}, ...]` at 30 FPS
- Algorithm: sub-band energy → 7 phoneme classes → linear cross-fade (3 frames) → ARKit poses
- Eye blink, gaze saccade, brow tracks included in every frame
- Sent as `{"type":"blendshape_matrix", "seq": N, "matrix": [...]}` before `tts_end`

## Emotion System

All 6 emotions fully implemented across all layers:

| Emotion | BehaviorManager body params | Avatar3D morph targets |
|---|---|---|
| neutral | breathSpeed 1.8, swayAmp 0.02 | all zeros |
| happy | breathSpeed 2.4, swayAmp 0.035, headBiasY +0.04 | mouthSmile, cheekSquint, browOuterUp |
| sad | breathSpeed 1.2, swayAmp 0.008, headBiasY -0.06 | mouthFrown, browInnerUp, browDown |
| angry | breathSpeed 3.2, swayAmp 0.012 | browDown, eyeSquint, mouthFrown, noseSneer |
| surprised | breathSpeed 2.8, swayAmp 0.025, headBiasY +0.05 | eyeWide, browInnerUp, browOuterUp, mouthShrug |
| fearful | breathSpeed 2.2, swayAmp 0.010, headBiasY -0.03 | eyeWide, browInnerUp, browOuterUp, mouthFrown |

- During speech: emotion morphs are dormant (blendshape_matrix drives all shapes)
- During idle: `BehaviorManager.emotionWeights` lerp smoothly at 5 rad/s

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
| Service | Port |
|---|---|
| Backend orchestrator | 8765 |
| Vexyl STT | 8080 |
| vLLM-Omni TTS | 8091 |
| LLM endpoint | 8000 |
| Frontend (Vite dev) | 3005 |

## Removed / Deprecated
- `server/stt_engine.py` — old Faster-Whisper STT (replaced by Vexyl STT)
- `server/translation_engine.py` — old SMaLL-100 (replaced by IndicTrans2 via CTranslate2)
- `server/llm_engine.py` — old hardcoded responses (replaced by external LLM)
- `server/tts_engine.py` — old direct OmniVoice integration (replaced by vLLM-Omni HTTP API)
- Legacy endpoints: `/ws/tts`, `/ws/stt`, `/api/v1/chat`, `/speak/{emotion}` — all removed
