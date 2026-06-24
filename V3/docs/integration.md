# Integration Guide

Embed the S2S Voice Avatar into any existing web application using the provided JavaScript widget and WebSocket client.

---

## Installation

No build step or npm package required. The widget loads Three.js via importmap at runtime.

### 1. Set Up the Import Map

```html
<script type="importmap">
{
  "imports": {
    "three": "https://unpkg.com/three@0.184.0/build/three.module.js",
    "three/addons/": "https://unpkg.com/three@0.184.0/examples/jsm/"
  }
}
</script>
```

### 2. Add the Container

```html
<div id="avatar-container" style="width: 400px; height: 500px;"></div>
```

### 3. Load the Avatar Widget & S2S Manager

```html
<script type="module">
import { AvatarWidget } from './client/avatar-widget.js';
import { S2SManager } from './client/src/stt.js';

// Initialize the 3D avatar
const widget = new AvatarWidget({
  container: document.getElementById('avatar-container'),
  modelUrl: '/avatar_head.glb',
  calibration: {
    laX: -1.82, laY: -2.42, laZ: 3.14,
    raX: -1.82, raY: 2.62, raZ: -3.14,
    lfX: 1.10, lfY: 0.00, lfZ: -0.20,
    rfX: 1.12, rfY: 0.00, rfZ: 0.14,
    lhX: -0.10, lhY: 1.66, lhZ: 0.26,
    rhX: -0.18, rhY: -1.66, rhZ: -0.26,
  },
  onReady: () => console.log('Avatar ready'),
});

// Connect to the S2S pipeline
const s2s = new S2SManager('ws://localhost:8765/ws/s2s');

// Wire up callbacks
s2s.onTranscript = (text, lang) => {
  console.log(`User said (${lang}):`, text);
};

s2s.onTtsStart = (seq, text) => {
  console.log(`Speaking response:`, text);
};

s2s.onAudioChunk = (seq, pcmBytes, sampleRate) => {
  // Audio playback is handled automatically by S2SManager
};

s2s.onBlendshapeMatrix = (seq, matrix) => {
  widget.setAnimationMatrix(matrix);
};

s2s.onTtsEnd = (seq) => {
  widget.clearAnimation();
};

s2s.onError = (err) => console.error(err);
</script>
```

### 4. Start / Stop Recording

```javascript
// Start listening (default: Hindi)
await s2s.start('hi-IN');

// Stop — triggers translation + TTS generation
s2s.stop();

// Abort immediately
s2s.cancel();
```

---

## API Reference

### `AvatarWidget`

The main entry point for the 3D avatar. Import from `client/avatar-widget.js`.

**Constructor**

```javascript
new AvatarWidget({
  container: HTMLElement,          // required — DOM element to render into
  modelUrl: string,                // default: '/avatar_head.glb'
  serverUrl: string|null,          // optional — server base URL
  onReady: function|null,          // optional — callback when avatar loads
  calibration: object|null,        // optional — arm posture calibration
})
```

**Methods**

| Method | Description |
|---|---|
| `setAnimationMatrix(matrix)` | Apply PantoMatrix blendshape timeline for lip-sync animation |
| `clearAnimation()` | Reset all morph targets to zero, return to idle pose |
| `setEmotion(emotion)` | Set idle expression — `neutral`, `happy`, `sad`, `angry`, `surprised` |
| `syncAudio(audioContext, startTime)` | Synchronize animation clock with AudioContext for precise lip-sync |
| `dispose()` | Clean up Three.js resources, cancel animation frame |

### `S2SManager`

WebSocket client for the S2S pipeline. Import from `client/src/stt.js`.

**Constructor**

```javascript
new S2SManager(wsUrl)
```

`wsUrl` — Full WebSocket URL, e.g. `ws://localhost:8765/ws/s2s`

**Methods**

