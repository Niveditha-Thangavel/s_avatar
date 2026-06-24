# Architecture

## Overview

The S2S Voice Avatar is a real-time Speech-to-Speech pipeline that converts a user's spoken input in an Indian language into a synthesized voice response delivered by a 3D animated avatar with precise lip-sync.

```
┌──────────┐    ┌──────────┐    ┌──────────┐    ┌──────────┐    ┌──────────────┐
│  Vexyl   │───▶│ Indic→EN│───▶│   LLM    │───▶│  EN→Indic│───▶│  OmniVoice   │
│   STT    │    │  Trans   │    │(external)│    │   Trans  │    │  TTS (Local) │
└──────────┘    └──────────┘    └──────────┘    └──────────┘    └──────┬───────┘
     ▲               ▲              ▲               ▲                 │
audio in         sentence       english        response           PCM audio
(16kHz Int16)    complete       ready          indic            (24kHz float32)
                                                                       │
                                                                 ┌─────▼──────┐
                                                                 │ PantoMatrix │
                                                                 │ (server-side│
                                                                 │  blendshape │
                                                                 │  extractor) │
                                                                 └─────┬──────┘
                                                                       │
                                                          blendshape_matrix
                                                              (JSON)
                                                                       │
                                                                 ┌─────▼──────┐
                                                                 │  Avatar3D  │
                                                                 │ (Three.js) │
                                                                 └────────────┘
```

---

## Pipeline Stages

### 1. Audio Ingestion (Client → Server)

The browser captures microphone audio at 16 kHz mono via an `AudioWorkletProcessor`. Samples are converted from float32 to **Int16 PCM** and streamed over a WebSocket to the orchestrator server. The WebSocket protocol is binary-optimized: audio metadata is sent as JSON, followed immediately by raw binary PCM bytes.

### 2. Speech-to-Text (Vexyl STT)

