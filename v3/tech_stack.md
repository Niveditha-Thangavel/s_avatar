# Technical Stack Description: 3D Digital Human

This document outlines the libraries, tools, and APIs utilized in the 3D Digital Human project to run server-side neural speech synthesis and GPU-accelerated character animation in the browser.

---

## 1. Core Runtime & Build Tools

*   **Vite (v8.0.12+)**: Handles the local development server, module resolution (ES Modules), hot module replacement (HMR), and minification during production builds.
*   **HTML5, Vanilla CSS, and JavaScript (ES6+)**: Structural base of the web interface. No heavy framework (like React or Vue) is used, ensuring extremely low loading times and direct DOM control.

---

## 2. 3D WebGL Rendering Pipeline

*   **Three.js (v0.184.0+)**: Manage high-level 3D operations including:
    *   **GLTFLoader**: Decodes and imports the rigged humanoid GLB model.
    *   **PerspectiveCamera**: Set up zoom limits, rotation boundaries, and automatic head-focus coordinates.
    *   **Directional & Ambient Lights**: Key, fill, and rim spotlights to build realistic studio reflections.
    *   **PMREMGenerator**: Generates physical-based rendering (PBR) reflections using a soft `RoomEnvironment` for organic-looking skin and eyes.
    *   **Skeletal Bones**: Locates and updates parent/child joint nodes (`LeftArm`, `LeftForeArm`, `LeftHand`, etc.) dynamically at 60 FPS.
    *   **Morph Targets (Blendshapes)**: Translates viseme weights directly to lip vertices for talking.

---

## 3. Server-Side Text-to-Speech & Speech-to-Text

*   **OmniVoice (v0.1.5)**: Multilingual zero-shot TTS model supporting 600+ languages from k2-fsa/OmniVoice.
    *   Generates 24kHz PCM Float32 audio on the Python server.
    *   Supports voice cloning (via reference audio) and voice design (via text attributes).
    *   Based on diffusion language model architecture.

*   **faster-whisper (v1.1.1)**: Fast Whisper implementation using CTranslate2 for server-side STT.
    *   Uses Whisper Large-v3-Turbo model (pruned from 32 to 4 decoder layers).
    *   Built-in Silero VAD v6 for voice activity detection.
    *   Supports 100+ languages with automatic detection.

*   **PyTorch (v2.5.1) + torchaudio**: Deep learning framework powering both TTS and STT models.
    *   Supports CUDA (NVIDIA GPU), MPS (Apple Silicon), and CPU inference.
    *   Provides tensor operations and model loading/inference.

---

## 4. Multi-Threaded Architecture

To prevent audio playback and network operations from blocking the 3D WebGL renderer, the system uses:

1.  **Main Thread**: Handles WebGL drawing, user interactions, UI updates, and audio playback.
2.  **Web Worker Threads**: Can be used for background processing if needed (currently not actively used since TTS is server-side).

---

## 5. Web Audio & Speech Synchronization

*   **Web Audio API**: Provides low-latency audio scheduling and playback.
    *   **AudioContext**: Manages audio state and playback timing.
    *   **AudioBufferSourceNode**: Queues and plays Float32 raw PCM chunks from server.
    *   **AnalyserNode**: Computes Root Mean Square (RMS) volume during playback to drive secondary speech animations (like head bobbing).

*   **Text-based Viseme Mapping**: Maps characters from the spoken text to viseme blendshapes using a simplified character-to-viseme table. Blends lip vertices using Oculus-standard shapes (e.g., `PP`, `FF`, `AA`).

---

## 6. Real-Time Communication

*   **WebSocket API**: Bidirectional communication between browser client and Python server.
    *   **TTS Endpoint (`/ws/tts`)**: Client sends text → Server streams audio chunks + text back.
    *   **STT Endpoint (`/ws/stt`)**: Client streams microphone audio → Server returns transcript + AI reply.
    *   Low-latency streaming for real-time voice interaction.

*   **FastAPI (v0.115.12)**: Modern async Python web framework.
    *   Automatic OpenAPI documentation.
    *   Native WebSocket support.
    *   High-performance async request handling.

*   **Uvicorn (v0.34.3)**: Lightning-fast ASGI server for serving FastAPI applications.

---

## 7. Offline Optimization & Caching

*   **Service Worker (`sw.js`)**: Proxies and intercepts browser asset requests.
    *   Caches static assets (HTML, CSS, JS, images) for instant offline loading.
    *   Uses stale-while-revalidate strategy for optimal performance.

---

## 8. Audio Processing

*   **soundfile (v0.12.1)**: Python library for reading/writing audio files.
*   **NumPy (v1.26.4)**: Numerical computing for audio buffer manipulation and processing.

---

## Summary

### Client-Side Stack
- **Build**: Vite 8
- **3D**: Three.js 0.184
- **Audio**: Web Audio API
- **Network**: WebSocket
- **Caching**: Service Worker

### Server-Side Stack
- **Framework**: FastAPI + Uvicorn
- **TTS**: OmniVoice 0.1.5
- **STT**: faster-whisper 1.1.1 (Whisper Large-v3-Turbo)
- **ML**: PyTorch 2.5.1 + torchaudio
- **Audio**: soundfile + NumPy

This architecture separates compute-intensive AI operations (TTS/STT) on the server while keeping the client lightweight and focused on real-time 3D rendering and audio playback.
