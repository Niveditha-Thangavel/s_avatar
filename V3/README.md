# S2S Voice Avatar Server & Integration Guide (v3.0)

This repository contains the low-latency Speech-to-Speech (S2S) Voice Avatar backend orchestrator, along with the lightweight JavaScript client library for embedding and animating the 3D human head with Viseme-based ARKit lip-sync.

---

## 1. Directory Structure

```
V3/
├── server/                    # Python FastAPI S2S Orchestrator
│   ├── main.py                # FastAPI server (websockets + API endpoints)
│   ├── pipeline_orchestrator.py # Pipeline S2S flow & translation coordination
│   ├── sentence_buffer.py     # Streaming text sentence segmenter
│   ├── local_tts.py           # Local OmniVoice TTS inference wrapper
│   ├── pantomatrix.py         # viseme & ARKit blendshape generator
│   └── test_stack.py          # Dashboard script for checking stack health
├── client/                    # Client integration & widget library
│   ├── avatar-widget.js       # Main Three.js loader & blendshape driver
│   ├── widget-demo.html       # Standalone demo for testing integration
│   └── src/
│       ├── stt.js             # S2SManager WebSocket client & mic streamer
│       ├── avatar3d.js        # Three.js 3D avatar scene coordinator
│       └── behavior.js        # Procedural idle behavior (gaze, breathing, blinking)
└── vexyl_stt_src/             # Standalone Vexyl STT engine Docker container
```

---

## 2. Server Setup & Running Guide

The server coordinates three core engines:
1.  **Vexyl STT**: Live streaming speech-to-text (WebSocket) on port `8080`.
2.  **IndicTrans2 (CTranslate2)**: English <-> Indic text translation (ran in-process on CPU or GPU).
3.  **Local TTS (OmniVoice)**: Generates high-quality synthesized speech and passes raw audio to PantoMatrix to extract ARKit blendshapes.

### Running with Docker Compose (Recommended)

1.  **Configure environment variables**:
    Create a `.env` file in the root directory:
    ```bash
    HF_TOKEN=your_huggingface_write_token  # Required to download IndicTrans2 & OmniVoice models
    ```
2.  **Start the stack (CPU Mode)**:
    ```bash
    docker compose up --build -d
    ```
3.  **Start the stack (GPU Mode - Recommended for low-latency)**:
    ```bash
    docker compose -f docker-compose.yml -f docker-compose.gpu.yml up --build -d
    ```

This exposes:
*   **Orchestrator HTTP/WS Server**: `http://localhost:8765`
*   **Vexyl STT WebSocket Server**: `ws://localhost:8080`

### Running the Python Server Locally (For Debugging)

1.  **Create and activate virtual environment**:
    ```bash
    cd server
    python3 -m venv venv
    source venv/bin/activate
    pip install -r requirements.txt
    ```
2.  **Pre-download the IndicTrans2 CTranslate2 Models**:
    ```bash
    python download_models.py
    ```
3.  **Start uvicorn server**:
    ```bash
    uvicorn main:app --host 0.0.0.0 --port 8765 --loop asyncio
    ```

### Validating Server Status
You can run the validation dashboard to verify STT, translation engines, and TTS models are fully loaded:
```bash
python server/test_stack.py
```

---

## 3. Client Integration Guide

To embed the 3D Avatar and connect it to the low-latency Speech-to-Speech WebSocket, follow these steps:

### A. Load Three.js and Import the Widget
Configure an import map for Three.js (r184 recommended) and import `AvatarWidget` and `S2SManager`:

```html
<script type="importmap">
{
  "imports": {
    "three": "https://unpkg.com/three@0.184.0/build/three.module.js",
    "three/addons/": "https://unpkg.com/three@0.184.0/examples/jsm/"
  }
}
</script>

<div id="avatar-container" style="width: 500px; height: 500px;"></div>

<script type="module">
  import { AvatarWidget } from './client/avatar-widget.js';
  import { S2SManager } from './client/src/stt.js';

  // 1. Initialize the 3D human head
  const widget = new AvatarWidget({
    container: document.getElementById('avatar-container'),
    modelUrl:  '/avatar_head.glb', // Path to the rigged GLB model
    onReady:   () => console.log('Avatar loaded & ready'),
  });
</script>
```

### B. Connect S2SManager to S2S WebSocket
Create the speech manager and register event callbacks to drive the avatar dynamically:

```javascript
// Connect S2SManager to the orchestrator ws endpoint
const s2s = new S2SManager('ws://localhost:8765/ws/s2s');

// Real-time user transcript is received
s2s.onTranscript = (text, lang) => {
  console.log(`[STT] User said (${lang}):`, text);
};

// Avatar speech starts
s2s.onTtsStart = (seq, text) => {
  console.log(`[TTS] Speaking response sentence:`, text);
};

// Audio chunks are received and scheduled sequentially
s2s.onAudioChunk = (seq, pcmBytes, sampleRate) => {
  // s2s handles the audio playback queue automatically
};

// ARKit viseme matrix is received for lip-sync animation
s2s.onBlendshapeMatrix = (seq, matrix) => {
  // Pass the blendshape timeline straight to the avatar widget
  widget.setAnimationMatrix(matrix);
};

// Avatar finishes speaking
s2s.onTtsEnd = (seq) => {
  // Clear animation and return avatar to idle pose
  widget.clearAnimation();
};

s2s.onError = (err) => console.error(err);
```

### C. Trigger Recording
Call `s2s.start(lang)` to acquire the microphone and stream audio:

```javascript
// Start listening (default language: Hindi)
await s2s.start('hi-IN');

// Stop recording when user finishes speaking (starts processing translation & TTS)
s2s.stop();

// Interrupt / abort the session immediately
s2s.cancel();
```

---

## 4. Supported Languages (BCP-47)
Pass one of these language codes into `s2s.start()`:

| BCP-47 Code | Language |
|---|---|
| `hi-IN` | Hindi |
| `ta-IN` | Tamil |
| `te-IN` | Telugu |
| `ml-IN` | Malayalam |
| `kn-IN` | Kannada |
| `mr-IN` | Marathi |
| `gu-IN` | Gujarati |
| `bn-IN` | Bengali |
| `pa-IN` | Punjabi |
| `or-IN` | Odia |
| `as-IN` | Assamese |
| `ur-IN` | Urdu |

---

## 5. Procedural Emotion System

The widget supports 5 procedural expressions: `neutral`, `happy`, `sad`, `angry`, `surprised`. Changing the emotion drives secondary animations like head sways, eye gazes, blinking rates, and breathing speed.

```javascript
// Force the avatar to look happy
widget.setEmotion('happy');

// Revert to neutral
widget.setEmotion('neutral');
```