The orchestrator relays audio to Vexyl STT — a standalone WebSocket service wrapping the [ai4bharat/indic-conformer-600m-multilingual](https://huggingface.co/ai4bharat/indic-conformer-600m-multilingual) model (600M parameters). Vexyl provides:

- **Energy-based VAD** — no external VAD dependency, configurable silence threshold and duration
- **Real-time streaming** — returns `final` transcript messages as speech is detected
- **14 Indian languages** — including Sanskrit and Nepali in addition to the 12 supported downstream
- **Batch REST API** — async file transcription (WAV, MP3, FLAC, OGG, M4A) up to 5 minutes / 25 MB

Transcripts arrive in real-time — the pipeline does not wait for the user to finish speaking.

### 3. Sentence Buffering

The `SentenceBuffer` (`server/sentence_buffer.py`) accumulates streaming transcript fragments and emits complete sentences when it detects boundaries (`. ? ! | ।`). An auto-flush timer (0.8 seconds) ensures partial utterances don't stall the pipeline — if no new text arrives within the window, the buffer emits whatever it has.

### 4. Translation (IndicTrans2 via CTranslate2)

Each complete sentence is translated through two distilled CTranslate2 models:

- **Indic → English** (`indic-en-1B`): Converts the user's Indic language input to English
- **English → Indic** (`en-indic-1B`): Converts the response back to the target Indic language

Both models run **in-process** on CPU or GPU using `sentencepiece` tokenizers with FLORES-200 language codes. Models are cached at `~/.cache/ctranslate2/` (~1.6 GB total). In Docker, models download at container startup into persistent volumes — not baked into the image.

```
Input:  "नमस्ते" (Hindi)
        → indic-en-1B → "Hello"
        → [LLM processes English response]
        → "How can I help you?"
        → en-indic-1B → "मैं आपकी कैसे मदद कर सकता हूँ?"
```

### 5. LLM Response Generation

The pipeline connects to an external OpenAI-compatible LLM endpoint. The architecture currently cycles through static mock responses for testing — the LLM integration is configurable via `LLM_BASE_URL`, `LLM_API_KEY`, and `LLM_MODEL` environment variables.

The response is appended with a "You said: {original text}" suffix in the target language so the avatar verbally acknowledges the user's input.

### 6. Text-to-Speech (OmniVoice)

`LocalTTS` (`server/local_tts.py`) wraps `k2-fsa/OmniVoice` for local inference. It uses a **reference audio sample** for voice cloning — the speaker's voice characteristics are extracted from `ref_audio.wav`. Output is 24 kHz **float32 PCM**.

If OmniVoice fails to load (e.g., missing GPU, OOM), the system falls back to **1 second of silence** so the pipeline remains testable end-to-end.

### 7. Blendshape Extraction (PantoMatrix)

`PantoMatrix` (`server/pantomatrix.py`) is the core animation engine. It analyzes raw PCM audio and produces 52 ARKit blendshape frames at 30 FPS.

**Algorithm:**

1. **Frame segmentation** — audio is divided into 30 FPS frames (~33 ms each)
2. **Feature extraction** — per frame: RMS energy, zero-crossing rate, spectral sub-band ratios (low < 500 Hz, mid 500-2000 Hz, high > 2000 Hz)
3. **Phoneme classification** — each frame maps to 1 of 7 classes:

| Class | Examples | Characteristic |
|---|---|---|
| Silence | — | Energy < 4% of peak |
| Low vowel | /a/ /ɑ/ /ɔ/ | High low-band energy |
| Mid vowel | /ɛ/ /ɪ/ /ʌ/ | Balanced mid-band |
| High vowel | /i/ /e/ /eɪ/ | Low energy, spread lips |
| Fricative | /v/ /ð/ /z/ | High ZCR, mid-high energy |
| Sibilant | /s/ /ʃ/ /f/ | Very high ZCR, high energy |
| Bilabial | /p/ /b/ /m/ | Low energy, closed lips |

4. **Pose mapping** — each phoneme class has a target ARKit blendshape pose (from 52 shapes)
5. **Linear cross-fade** — transitions between poses are blended over 3 frames (~100 ms) to avoid visual pops
6. **Energy scaling** — each pose is scaled by `sqrt(normalized_energy)` so mouth movement matches loudness
7. **Procedural overlays** — independent per-frame eye blinks (random interval ~2.5-5.5 s), gaze saccades (sinusoidal), brow motion correlated with energy, and jaw micro-tremor

PantoMatrix runs in `loop.run_in_executor()` — it does **not block the asyncio event loop**. The blendshape matrix is sent to the client before the `tts_end` signal.

### 8. Client Rendering (Three.js)

The `AvatarWidget` (`client/avatar-widget.js`) manages:

- **Audio playback** — PCM chunks are scheduled gaplessly via `AudioContext` `nextPlayTime` cursor
- **Animation interpolation** — the 30 FPS blendshape matrix is interpolated at display refresh rate (60+ Hz) using binary search + linear interpolation
- **Idle behavior** — when not speaking, the `AvatarWidget` (`client/avatar-widget.js`) drives procedural breathing, blinking, and gaze saccades modulated by the current emotion

**Morph target aliasing** — the render loop handles name mismatches between the PantoMatrix blendshape names and the GLB model's morph target names (case-insensitive, dot-suffix stripping, and `jawOpen` ↔ `mouthOpen` fallback).

---

## Concurrency Model

Five `asyncio` worker tasks run in parallel per session, connected by `asyncio.Queue` instances:

```
┌─────────────────────────────────────────────────────────────────────────┐
│                         PipelineOrchestrator                            │
│                                                                         │
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────┐              │
│  │  Audio I/O   │───▶│  Sentence    │───▶│  Pipeline    │───┐          │
│  │  (forward)   │    │  Buffer      │    │  (Translate) │   │          │
│  └──────────────┘    └──────────────┘    └──────────────┘   │          │
│                                                              ▼          │
│                                                    ┌──────────────┐     │
│                                                    │     TTS      │     │
│                                                    │  + PantoMat. │     │
│                                                    └──────┬───────┘     │
│                                                           │             │
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────┐ │             │
│  │  Monitor     │    │  Audio       │◄───│  TTS Queue   │◄┘             │
│  │  (heartbeat) │    │  Sender      │    └──────────────┘               │
│  └──────────────┘    └──────────────┘                                   │
└─────────────────────────────────────────────────────────────────────────┘
```

This enables **pipeline parallelism**: sentence N+1 can be translating while sentence N is still being synthesized and played, minimizing latency between turns.

---

## Key Latency Strategies

| Strategy | Description |
|---|---|
| **Sentence-level chunking** | `SentenceBuffer` detects boundaries — no waiting for full utterance |
| **Pipeline parallelism** | 5 async worker tasks run independently via `asyncio.Queue` |
| **In-process translation** | IndicTrans2 via CTranslate2 — no network round-trip |
| **Non-blocking blendshapes** | PantoMatrix runs in thread executor, event loop stays free |
| **Gapless audio playback** | Frontend schedules PCM chunks via `AudioContext` `nextPlayTime` cursor |
| **Cross-fade smoothing** | Phoneme transitions blend over 100 ms to prevent animation artifacts |

---

## External Dependencies

| Service | Role | Protocol | Default URL |
|---|---|---|---|
| Vexyl STT | Streaming speech-to-text | WebSocket | `ws://localhost:8080` |
| OpenAI-compatible LLM | Response generation | HTTP POST `/v1/chat/completions` | `http://localhost:8000/v1` |
| vLLM-Omni TTS | Alternative remote TTS | HTTP POST `/v1/audio/speech` | `http://localhost:8091/v1` |
| IndicTrans2 (CTranslate2) | Indic↔English translation | In-process | `~/.cache/ctranslate2/` |

---

## Component Reference

### Server

| File | Class / Function | Responsibility |
|---|---|---|
| `server/main.py` | FastAPI `app` | HTTP/WS endpoints, lifespan management, in-memory audio store |
| `server/pipeline_orchestrator.py` | `PipelineOrchestrator` | Full S2S pipeline orchestration per session |
| `server/pipeline_orchestrator.py` | `IndicTrans2Engine` | CTranslate2 Indic↔English translation (async, thread-safe) |
| `server/pipeline_orchestrator.py` | `SentenceEvent` / `TTSChunk` | Pipeline data types |
| `server/sentence_buffer.py` | `SentenceBuffer` | Streaming text segmentation with auto-flush timer |
| `server/local_tts.py` | `LocalTTS` | OmniVoice text-to-speech wrapper with silent fallback |
| `server/pantomatrix.py` | `extract_blendshapes()` | Audio→52 ARKit blendshapes, cross-fade smoothed |
| `server/download_models.py` | — | Pre-downloads IndicTrans2 CT2 models |

### Client

| File | Class | Responsibility |
|---|---|---|
| `client/avatar-widget.js` | `AvatarWidget` | Standalone widget: Three.js scene, emotion body/head, morph interpolation, procedural idle, WebSocket audio sync |

### Vexyl STT

| File | Responsibility |
|---|---|
| `server/vexyl_stt/vexyl_stt_server.py` | WebSocket streaming + batch REST transcription server |
| `server/vexyl_stt/Dockerfile` | Container — model downloads at startup into volume |
| `server/vexyl_stt/entrypoint.sh` | Startup model downloader (gated model, needs `HF_TOKEN` on first run) |

---

## WebSocket Session Lifecycle

```
Client                    Orchestrator              Vexyl STT
  │                           │                        │
  │── {type:"start"} ────────►│                        │
  │                           │── {type:"start"} ─────►│
  │                           │◄── {type:"started"} ───│
  │◄── {type:"transcript"} ───│◄── {type:"final"} ─────│
  │── [binary PCM] ─────────►│── [binary PCM] ────────►│
  │                           │◄── {type:"final"} ─────│
  │◄── {type:"transcript"} ───│                        │
  │                           │── [translate → LLM →   │
  │                           │    translate → TTS]    │
  │◄── {type:"tts_start"} ────│                        │
  │◄── {type:"audio_chunk"} ──│                        │
  │◄── [binary PCM] ──────────│                        │
  │◄── {type:"blendshapes"} ──│                        │
  │◄── {type:"tts_end"} ──────│                        │
  │── {type:"stop"} ─────────►│── {type:"stop"} ──────►│
  │                           │◄── {type:"stopped"} ───│
```

---

## Emotion System

Six emotions are implemented across all layers (server blendshape baking + client procedural animation):

| Emotion | Breath Speed | Breath Amp | Sway Amp | Head Bias Y | Blendshape Targets |
|---|---|---|---|---|---|
| `neutral` | 1.8 | 0.022 | 0.02 | 0.00 | — |
| `happy` | 2.4 | 0.030 | 0.035 | +0.04 | mouthSmile, cheekSquint, browOuterUp |
| `sad` | 1.2 | 0.015 | 0.008 | -0.06 | mouthFrown, browInnerUp, browDown |
| `angry` | 3.2 | 0.038 | 0.012 | 0.00 | browDown, eyeSquint, mouthFrown, noseSneer |
| `surprised` | 2.8 | 0.035 | 0.025 | +0.05 | eyeWide, browInnerUp, browOuterUp, mouthShrug |
| `fearful` | 2.2 | 0.028 | 0.010 | -0.03 | eyeWide, browInnerUp, browOuterUp, mouthFrown |

**During speech:** emotion is baked into the blendshape matrix server-side (via `_apply_emotion_to_matrix()` in `main.py`). Inline emotion tokens (`[laughter]`, `[sigh]`, `[surprise-oh]`, `[dissatisfaction-hnn]`) can also be embedded in the TTS text for per-phrase expression clamps.

**During idle:** `BehaviorManager.emotionWeights` are lerped at 5 rad/s, and body parameters (breathing speed, sway amplitude) transition at 2 rad/s.

---

## Latency Budget

Typical per-sentence latency breakdown (GPU, pipeline-parallel):

| Stage | Time | Parallel? |
|---|---|---|
| STT (Vexyl) | 200-400 ms | Overlaps with speaking |
| Indic→EN translation | 50-150 ms | Sequential per sentence |
| LLM generation | 200-1000 ms | Sequential per sentence |
| EN→Indic translation | 50-150 ms | Sequential per sentence |
| TTS (OmniVoice) | 300-800 ms | Overlaps with next sentence |
| PantoMatrix | 10-50 ms | Overlaps with streaming |
| Network | 20-50 ms | Per chunk |

End-to-end latency from stop speaking to first audio: **~600-1800 ms** depending on LLM speed and hardware.
