# 3D Digital Human — Real-Time Talking Avatar

A browser-based 3D digital human with server-side neural Text-to-Speech (OmniVoice) and Speech-to-Text (Whisper), real-time lip-sync, and procedural animations powered by Three.js WebGL.

---

## Overview

This project renders a rigged 3D humanoid avatar in real-time WebGL and drives it with synchronized facial expressions, lip movements, blinking, gaze saccades, and breathing. Voice synthesis is handled server-side using OmniVoice (600+ languages), with audio streamed to the browser for playback and lip-sync.

![Avatar Portrait](public/avatar_portrait.png)

---

## Features

### **Client (Browser)**
- **3D WebGL Rendering** — Real-time avatar rendering using Three.js with PBR lighting
- **Real-Time Lip Sync** — Text-based viseme mapping with LERP-based coarticulation smoothing
- **ARKit / Oculus Blendshapes** — Supports 52+ facial morph targets for mouth, eyes, cheeks, and brows
- **Procedural Animations** — Breathing sine waves, Markovian blink state machine, saccadic eye movements, and speech-driven head bobbing
- **Custom GLB Upload** — Drop in any Ready Player Me or Mixamo-rigged `.glb` / `.gltf` avatar at runtime
- **Posture Calibration** — Interactive sliders for arm, forearm, and wrist bones with live code export

### **Server (Python)**
- **OmniVoice TTS** — Multilingual zero-shot text-to-speech supporting 600+ languages
- **Voice Design** — Customize voice attributes (gender, age, accent) via text prompts
- **Whisper Large-v3-Turbo STT** — Fast, accurate speech-to-text with automatic language detection
- **Real-Time Streaming** — WebSocket-based audio streaming for low-latency playback
- **FastAPI Backend** — Modern async Python web framework with automatic API docs

---

## Tech Stack

### **Frontend (Client)**
| Layer | Technology |
|---|---|
| Build Tool | Vite 8 (ES Modules, HMR) |
| 3D Rendering | Three.js 0.184 (WebGL) |
| Audio | Web Audio API (AudioContext, AnalyserNode) |
| Threading | Web Worker (dedicated worker threads) |
| Caching | Service Worker (offline-first asset caching) |
| Language | Vanilla JavaScript (ES6+), HTML5, CSS3 |

### **Backend (Server)**
| Layer | Technology |
|---|---|
| Web Framework | FastAPI 0.115 + Uvicorn (async ASGI server) |
| TTS Engine | OmniVoice 0.1.5 (k2-fsa/OmniVoice) |
| STT Engine | faster-whisper 1.1.1 (Whisper Large-v3-Turbo) |
| ML Framework | PyTorch 2.5.1 + torchaudio |
| Audio I/O | soundfile, NumPy |
| Communication | WebSocket (real-time audio/text streaming) |

---

## Project Structure

```
v3/
├── public/
│   ├── avatar_head.glb        # Default rigged humanoid 3D model
│   ├── avatar_portrait.png    # Preview image
│   ├── sw.js                  # Service Worker (offline asset caching)
│   ├── favicon.svg
│   └── icons.svg
├── src/
│   ├── main.js                # App controller — render loop, UI wiring
│   ├── avatar3d.js            # Three.js scene, GLB loader, bone/morph target control
│   ├── lipsync.js             # Audio scheduling, text-based viseme smoothing
│   ├── behavior.js            # Breathing, blinking, gaze saccades, emotion weights
│   ├── tts_client.js          # WebSocket TTS client (connects to server)
│   ├── stt.js                 # WebSocket STT client (microphone → transcription)
│   └── style.css              # App styles
├── server/
│   ├── main.py                # FastAPI server with WebSocket endpoints
│   ├── tts_engine.py          # OmniVoice wrapper (server-side TTS)
│   ├── stt_engine.py          # faster-whisper wrapper (server-side STT)
│   ├── chat.py                # Placeholder LLM response layer
│   ├── requirements.txt       # Python dependencies
│   └── start.sh               # Server startup script
├── index.html                 # Entry point with controls sidebar and WebGL viewport
├── main.js                    # Vite entry point
├── vite.config.js             # Vite config (COOP/COEP headers)
└── package.json
```

