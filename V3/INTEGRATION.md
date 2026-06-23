# Integration Guide — S2S Voice Avatar Widget

This guide explains how to embed the `AvatarWidget` into any web page and connect it to the S2S pipeline.

---

## Quick Start

```html
<script type="importmap">
{
  "imports": {
    "three": "https://unpkg.com/three@0.184.0/build/three.module.js",
    "three/addons/": "https://unpkg.com/three@0.184.0/examples/jsm/"
  }
}
</script>

<div id="avatar-container" style="width:600px;height:600px;"></div>

<script type="module">
  import { AvatarWidget } from './avatar-widget.js';

  const widget = new AvatarWidget({
    container: document.getElementById('avatar-container'),
    modelUrl:  '/avatar_head.glb',
    onReady:   () => console.log('Avatar ready'),
  });

  // Set idle emotion
  widget.setEmotion('happy');

  // Drive lip-sync from a PantoMatrix result
  widget.setAnimationMatrix(matrix);   // [{time, blendshapes}]
  widget.clearAnimation();             // return to idle
</script>
```

---

## S2S WebSocket Integration

The full pipeline runs over a single WebSocket at `/ws/s2s`.

```javascript
const WS_URL = 'ws://localhost:8765/ws/s2s';
const ws = new WebSocket(WS_URL);
ws.binaryType = 'arraybuffer';

// 1. Open connection
ws.onopen = () => {
  ws.send(JSON.stringify({
    type:       'start',
    lang:       'hi-IN',           // BCP-47 Indic language code
    session_id: `demo_${Date.now()}`,
  }));
};

// 2. Stream mic PCM (Int16 @ 16kHz mono) while recording
//    See S2SManager in src/stt.js for the AudioWorklet implementation

// 3. Handle server messages
ws.onmessage = (evt) => {
  if (evt.data instanceof ArrayBuffer) {
    // Raw float32 PCM — schedule playback + accumulate for PantoMatrix
    return;
  }
  const msg = JSON.parse(evt.data);
  switch (msg.type) {
    case 'transcript':
      // msg.text — real-time STT from Vexyl
      break;
    case 'tts_start':
      // msg.seq, msg.text — new sentence beginning
      break;
    case 'audio_chunk':
      // msg.seq, msg.sample_rate, msg.byte_length
      // Next binary frame = float32 PCM bytes
      break;
    case 'tts_end':
      // msg.seq — run PantoMatrix on accumulated PCM, call widget.setAnimationMatrix()
      break;
  }
};

// 4. Stop recording
ws.send(JSON.stringify({ type: 'stop' }));
```

---

## Client-side PantoMatrix

After receiving the `blendshape_matrix` message from the server, drive the avatar:

```javascript
// Server sends blendshape_matrix after TTS streaming + PantoMatrix
s2s.onBlendshapeMatrix = (seq, matrix) => {
  widget.setAnimationMatrix(matrix);
};
```

The matrix format is a JSON array of `{time: number, blendshapes: {[name: string]: number}}` objects at 30 FPS, computed by `server/pantomatrix.py`.

---

## Supported Languages

| BCP-47 | Language |
|---|---|
| hi-IN | Hindi |
| bn-IN | Bengali |
| gu-IN | Gujarati |
| kn-IN | Kannada |
| ml-IN | Malayalam |
| mr-IN | Marathi |
| pa-IN | Punjabi |
| ta-IN | Tamil |
| te-IN | Telugu |
| ur-IN | Urdu |
| as-IN | Assamese |
| or-IN | Odia |

---

## AvatarWidget API

| Method | Description |
|---|---|
| `new AvatarWidget(opts)` | Create widget. `opts`: `container`, `modelUrl`, `onReady`, `calibration` |
| `setAnimationMatrix(matrix)` | Drive lip-sync from PantoMatrix output |
| `clearAnimation()` | Return to idle pose (all blendshapes → 0) |
| `setEmotion(name)` | Set idle expression: `neutral`, `happy`, `sad`, `angry`, `surprised` |
| `loadGLBModel(url)` | Swap the 3D model at runtime |
| `dispose()` | Clean up Three.js renderer and observers |
| `resize()` | Manually trigger resize (if ResizeObserver is not sufficient) |