| Method | Description |
|---|---|
| `start(lang, sessionId?)` | Open WebSocket, request mic access, stream audio |
| `stop()` | End recording, signal server to finish processing |
| `cancel()` | Abort session immediately, close WebSocket |
| `mute()` | Mute microphone (used during avatar speech) |
| `unmute()` | Re-enable microphone after speech |

**Callbacks**

Set these before calling `start()`:

| Callback | Signature | Description |
|---|---|---|
| `onTranscript` | `(text: string, lang: string)` | Real-time STT transcript |
| `onTtsStart` | `(seq: number, text: string)` | Sentence TTS begins |
| `onAudioChunk` | `(seq: number, pcmBytes: ArrayBuffer, sampleRate: number)` | Raw PCM audio data |
| `onTtsEnd` | `(seq: number)` | Sentence fully transmitted |
| `onBlendshapeMatrix` | `(seq: number, matrix: object[])` | ARKit blendshape frames |
| `onStatusChange` | `(status: string)` | Connection state changes |
| `onError` | `(message: string)` | Pipeline error |

**Status values:** `listening`, `speaking`, `processing`, `idle`

---

## REST API

All endpoints are served on port `8765` by the FastAPI server.

### `GET /health`

Service health and model readiness.

**Response:** `200 OK`

```json
{
  "status": "ok",
  "timestamp": 1719200000.0,
  "version": "2.0-s2s",
  "services": {
    "translator": "loaded",
    "vexyl_stt": "ws://vexyl-stt:8080",
    "tts": "loaded (local omnivoice)"
  },
  "models": {
    "indic_en": "/root/.cache/ctranslate2/ct2-rotary-indictrans2-indic-en-dist-200M",
    "en_indic": "/root/.cache/ctranslate2/ct2-rotary-indictrans2-en-indic-dist-200M",
    "tts": "k2-fsa/OmniVoice"
  }
}
```

### `POST /tts`

Text-to-speech synthesis with PantoMatrix blendshapes. Translates English input to the target Indic language if `lang` is not `"en"`.

**Request body** (`application/json`):

| Field | Type | Default | Description |
|---|---|---|---|
| `text` | string | `""` | Input text to synthesize |
| `lang` | string | `"en"` | Target language BCP-47 code (e.g. `"hi-IN"`, `"ta-IN"`) |
| `speed` | float | `1.0` | Playback speed factor |

**Response:** `200 OK`

```json
{
  "audio_url": "http://localhost:8765/api/v1/audio/<uuid>",
  "animation_matrix": [
    { "time": 0.0, "blendshapes": { "jawOpen": 0.12, "mouthSmileLeft": 0.05 } },
    { "time": 0.033, "blendshapes": { ... } }
  ]
}
```

The `animation_matrix` contains 52 ARKit blendshape frames at 30 FPS. Audio is 24 kHz float32 WAV.

### `POST /speak/{emotion}`

TTS synthesis with emotion-baked blendshapes. Same as `/tts` but applies an emotion overlay.

**Path parameter:** `emotion` — one of `neutral`, `happy`, `sad`, `angry`, `surprised`, `fearful`

**Request body** — same as `/tts`

**Response:** `200 OK`

```json
{
  "audio_url": "http://localhost:8765/api/v1/audio/<uuid>",
  "animation_matrix": [...],
  "emotion": "happy"
}
```

### `GET /api/v1/audio/{audio_id}`

Retrieve a previously generated WAV audio file by its ID.

**Response:** `200 OK` — binary WAV audio (`audio/wav`)

### `POST /chat`

Translation debug endpoint. Translates English text to an Indic language.

**Request body** (`application/json`):

| Field | Type | Default | Description |
|---|---|---|---|
| `text` | string | — | English text to translate |
| `lang` | string | `"en"` | Target language BCP-47 code |

**Response:** `200 OK`

```json
{
  "original": "Hello, how are you?",
  "translated": "नमस्ते, आप कैसे हैं?",
  "lang": "hi-IN",
  "flores_tgt": "hin_Deva"
}
```

---
## WebSocket Protocol

