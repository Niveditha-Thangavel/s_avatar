# Avatar Widget — Integration Guide

Embed the 3D talking avatar into any existing website or web app with full lipsync, blinking, breathing, and body language.

---

## Quick Start (3 steps)

### 1. HTML — Add a container and import map

```html
<!-- Place the avatar anywhere in your page -->
<div id="avatar-container" style="width:400px;height:500px;"></div>

<!-- Three.js import map (required) -->
<script type="importmap">
{
  "imports": {
    "three": "https://unpkg.com/three@0.184.0/build/three.module.js",
    "three/addons/": "https://unpkg.com/three@0.184.0/examples/jsm/"
  }
}
</script>
```

### 2. JS — Create the widget

```html
<script type="module">
import { AvatarWidget } from './avatar-widget.js';

const widget = new AvatarWidget({
  container: document.getElementById('avatar-container'),
  modelUrl: '/avatar_head.glb',   // path to your GLB model
  onReady: () => console.log('Avatar ready')
});
</script>
```

### 3. JS — Drive lipsync from your server

```html
<script type="module">
// Your existing chat logic calls this when it gets a response
async function onChatResponse(userAudioBlob) {
  // Send audio to backend → get audio + animation_matrix
  const form = new FormData();
  form.append('file', userAudioBlob, 'audio.wav');
  const res = await fetch('http://your-server:8765/api/v1/chat', {
    method: 'POST',
    body: form
  });
  const data = await res.json();

  // Drive lipsync
  widget.setAnimationMatrix(data.animation_matrix);

  // Play audio
  const audio = new Audio(data.audio_url);
  audio.onended = () => widget.clearAnimation();
  audio.play();
}
</script>
```

---

## Full Chatbot Integration Example

```html
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Chat + Avatar</title>
  <script type="importmap">
  {
    "imports": {
      "three": "https://unpkg.com/three@0.184.0/build/three.module.js",
      "three/addons/": "https://unpkg.com/three@0.184.0/examples/jsm/"
    }
  }
  </script>
  <style>
    .layout { display: flex; height: 100vh; }
    .chat-panel { width: 360px; padding: 20px; display: flex; flex-direction: column; }
    .avatar-panel { flex: 1; }
    #avatar-container { width: 100%; height: 100%; }
    .messages { flex: 1; overflow-y: auto; }
    .input-row { display: flex; gap: 8px; margin-top: 12px; }
    #chat-input { flex: 1; padding: 10px; }
  </style>
</head>
<body>
  <div class="layout">
    <div class="chat-panel">
      <h2>Chat</h2>
      <div class="messages" id="messages"></div>
      <div class="input-row">
        <input type="text" id="chat-input" placeholder="Type a message..." />
        <button id="btn-send">Send</button>
        <button id="btn-mic">🎤</button>
      </div>
    </div>
    <div class="avatar-panel">
      <div id="avatar-container"></div>
    </div>
  </div>

  <script type="module">
    import { AvatarWidget } from './avatar-widget.js';

    // ── 1. Init avatar ────────────────────────────────────────────────
    const widget = new AvatarWidget({
      container: document.getElementById('avatar-container'),
      modelUrl: '/avatar_head.glb',
    });

    const SERVER = 'http://localhost:8765';

    // ── 2. Send audio → get lipsync + audio ───────────────────────────
    async function processAudio(wavBlob) {
      const form = new FormData();
      form.append('file', wavBlob, 'audio.wav');
      const res = await fetch(`${SERVER}/api/v1/chat`, { method: 'POST', body: form });
      if (!res.ok) throw new Error(`API error ${res.status}`);
      const data = await res.json();

      if (data.animation_matrix) {
        widget.setAnimationMatrix(data.animation_matrix);
      }

      if (data.audio_url) {
        const audio = new Audio(data.audio_url);
        audio.onended = () => widget.clearAnimation();
        audio.play();
      }

      return data;
    }

    // ── 3. Text input → LLM reply → TTS + lipsync ────────────────────
    async function sendText(text) {
      // Get LLM response
      const chatRes = await fetch(`${SERVER}/chat`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text, lang: 'en' })
      });
      const chatData = await chatRes.json();
      const reply = chatData.native_text || chatData.reply || text;

      // Convert reply to audio + matrix via unified API
      // Method A: Use /api/v1/chat with a WAV blob (requires mic recording)
      // Method B: Use /ws/tts WebSocket (text → PCM audio) — see legacy path below

      // For text-only input, use the WebSocket TTS path:
      await speakViaWebSocket(reply);
    }

    // ── 4. WebSocket TTS (text → PCM + matrix) ────────────────────────
    function speakViaWebSocket(text) {
      return new Promise((resolve) => {
        const ws = new WebSocket(`ws://localhost:8765/ws/tts`);
        ws.binaryType = 'arraybuffer';
        const chunks = [];
        let matrix = null;

        ws.onopen = () => ws.send(JSON.stringify({ type: 'speak', text }));
        ws.onmessage = (e) => {
          if (e.data instanceof ArrayBuffer) {
            chunks.push(new Float32Array(e.data));
          } else {
            const msg = JSON.parse(e.data);
            if (msg.type === 'status' && msg.data === 'complete') {
              matrix = msg.animation_matrix || null;
              ws.close();
            }
          }
        };
        ws.onclose = () => {
          if (chunks.length === 0) { resolve(); return; }
          const total = chunks.reduce((s, c) => s + c.length, 0);
          const audio = new Float32Array(total);
          let off = 0;
          for (const c of chunks) { audio.set(c, off); off += c.length; }

          if (matrix) widget.setAnimationMatrix(matrix);

          const ctx = new AudioContext();
          const buf = ctx.createBuffer(1, audio.length, 24000);
          buf.getChannelData(0).set(audio);
          const src = ctx.createBufferSource();
          src.buffer = buf;
          src.connect(ctx.destination);
          src.onended = () => { widget.clearAnimation(); resolve(); };
          src.start(0);
        };
      });
    }

    // ── 5. Mic recording → unified API ───────────────────────────────
    let mediaRecorder = null;
    let audioChunks = [];

    document.getElementById('btn-mic').onclick = async () => {
      if (mediaRecorder && mediaRecorder.state === 'recording') {
        mediaRecorder.stop();
        return;
      }
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      mediaRecorder = new MediaRecorder(stream);
      audioChunks = [];
      mediaRecorder.ondataavailable = (e) => audioChunks.push(e.data);
      mediaRecorder.onstop = async () => {
        const blob = new Blob(audioChunks, { type: 'audio/wav' });
        await processAudio(blob);
        stream.getTracks().forEach(t => t.stop());
      };
      mediaRecorder.start();
    };

    // ── 6. Bind chat UI ──────────────────────────────────────────────
    const addMsg = (role, text) => {
      const el = document.createElement('div');
      el.textContent = `${role}: ${text}`;
      document.getElementById('messages').appendChild(el);
    };

    document.getElementById('btn-send').onclick = async () => {
      const input = document.getElementById('chat-input');
      const text = input.value.trim();
      if (!text) return;
      addMsg('You', text);
      input.value = '';
      await sendText(text);
    };
  </script>