---

## Getting Started

### Prerequisites

- **Node.js** 18+ (for frontend)
- **Python** 3.10+ (for backend)
- **PyTorch** 2.5.1 (with CUDA 12.4 for GPU, or CPU/MPS for Mac)

### Install & Run

#### **1. Frontend (Client)**

```bash
# Install dependencies
npm install

# Start development server
npm run dev
```

Open `http://localhost:3005` in your browser.

#### **2. Backend (Server)**

```bash
cd server

# Install PyTorch (choose one based on your hardware):
# NVIDIA GPU:
pip install torch==2.5.1 torchaudio==2.5.1 --index-url https://download.pytorch.org/whl/cu124

# Apple Silicon (MPS):
pip install torch==2.5.1 torchaudio==2.5.1

# CPU only:
pip install torch==2.5.1 torchaudio==2.5.1 --index-url https://download.pytorch.org/whl/cpu

# Install other dependencies
pip install -r requirements.txt

# Start server
./start.sh
# or: uvicorn main:app --host 0.0.0.0 --port 8765 --reload
```

Server runs on `http://localhost:8765`

### Build for Production

```bash
npm run build
npm run preview
```

---

## Architecture

The application uses a client-server architecture with WebSocket communication for real-time audio streaming.

```
[Browser Client]                          [Python Server]
      │                                         │
      │  1. User types text                     │
      ├──────────────────────────────────────>  │
      │     WebSocket: /ws/tts                  │
      │                                          │
      │                          2. OmniVoice generates audio
      │                             (24kHz PCM float32)
      │                                          │
      │  <──────────────────────────────────────┤
      │     3. Stream audio chunks + text       │
      │                                          │
   4. Play audio                                │
   5. Update lip-sync                           │
   6. Render 3D avatar                          │
      │                                          │
      │  7. User speaks (microphone)            │
      ├──────────────────────────────────────>  │
      │     WebSocket: /ws/stt                  │
      │     (stream raw PCM Int16)              │
      │                                          │
      │                          8. Whisper transcribes
      │                          9. Generate reply
      │                                          │
      │  <──────────────────────────────────────┤
      │     10. Return transcript + reply       │
      │                                          │
   11. Display + speak reply                    │
```

### Key Components

**`avatar3d.js` — Avatar3D**  
Loads the GLB model, sets up the Three.js scene with PBR lighting and orbit controls, locates skeleton bones by name, and applies blendshape weights every frame.

**`tts_client.js` — TTSClient**  
WebSocket client that sends text to the server's `/ws/tts` endpoint and receives streamed PCM audio chunks. Forwards audio to the lip-sync manager for playback.

**`stt.js` — STTManager**  
Captures microphone input, streams raw PCM audio to the server's `/ws/stt` endpoint, and receives transcription + AI reply.

**`lipsync.js` — LipSyncManager**  
Schedules audio chunks on the Web Audio API timeline, maps text characters to viseme blendshapes, and smoothly interpolates morph weights each frame.

**`behavior.js` — BehaviorManager**  
Generates all secondary life-like motion: sinusoidal breathing sways, a 6-state blink machine, randomized saccadic gaze shifts, and RMS-volume-driven head bobbing.

**`server/tts_engine.py` — OmniVoice TTS**  
Wraps the k2-fsa/OmniVoice model with async streaming interface. Supports voice cloning (via ref_audio.wav) and voice design (via text attributes).

**`server/stt_engine.py` — Whisper STT**  
Wraps faster-whisper (Whisper Large-v3-Turbo) with async transcription. Accumulates audio buffers and uses Whisper's built-in Silero VAD for silence detection.

---

## Configuration

### **Voice Settings**

| Parameter | Options | Description |
|---|---|---|
| **Voice Design** | Text prompt | Customize voice: "female, young adult, american accent" |
| **Speed** | 0.5 - 2.0× | Speech rate multiplier |
| **Quality** | 16 / 32 steps | Inference steps (16=fast, 32=high quality) |

