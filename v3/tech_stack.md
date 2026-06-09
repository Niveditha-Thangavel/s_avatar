# Technical Stack Description: 3D Digital Human

This document outlines the libraries, tools, and browser APIs utilized in the browser-based 3D Digital Human project to run local neural speech synthesis and GPU-accelerated character animation.

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

## 3. Local Text-to-Speech (Neural Net)

*   **Kokoro.js (v1.2.1+) & @huggingface/transformers (v4.2.0+)**: Runs local Text-to-Speech models directly inside the browser using ONNX Runtime Web.
    *   **Kokoro-82M ONNX**: A highly compressed, high-fidelity neural model.
    *   **Quantization (4-bit / 8-bit)**: Reduces model asset download size from ~170MB down to ~45MB, allowing fast, client-side inference on standard consumer CPUs.
    *   **ONNX Runtime WebAssembly (WASM)**: Standard fallback that compiles neural networks into high-speed WASM code.
    *   **WebGPU**: Enabled as a config option for GPU-accelerated speech synthesis.

---

## 4. Multi-Threaded Architecture

To prevent CPU speech generation from blocking the 3D WebGL renderer (which causes screen stuttering/stretching), the system uses a dual-thread setup:

1.  **Main Thread**: Handles WebGL drawing, user clicks, audio playback queues, and breathing sways.
2.  **Web Worker Thread (`tts.worker.js`)**: Executes the heavy neural net inference, generating raw float speech buffers in the background, and posting them back in small chunks.

---

## 5. Web Audio & Speech Synchronization

*   **Web Audio API**: Provides low-latency audio scheduling and streaming.
    *   **AudioContext**: Manages state, resuming on user gestures.
    *   **AudioBufferSourceNode**: Queues and plays Float32 raw PCM chunks.
    *   **AnalyserNode**: Computes Root Mean Square (RMS) volume during playback to drive secondary speech animations (like head bobbing).
*   **IPA (International Phonetic Alphabet) Viseme Alignment**: Matches phonemes with precise timelines, blending lip vertices using Oculus-standard shapes (e.g., `PP`, `FF`, `AA`).

---

## 6. Offline Optimization & Caching

*   **Service Worker (`sw.js`)**: Proxies and intercepts browser asset requests.
    *   **Hugging Face ONNX Cache**: Automatically saves downloaded ONNX model weights and tokenizer configs locally, allowing instant boot-up on subsequent loads.