</body>
</html>
```

---

## Widget API Reference

### Constructor Options

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `container` | `HTMLElement` | **required** | DOM element to render the avatar into |
| `modelUrl` | `string` | `/avatar_head.glb` | Path to the GLB 3D model |
| `onReady` | `function(widget)` | `null` | Called when the model finishes loading |
| `calibration` | `object` | *(default posture)* | Custom arm/forearm/hand rotation offsets |

### Methods

| Method | Description |
|--------|-------------|
| `setAnimationMatrix(matrix)` | Feed a PantoMatrix array to drive lipsync. Takes `[{time, blendshapes}]` |
| `clearAnimation()` | Stop lipsync, reset all face morphs to zero |
| `setEmotion('happy')` | Set idle expression: `neutral`, `happy`, `sad`, `angry`, `surprised` |
| `resize()` | Manually trigger a resize (auto-detected via ResizeObserver) |
| `dispose()` | Destroy the widget, free GPU memory, stop animation loop |

### What happens automatically (no code needed)

- **Blinking** — random blinks every 3-6 seconds, with occasional double-blinks
- **Breathing** — subtle head and arm sway at ~1.8Hz
- **Eye saccades** — random micro gaze shifts every 1-3 seconds
- **Arm sways** — subtle idle arm movement synchronized with breathing
- **Posture** — arm/forearm/hand calibration applied
- **Resize** — automatically adapts to container size changes

---

## Two Integration Paths

### Path A: Mic recording → unified `/api/v1/chat` (recommended)

```
User speaks → MediaRecorder → WAV blob → POST /api/v1/chat
  → returns { audio_url, animation_matrix, emotion }
  → widget.setAnimationMatrix(matrix)
  → audio = new Audio(audio_url) → .play()
  → audio.onended = () => { widget.clearAnimation(); widget.setEmotion(data.emotion); }
```

Use this when the user speaks into a microphone. Single request returns everything.

### Path B: Text input → `/chat` + `/ws/tts` (text-driven)

```
User types → POST /chat → LLM reply text → WS /ws/tts
  → streams PCM audio chunks
  → on complete: msg.animation_matrix
  → widget.setAnimationMatrix(matrix)
  → play PCM audio via AudioContext
```

Use this when the user types text. The `/chat` endpoint gets the LLM response, then `/ws/tts` generates speech + animation matrix.

---

## Backend Endpoints Summary

| Endpoint | Method | Input | Output |
|----------|--------|-------|--------|
| `/api/v1/chat` | `POST` | WAV file (multipart) | `{ audio_url, animation_matrix, emotion }` |
| `/chat` | `POST` | `{ text, lang }` JSON | `{ reply, native_text, emotion }` |
| `/ws/tts` | WebSocket | `{ type:"speak", text }` | Binary PCM chunks + `{ type:"status", data:"complete", animation_matrix }` |
| `/health` | `GET` | — | Server status |

---

## Files to Copy

Place these on your web server:

| File | Source |
|------|--------|
| `avatar-widget.js` | `V3/avatar-widget.js` |
| `avatar_head.glb` | `V3/public/avatar_head.glb` |

No build step, no bundler needed. Just serve them as static files.
