# Architecture

## Overview

The S2S Voice Avatar is a real-time Speech-to-Speech pipeline that converts a user's spoken input in an Indian language into a synthesized local voice response delivered by a 3D animated avatar with precise lip-sync.

```
[Client Audio Stream]
       │ (16kHz Int16 PCM via WebSocket)
       ▼
┌──────────────┐
│  Vexyl STT   │ ──► [ASR Decodes Speech]
└──────────────┘
       │
       ▼
┌──────────────┐
│  Server      │
│  Orchestrator│
│  (FastAPI)   │ ──► [Select Static English Response]
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

## Pipeline Stages

### 1. Audio Ingestion (Client → Server)

The browser captures microphone audio at 16 kHz mono via an `AudioWorkletProcessor`. Samples are converted from float32 to **Int16 PCM** and streamed over a WebSocket to the orchestrator server.

### 2. Speech-to-Text (Vexyl STT)

The orchestrator relays audio to Vexyl STT — a standalone WebSocket service wrapping the [ai4bharat/indic-conformer-600m-multilingual](https://huggingface.co/ai4bharat/indic-conformer-600m-multilingual) model (600M parameters). Vexyl provides:
- **Energy-based VAD** — silence threshold and duration detection.
- **Real-time streaming** — returns `final` transcript messages as speech is detected.
- **14 Indian languages** supported.

### 3. Sentence Buffering

The `SentenceBuffer` (`server/sentence_buffer.py`) accumulates streaming transcript fragments and emits complete sentences when it detects boundaries (`. ? ! | ।`). An auto-flush timer (0.8 seconds) ensures partial utterances don't stall the pipeline — if no new text arrives within the window, the buffer emits whatever it has.

### 4. Translation (IndicTrans2 via CTranslate2 with Caching)

The system selects a static response in English and translates it directly to the target Indic language using the `en-indic-1B` model:
- The translation executes **in-process** on GPU using `sentencepiece` tokenizers.
- **Caching**: The translated response is cached locally in memory. Subsequent hits for the same target language bypass the translation engine entirely, achieving **0ms latency** on translation.
- **Post-Processing Transliteration**: Non-Devanagari languages (like Tamil, Telugu, Kannada) undergo native script post-processing so that IndicTrans2's unified Devanagari output is mapped back to the native target language script.

### 5. Text-to-Speech (Local TTS)

`LocalTTS` (`server/local_tts.py`) wraps `k2-fsa/OmniVoice` for local, high-speed audio generation on the GPU. It uses a **reference audio sample** for voice characteristics, producing 24 kHz **float32 PCM** streams. 

If LocalTTS fails to load (e.g. missing GPU), the system falls back to **1 second of silence** so the pipeline remains functional for visual lipsync tests.

### 6. Blendshape Extraction (PantoMatrix)

`PantoMatrix` (`server/pantomatrix.py`) is the core animation engine. It analyzes raw PCM audio and produces 52 ARKit blendshape frames at 30 FPS.

**Algorithm:**
1. **Frame segmentation** — audio is divided into 30 FPS frames (~33 ms each).
2. **Feature extraction** — Zero-crossing rate, RMS energy, and spectral sub-band ratios.
3. **Phoneme classification** — each frame maps to 1 of 7 viseme classes (Silence, Low Vowel, Mid Vowel, High Vowel, Fricative, Sibilant, Bilabial).
4. **Pose mapping** — maps visemes to combinations of 52 morph target weights.
5. **Linear cross-fade** — blends target poses over 3 frames to avoid visual pops.
6. **Energy scaling** — scales mouth movements dynamically with volume.
7. **Procedural overlays** — eye blinking, gaze saccades, brow movement, and jaw micro-tremors.

PantoMatrix runs in `loop.run_in_executor()` — it does **not block the asyncio event loop**.

### 7. Client Rendering (Three.js / React)

The client (`tester-app` or embeddable `AvatarWidget` widget) manages:
- **Audio playback** — PCM chunks are scheduled gaplessly via `AudioContext`.
- **Animation interpolation** — the 30 FPS blendshape matrix is interpolated at display refresh rate (60+ Hz) using binary search + linear interpolation.
- **Diagnostics Panel**: Real-time blendshapes visualization, manual sliders override, and the **Latency Monitor** providing detailed breakdowns of processing steps.

---

## Concurrency Model

Four `asyncio` worker tasks run in parallel per session, connected by `asyncio.Queue` instances:

```
┌─────────────────────────────────────────────────────────┐
│                 PipelineOrchestrator                    │
│                                                         │
│  ┌──────────────┐    ┌──────────────┐                   │
│  │  Audio I/O   │───▶│  Sentence    │                   │
│  │  (forward)   │    │  Buffer      │                   │
│  └──────────────┘    └──────┬───────┘                   │
│                             │                           │
│                             ▼                           │
│                      ┌──────────────┐                   │
│                      │  Pipeline    │                   │
│                      │  (Translate) │                   │
│                      └──────┬───────┘                   │
│                             │                           │
│                             ▼                           │
│                      ┌──────────────┐                   │
│                      │     TTS      │                   │
│                      │  + PantoMat. │                   │
│                      └──────┬───────┘                   │
│                             │                           │
│                             ▼                           │
│                      ┌──────────────┐                   │
│                      │  Audio       │                   │
│                      │  Sender      │                   │
│                      └──────────────┘                   │
└─────────────────────────────────────────────────────────┘
```

This enables **pipeline parallelism**: sentence N+1 can be translating while sentence N is still being synthesized and played, minimizing latency between turns.

---

## Key Latency Strategies

| Strategy | Description |
|---|---|
| **Sentence-level chunking** | `SentenceBuffer` detects boundaries — no waiting for full utterance |
| **Pipeline parallelism** | Multiple async worker tasks run independently via queues |
| **In-process translation** | IndicTrans2 via CTranslate2 — no network round-trip |
| **Translation Caching** | English-to-Indic translations cached in-memory for 0ms lookup |
| **GPU Autocasting** | Mixed-precision FP16 evaluation on CUDA-enabled layers |
| **CUDA Base Images** | Pre-built wheels for PyTorch 12.4 to bypass CPU fallback |
| **Non-blocking blendshapes** | PantoMatrix runs in thread executor, event loop stays free |
| **Gapless audio playback** | Frontend schedules PCM chunks via `AudioContext` timeline |

---

## External Dependencies

| Service | Role | Protocol | Default URL |
|---|---|---|---|
| Vexyl STT | Streaming speech-to-text | WebSocket | `ws://localhost:8080` |
| IndicTrans2 (CTranslate2) | Indic↔English translation | In-process | `/root/.cache/ctranslate2/` |
| LocalTTS (OmniVoice) | Text-to-speech engine | In-process | Gated inside backend |