### Client → Server

| Type | Content | Description |
|---|---|---|
| `start` | `{"type":"start","lang":"hi-IN","session_id":"abc"}` | Initiate session |
| binary | `[Int16 PCM @ 16kHz mono]` | Audio stream |
| `stop` | `{"type":"stop"}` | End recording gracefully |
| `cancel` | `{"type":"cancel"}` | Abort session immediately |

### Server → Client

| Type | Fields | Description |
|---|---|---|
| `transcript` | `text, lang` | Real-time STT output |
| `tts_start` | `seq, text, lang` | Sentence TTS begins |
| `audio_chunk` | `seq, sample_rate, byte_length` | Audio metadata — followed by binary PCM |
| _(binary)_ | Raw float32 PCM bytes | Actual audio data |
| `blendshape_matrix` | `seq, matrix` | 52 ARKit blendshape frames at 30 FPS |
| `tts_end` | `seq` | Sentence fully transmitted |
| `pipeline_status` | `session_id, seq, lang` | Health heartbeat every 2 seconds |
| `error` | `message` | Pipeline error |

---

## Emotion System

The avatar supports 6 procedural expressions that drive secondary animations (breathing speed, head sway, blinking rate):

| Emotion | Breathing | Head Sway | Key Blendshapes |
|---|---|---|---|
| `neutral` | 1.8 Hz | Subtle | None |
| `happy` | 2.4 Hz | Elevated, head up | Smile, cheek squint, brow raise |
| `sad` | 1.2 Hz | Minimal, head down | Frown, brow inner up |
| `angry` | 3.2 Hz | Tense | Brow down, eye squint, sneer |
| `surprised` | 2.8 Hz | Upright, head up | Wide eyes, brow up, mouth shrug |
| `fearful` | 2.2 Hz | Guarded, head down | Wide eyes, brow up, frown |

During speech, emotion is baked into the server-delivered blendshape matrix. During idle, the client smoothly interpolates weights at 5 rad/s.

```javascript
widget.setEmotion('happy');
```

---

## Examples

### Complete Demo Page

See [`client/widget-demo.html`](../client/widget-demo.html) for a full working implementation with:

- Language selector (all 12 Indic languages)
- Emotion controls
- Pipeline log with color-coded messages
- Status bar
- Microphone start/stop buttons

### Custom Audio Playback

Override `onAudioChunk` for custom scheduling:

```javascript
const audioCtx = new AudioContext();

s2s.onAudioChunk = (seq, pcmBytes, sampleRate) => {
  const f32 = new Float32Array(pcmBytes);
  const buffer = audioCtx.createBuffer(1, f32.length, sampleRate);
  buffer.getChannelData(0).set(f32);
  const source = audioCtx.createBufferSource();
  source.buffer = buffer;
  source.connect(audioCtx.destination);
  source.start();
};
```

The demo page implements gapless playback using `AudioContext` `nextPlayTime` cursor — audio chunks are queued sequentially with no gaps between sentences.

---

## Deployment Checklist

1. **Deploy Vexyl STT** — Docker container, model downloads at startup into volume
   - Configure `VEXYL_STT_PORT`, `VEXYL_STT_API_KEY`
   - `HF_TOKEN` required on first run (gated model); remove after cached
   - For production: enable API key auth, set `VEXYL_STT_DECODE=rnnt` for accuracy
2. **Deploy server** — FastAPI server (port 8765), models download at startup into volume
   - Point `VEXYL_STT_URL` to the STT instance
   - Models from adalat-ai and k2-fsa are not gated — no token needed
3. **Deploy vLLM-Omni TTS** — port 8091 (optional, local TTS is default)
4. **Deploy LLM endpoint** — port 8000 (OpenAI-compatible)
5. **Host the 3D model** — serve `avatar_head.glb` from your CDN or server
6. **Copy `client/` files** to your web server
7. **Configure environment variables** — service URLs
8. **Test** — `GET /health` should show all services loaded