### **Speech-to-Text**

| Parameter | Options | Description |
|---|---|---|
| **Language** | Auto-detect or specific | en, ar, fr, de, es, zh, ja, ko, pt, ru, etc. |

---

## Custom Avatar

Any `.glb` / `.gltf` model with a standard Mixamo or Ready Player Me rig will work. The loader automatically:

1. Detects bones by name suffix (`Head`, `Neck`, `LeftArm`, `RightForeArm`, etc.)
2. Discovers morph target dictionaries on all mesh nodes
3. Falls back gracefully when optional bones or blendshapes are missing

Click **Upload GLB Model** in the sidebar to load a custom avatar at runtime, or **Reset Default** to revert to `avatar_head.glb`.

### Posture Calibration

If the uploaded avatar's arms look unnatural, use the **Posture Calibration** panel to adjust the X/Y/Z rotation offsets for each arm/forearm/wrist bone in real time. Once satisfied, copy the generated code snippet and paste it into `avatar3d.js` as the default `avatarCalibration` values.

---

## Lip-Sync Pipeline

```
Text → Sent to server
     → OmniVoice generates audio (PCM Float32)
     → Stream to client with text
     → Text character mapping → Viseme shapes
     → Exponential LERP coarticulation → Blendshape morph targets
```

Supported visemes: `viseme_sil`, `viseme_PP`, `viseme_FF`, `viseme_TH`, `viseme_DD`, `viseme_kk`, `viseme_CH`, `viseme_SS`, `viseme_nn`, `viseme_RR`, `viseme_aa`, `viseme_E`, `viseme_I`, `viseme_O`, `viseme_U`

---

## Emotions

Five preset emotion states drive facial blendshape combinations:

| Emotion | Active Blendshapes |
|---|---|
| Neutral | — |
| Happy 😊 | mouthSmile, cheekSquint, browOuterUp |
| Sad 😢 | mouthFrown, browInnerUp, browDown |
| Angry 😠 | browDown, eyeSquint, mouthFrown |
| Surprised 😲 | eyeWide, browInnerUp, browOuterUp, mouthOpen |

All transitions are smoothly interpolated using exponential LERP at 5 rad/s.

---

## Browser Requirements

- **Modern Browser** — Chrome 113+, Edge 113+, Firefox 115+, Safari 16+
- **WebGL 2.0** — For 3D rendering
- **Web Audio API** — For audio playback
- **WebSockets** — For server communication
- **Microphone Access** — For speech-to-text (optional)

---

## Dependencies

### Frontend
```json
{
  "three": "^0.184.0"
}
```

### Backend
```
fastapi==0.115.12
uvicorn[standard]==0.34.3
omnivoice==0.1.5
faster-whisper==1.1.1
torch==2.5.1
torchaudio==2.5.1
soundfile==0.12.1
numpy==1.26.4
```

---

## License

Private project — Simplyfi © 2025

---

## Troubleshooting

### Server won't start
- Ensure PyTorch is installed correctly for your hardware
- Check Python version (3.10+ required)
- Verify all dependencies installed: `pip install -r requirements.txt`

### No audio playback
- Check browser console for Web Audio API errors
- Ensure server is running on `localhost:8765`
- Verify WebSocket connection in Network tab

### Lip-sync looks wrong
- The current implementation uses simplified text-based visemes
- For accurate phoneme-based sync, server would need to return IPA phonemes from OmniVoice

### Avatar arms in wrong position
- Use the Posture Calibration panel to adjust bone rotations
- Copy the generated code and update `avatarCalibration` in `avatar3d.js`

---

## Roadmap

- [ ] Add LLM integration (replace placeholder `chat.py`)
- [ ] Implement IPA phoneme extraction from OmniVoice for accurate lip-sync
- [ ] Add emotion detection from voice/text
- [ ] Support multiple avatar voices/personas
- [ ] Add conversation history/context
- [ ] Deploy to production with HTTPS/WSS
