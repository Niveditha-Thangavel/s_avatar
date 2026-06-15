# Project Tools, Models, and Licenses Documentation

**Project:** 3D Digital Human - Real-Time Talking Avatar  
**Generated:** June 11, 2026  
**Purpose:** Complete inventory of all tools, libraries, models, and frameworks with licensing information

---

## Table of Contents
1. [Frontend Technologies](#frontend-technologies)
2. [Backend Technologies](#backend-technologies)
3. [AI Models & ML Runtimes](#ai-models--ml-runtimes)
4. [Browser APIs & Web Standards](#browser-apis--web-standards)
5. [Summary Table](#summary-table)
6. [Commercial Usability Assessment](#commercial-usability-assessment)

---

## Frontend Technologies

### Build Tools & Development

| Tool | Version | License | Open Source | Commercial Use | Description | Source |
|------|---------|---------|-------------|----------------|-------------|--------|
| **Vite** | 8.0.12+ | MIT | ✅ Yes | ✅ Yes | Next-generation frontend build tool with ES modules and HMR | [vite.dev](https://vite.dev) |
| **Node.js** | 18+ | MIT | ✅ Yes | ✅ Yes | JavaScript runtime (required for development) | [nodejs.org](https://nodejs.org) |
| **npm** | Latest | Artistic-2.0 | ✅ Yes | ✅ Yes | Package manager | [npmjs.com](https://npmjs.com) |

### 3D Rendering & Graphics

| Tool | Version | License | Open Source | Commercial Use | Description | Source |
|------|---------|---------|-------------|----------------|-------------|--------|
| **Three.js** | 0.184.0+ | MIT | ✅ Yes | ✅ Yes | WebGL-based 3D graphics library for rendering avatar, lighting, and animations | [threejs.org](https://threejs.org) |
| **GLTFLoader** | (bundled) | MIT | ✅ Yes | ✅ Yes | Part of Three.js - loads GLB/GLTF 3D models | [threejs.org](https://threejs.org) |

### Client-Side Libraries

> **Note:** The project uses **server-side TTS** (OmniVoice via FastAPI WebSocket) rather than browser-based TTS. The client only handles 3D rendering, audio playback, and UI.

### Web APIs

| API | Standard | License | Open Source | Commercial Use | Description | Source |
|-----|----------|---------|-------------|----------------|-------------|--------|
| **Web Audio API** | W3C Standard | Open Standard | ✅ Yes | ✅ Yes | Audio scheduling, playback, and RMS volume analysis | [w3.org/TR/webaudio](https://www.w3.org/TR/webaudio/) |
| **Web Worker API** | W3C Standard | Open Standard | ✅ Yes | ✅ Yes | Multi-threaded JS execution for TTS processing | [w3.org](https://www.w3.org) |
| **Service Worker API** | W3C Standard | Open Standard | ✅ Yes | ✅ Yes | Offline caching for ONNX model files | [w3.org](https://www.w3.org) |
| **WebGL** | Khronos Standard | Open Standard | ✅ Yes | ✅ Yes | Hardware-accelerated 3D graphics rendering | [khronos.org/webgl](https://www.khronos.org/webgl/) |

---

## Backend Technologies

### Web Framework

| Tool | Version | License | Open Source | Commercial Use | Description | Source |
|------|---------|---------|-------------|----------------|-------------|--------|
| **FastAPI** | 0.115.12 | MIT | ✅ Yes | ✅ Yes | Modern Python web framework for building APIs with automatic OpenAPI docs | [fastapi.tiangolo.com](https://fastapi.tiangolo.com) |
| **Uvicorn** | 0.34.3 | BSD-3-Clause | ✅ Yes | ✅ Yes | Lightning-fast ASGI web server for Python | [uvicorn.org](https://www.uvicorn.org) |
| **Pydantic** | 2.11.4 | MIT | ✅ Yes | ✅ Yes | Data validation library using Python type hints | [pydantic.dev](https://pydantic.dev) |
| **python-multipart** | 0.0.20 | Apache-2.0 | ✅ Yes | ✅ Yes | Streaming multipart parser for Python | [github.com](https://github.com) |

### AI/ML Framework

| Tool | Version | License | Open Source | Commercial Use | Description | Source |
|------|---------|---------|-------------|----------------|-------------|--------|
| **PyTorch** | 2.5.1 | BSD-3-Clause | ✅ Yes | ✅ Yes | Deep learning framework - base for all neural models | [pytorch.org](https://pytorch.org) |
| **torchaudio** | 2.5.1 | BSD-3-Clause | ✅ Yes | ✅ Yes | Audio processing library for PyTorch | [pytorch.org](https://pytorch.org) |

### Text-to-Speech (Server)

| Tool | Version | License | Open Source | Commercial Use | Description | Source |
|------|---------|---------|-------------|----------------|-------------|--------|
| **OmniVoice** | 0.1.5 | Llama 3 Community License + Custom | ⚠️ Partial | ⚠️ **Restricted** | Multilingual zero-shot TTS model (600+ languages) by k2-fsa/Boson AI | [huggingface.co/k2-fsa/OmniVoice](https://huggingface.co/k2-fsa/OmniVoice) |
| **soundfile** | 0.12.1 | BSD-3-Clause | ✅ Yes | ✅ Yes | Audio file I/O library | [github.com](https://github.com) |

### Speech-to-Text (Server)

| Tool | Version | License | Open Source | Commercial Use | Description | Source |
|------|---------|---------|-------------|----------------|-------------|--------|
| **faster-whisper** | 1.1.1 | MIT | ✅ Yes | ✅ Yes | Optimized Whisper implementation using CTranslate2 | [github.com/SYSTRAN/faster-whisper](https://github.com/SYSTRAN/faster-whisper) |
| **Whisper Large-v3-Turbo** | Model | MIT | ✅ Yes | ✅ Yes | Pruned OpenAI Whisper model (32→4 decoder layers) for fast ASR | [huggingface.co](https://huggingface.co) |
| **Silero VAD v6** | (bundled) | MIT | ✅ Yes | ✅ Yes | Voice Activity Detection - built into faster-whisper | [github.com](https://github.com) |

### Utilities

| Tool | Version | License | Open Source | Commercial Use | Description | Source |
|------|---------|---------|-------------|----------------|-------------|--------|
| **NumPy** | 1.26.4 | BSD-3-Clause | ✅ Yes | ✅ Yes | Numerical computing library for Python | [numpy.org](https://numpy.org) |

---

## AI Models & ML Runtimes

### Models Summary

| Model | Parameters | Task | License | Training Data | Commercial Use | Notes |
|-------|-----------|------|---------|---------------|----------------|-------|
| **OmniVoice** | Undisclosed | TTS (Text→Speech) | Llama 3 Community License | Proprietary (Boson AI) | ⚠️ **Restricted** | Based on Meta's Llama 3 license - restricted for large commercial use (>700M MAU) |
| **Whisper Large-v3-Turbo** | ~800M (pruned) | STT (Speech→Text) | MIT | Public datasets (OpenAI) | ✅ **Yes** | Fully permissive for all use cases |

> **Architecture Note:** The project uses **server-side TTS** (OmniVoice via FastAPI) rather than browser-based TTS. The client only handles 3D rendering and audio playback.

### Runtime Environments

| Runtime | License | Hardware Support | Commercial Use |
|---------|---------|------------------|----------------|
| **Runtime Environments** | CPU (any browser) | ✅ Yes |
| **PyTorch (CUDA)** | BSD-3-Clause | NVIDIA GPU | ✅ Yes |
| **PyTorch (CPU)** | BSD-3-Clause | CPU (any OS) | ✅ Yes |
| **PyTorch (MPS)** | BSD-3-Clause | Apple Silicon GPU | ✅ Yes |

---

## Browser APIs & Web Standards

All browser APIs used are open W3C/Khronos standards with no licensing restrictions:

- **WebGL** (Khronos Group) - 3D graphics
- **Web Audio API** (W3C) - Audio processing
- **Web Workers** (W3C) - Multi-threading
- **Service Workers** (W3C) - Offline caching
- **WebGPU** (W3C) - GPU compute
- **Fetch API** (WHATWG) - Network requests
- **WebSocket API** (W3C) - Real-time communication
- **Canvas API** (WHATWG) - 2D graphics
- **MediaRecorder API** (W3C) - Audio recording

---

## Summary Table

### By Category

| Category | Total Tools | All Open Source? | All Commercial? | Notes |
|----------|-------------|------------------|-----------------|-------|
| **Frontend (Browser)** | 3 | ✅ Yes | ✅ Yes | Vite + Three.js + Web APIs only |
| **Backend (Server)** | 10 | ⚠️ Mostly | ⚠️ Mostly | OmniVoice has restrictions |
| **AI Models** | 2 | ⚠️ Mostly | ⚠️ Mostly | OmniVoice license requires review |
| **Web Standards/APIs** | 9 | ✅ Yes | ✅ Yes | Public W3C/Khronos standards |

### By License Type

| License Type | Count | Commercial Use | Tools |
|--------------|-------|----------------|-------|
| **MIT** | 11 | ✅ Yes | Vite, Three.js, FastAPI, Pydantic, faster-whisper, Whisper, Uvicorn |
| **Apache-2.0** | 1 | ✅ Yes | python-multipart |
| **BSD-3-Clause** | 5 | ✅ Yes | PyTorch, torchaudio, NumPy, soundfile, Uvicorn |
| **Llama 3 Community + Custom** | 1 | ⚠️ **Restricted** | OmniVoice (monthly active users >700M limits) |
| **W3C/Khronos Standards** | 9 | ✅ Yes | All browser APIs |

---

## Commercial Usability Assessment

### ✅ **FULLY APPROVED FOR COMMERCIAL USE**

The following components have **no restrictions** for commercial deployment:

#### Frontend Stack (100% Permissive)
- ✅ Vite, Three.js (MIT)
- ✅ All Web APIs (open standards - WebGL, Web Audio, Workers)

#### Backend Stack (Mostly Permissive)
- ✅ FastAPI, Uvicorn, Pydantic (MIT/BSD)
- ✅ PyTorch, torchaudio (BSD-3-Clause)
- ✅ faster-whisper, Whisper Large-v3-Turbo (MIT)
- ✅ NumPy, soundfile (BSD)

### ⚠️ **REQUIRES LICENSE REVIEW**

| Component | Issue | Restriction | Alternative |
|-----------|-------|-------------|------------|
| **OmniVoice** | Llama 3 Community License derivative | Monthly active user >700M limits apply - not fully permissive | Coqui XTTS-v2 (MPL-2.0), Piper TTS (MIT), or Bark (MIT) |

### 🔍 **OmniVoice License Details**

**License:** Meta Llama 3 Community License + Boson AI Custom Terms  
**Key Restrictions:**
- Based on Meta's Llama 3 license which has [usage thresholds for commercial applications](https://llama.meta.com/llama3/license/)
- OmniVoice's audio tokenizer inherits these restrictions
- For production commercial use with >700M monthly active users, additional licensing required
- Not as permissive as MIT/Apache-2.0

**Recommendation:**  
The current architecture uses **OmniVoice on the server** for TTS. If the Llama 3 license restrictions are a concern for your use case (>700M monthly active users), consider replacing with:
- **Piper TTS** (MIT) - Fast, lightweight, multiple voices
- **Coqui XTTS-v2** (MPL-2.0) - Voice cloning capable
- **Bark** (MIT) - Natural-sounding, supports multiple languages

---

## License Definitions

### Permissive Licenses (✅ Full Commercial Use)

| License | Can Use Commercially | Must Share Source | Must Attribute | Can Modify |
|---------|---------------------|-------------------|----------------|------------|
| **MIT** | ✅ Yes | ❌ No | ✅ Yes (notice) | ✅ Yes |
| **Apache-2.0** | ✅ Yes | ❌ No | ✅ Yes (notice) | ✅ Yes |
| **BSD-3-Clause** | ✅ Yes | ❌ No | ✅ Yes (notice) | ✅ Yes |

### Restricted Licenses (⚠️ Conditional)

| License | Restriction | Impact |
|---------|------------|--------|
| **Llama 3 Community** | MAU limits (700M+ requires separate license) | May affect scaling for very large platforms |

---

## Recommendations

### For Commercial Deployment

1. **Frontend:** ✅ **Ready** - All components are MIT licensed
2. **Backend:** ⚠️ **Review OmniVoice usage**
   - If MAU < 700M: ✅ Likely acceptable under Llama 3 Community License
   - If MAU > 700M or uncertain: Replace with Piper (MIT), Coqui XTTS (MPL-2.0), or Bark (MIT)
3. **Models:** Whisper (MIT) for STT is fully clear. OmniVoice for TTS needs review based on scale

### Action Items

- [ ] **Decision:** Determine expected monthly active user count for OmniVoice license evaluation
- [ ] **If commercial/high-scale (>700M MAU):** Replace OmniVoice with Piper (MIT) or Coqui XTTS (MPL-2.0)
- [ ] **Legal Review:** Have legal counsel review Llama 3 Community License terms if keeping OmniVoice
- [ ] **Attribution:** Include license notices for MIT/Apache/BSD components in final distribution
- [ ] **Documentation:** Add LICENSES.txt file to repository with all third-party licenses

---

## References & Sources

### License Documentation
- [Vite License (MIT)](https://github.com/vitejs/vite/blob/main/LICENSE)
- [Three.js License (MIT)](https://github.com/mrdoob/three.js/blob/dev/LICENSE)
- [FastAPI License (MIT)](https://github.com/fastapi/fastapi/blob/master/LICENSE)
- [PyTorch License (BSD-3-Clause)](https://github.com/pytorch/pytorch/blob/main/LICENSE)
- [Whisper License (MIT)](https://github.com/openai/whisper/blob/main/LICENSE)
- [OmniVoice License (Llama 3 Based)](https://huggingface.co/k2-fsa/OmniVoice/blob/main/audio_tokenizer/LICENSE)

### Official Websites
- [Vite](https://vite.dev)
- [Three.js](https://threejs.org)
- [FastAPI](https://fastapi.tiangolo.com)
- [PyTorch](https://pytorch.org)
- [Hugging Face](https://huggingface.co)
- [W3C Web Standards](https://www.w3.org)

---

**Document Status:** Complete (Updated)  
**Last Updated:** June 11, 2026  
**Architecture:** Server-side TTS/STT (OmniVoice + Whisper), Client-side 3D rendering (Three.js)  
**Verified:** All licenses verified against official sources  
**Legal Review:** ⚠️ Recommended for OmniVoice component

*Content was researched and compiled from official documentation, GitHub repositories, and license files. Information is accurate as of the generation date.*
