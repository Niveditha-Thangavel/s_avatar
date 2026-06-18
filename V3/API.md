# Voice Avatar Server — API Reference

**Base URL:** `http://localhost:8765` (backend) / `http://localhost:3005` (dev frontend with Vite proxy)

## Backend Endpoints

### `GET /health`

Check model readiness.

**Response 200:**

```json
{
  "status":    "ok",
  "timestamp": 1718000000.0,
  "omnivoice": "loaded",
  "whisper":   "loaded",
  "granite":   "loaded",
  "small100":  "loaded"
}
```

Each model field is `"loaded"` or `"loading"` depending on startup state.

---

### `GET /api/v1/audio/{audio_id}`

Serve a previously generated WAV audio file from the in-memory store.

| Parameter | Type | Description |
|-----------|------|-------------|
| `audio_id` | path | Hex string returned by `/api/v1/chat` or `/speak/{emotion}` |

**Response 200:** `audio/wav` binary (32-bit float PCM, 24000 Hz).

**Response 404:** `{ "detail": "Audio not found" }`

Audio entries are evicted (oldest 50 removed) when the store exceeds 100 entries.

---

### `POST /chat`

Legacy typed-text endpoint. No audio or animation matrix.

**Request:**

```json
{
  "text":       "Hello, how are you?",
  "session_id": "default",
  "lang":       "en"
}
```

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `text` | string | — | User input text (required, non-empty) |
| `session_id` | string | `"default"` | Conversation session ID for context |
| `lang` | string | `"en"` | Target response language (ISO 639-1) |

**Response 200:**

```json
{
  "reply":       "I'm doing well, thank you!",
  "native_text": "I'm doing well, thank you!",
  "emotion":     "happy",
  "intent":      "greeting"
}
```

**Pipeline:** LLM (Granite 4.0 Nano) → SMaLL-100 translation to target language.

---

### `POST /api/v1/chat`

Full unified pipeline: mic-recorded audio → blendshape animation matrix.

**Request:** `multipart/form-data`

| Field | Type | Description |
|-------|------|-------------|
| `file` | WAV file | 16 kHz mono preferred, 32-bit float (will be resampled if needed) |

**Response 200:**

```json
{
  "audio_url": "http://localhost:8765/api/v1/audio/abc123...",
  "animation_matrix": [
    {
      "time": 0.000,
      "blendshapes": {
        "jawOpen": 0.85,
        "mouthSmileLeft": 0.50,
        "browInnerUp": 0.0,
        ...
      }
    },
    ...
  ],
  "emotion": "happy",
  "intent":  "unknown"
}
```

**Pipeline (10 steps):**

| Step | Component | Description |
|------|-----------|-------------|
| 1 | Read audio | Decode WAV, convert to mono if stereo |
| 2 | Whisper STT | Speech-to-text + language ID + user emotion |
| 3 | SMaLL-100 → EN | Translate user's language to English |
| 4 | Granite LLM | Generate response + emotion + intent classification |
| 5 | Parse tokens | Extract inline emotion tokens (`[laughter]`, `[sigh]`, etc.) |
| 6 | SMaLL-100 → native | Translate response back to user's language |
| 7 | OmniVoice TTS | Synthesize speech at 24 kHz |
| 8 | PantoMatrix | Extract 52 ARKit blendshapes from audio (30 fps, in thread) |
| 9 | Emotion blend | Bake `_EMOTION_BLENDSHAPES` + inline tokens into every frame |
| 10 | Cleanup | Evict old audio entries if store exceeds 100 |

**Error responses:** `400` (no speech / invalid audio), `500` (STT/LLM/TTS failure).

---

### `POST /speak/{emotion}`

Forced-emotion TTS. Generates audio + animation matrix with the specified emotional expression baked into the blendshapes. Emotion is applied **only** to the 3D avatar's facial expression via `_EMOTION_BLENDSHAPES` — OmniVoice voice prosody is unaffected.

| Parameter | Type | Valid Values |
|-----------|------|-------------|
| `emotion` | path | `neutral`, `happy`, `sad`, `angry`, `surprised`, `fearful` |

**Request:**

```json
{
  "text":  "Hello world!",
  "lang":  "en",
  "speed": 1.0
}
```

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `text` | string | `""` | Text to speak (required, non-empty) |
| `lang` | string | `"en"` | Target language; text is translated from English if not `"en"` |
| `speed` | float | `1.0` | TTS speed multiplier |

**Response 200:**

```json
{
  "audio_url": "http://localhost:8765/api/v1/audio/abc123...",
  "animation_matrix": [
    {
      "time": 0.000,
      "blendshapes": {
        "jawOpen": 0.83,
        "mouthSmileLeft": 0.50,
        ...
      }
    },
    ...
  ],
  "emotion": "happy"
}
```

**Pipeline (7 steps):**

| Step | Description |
|------|-------------|
| 1 | Translate to target language (if `lang != "en"`) |
| 2 | Sanitize for OmniVoice (strip whitespace around bracketed tokens) |
| 3 | OmniVoice TTS → 24 kHz audio |
| 4 | Write WAV to in-memory store |
| 5 | PantoMatrix extract blendshapes (in thread) |
| 6 | Apply emotion blendshapes from `_EMOTION_BLENDSHAPES` to every frame |
| 7 | Cleanup old audio entries |

**Per-emotion blendshape targets** (defined in `_EMOTION_BLENDSHAPES`):

| Emotion | Key shapes |
|---------|-----------|
| `neutral` | (none) |
| `happy` | mouthSmile 0.50, cheekSquint 0.30, browOuterUp 0.20 |
| `sad` | mouthFrown 0.50, browInnerUp 0.45, browDown 0.15 |
| `angry` | browDown 0.65, eyeSquint 0.40, mouthFrown 0.30, noseSneer 0.25 |
| `surprised` | eyeWide 0.60, browInnerUp 0.55, browOuterUp 0.45, mouthShrugUpper 0.20 |
| `fearful` | eyeWide 0.45, browInnerUp 0.50, browOuterUp 0.30, mouthFrown 0.25 |

**Error responses:** `400` (empty text), `422` (invalid emotion), `500` (TTS failure).

---

## WebSocket Endpoints

### `WS /ws/tts`

Legacy streaming TTS. Accept text, stream PCM audio chunks, then receive a complete message with the animation matrix.

**Client → Server messages:**

```json
// Speak command
{ "type": "speak", "text": "Hello world", "instruct": null, "speed": 1.0, "numStep": 16, "emotion": "happy" }

// Stop current utterance
{ "type": "stop" }
```

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `text` | string | — | Text to synthesize (required) |
| `instruct` | string\|null | `null` | OmniVoice instruct (no effect on emotion prosody) |
| `speed` | float | `1.0` | Speech speed multiplier |
| `numStep` | int | `16` | Inference steps |
| `emotion` | string | `"happy"` | Emotion for blendshape animation (valid: neutral/happy/sad/angry/surprised/fearful) |

**Server → Client messages:**

```json
// Status updates
{ "type": "status", "data": "generating" }
{ "type": "status", "data": "stopped" }

// Audio chunk (followed by raw PCM bytes via WebSocket binary frame)
{ "type": "chunk", "text": "Hello world", "sampleRate": 24000, "byteLength": 19200 }

// Completion (after all chunks sent, includes animation matrix)
{
  "type": "status",
  "data": "complete",
  "animation_matrix": [
    { "time": 0.000, "blendshapes": { "jawOpen": 0.83, ... } },
    ...
  ],
  "emotion": "happy"
}

// Error
{ "type": "error", "message": "Empty text" }
```

---

### `WS /ws/stt`

Full legacy pipeline via WebSocket. Send PCM audio chunks, control with text messages, receive transcript + LLM reply.

**Client → Server messages:**

```json
// Binary: raw PCM audio (16-bit signed, 16 kHz, mono)
// Each binary frame is appended to an internal buffer

// Start listening
{ "type": "config" }

// Stop recording and process
{ "type": "stop" }

// Cancel current buffer
{ "type": "cancel" }
```

**Server → Client messages:**

```json
// Status updates
{ "type": "status", "data": "listening" }
{ "type": "status", "data": "transcribing" }
{ "type": "status", "data": "thinking" }
{ "type": "status", "data": "stopped" }
{ "type": "status", "data": "cancelled" }

// Transcript
{ "type": "transcript", "text": "Original user speech in detected language" }

// LLM reply
{
  "type":        "reply",
  "text":        "I'm doing well, thank you!",
  "native_text": "I'm doing well, thank you!",
  "emotion":     "happy",
  "intent":      "greeting"
}

// Error
{ "type": "error", "message": "STT error: ..." }
```

Audio buffer is flushed only on `"stop"`. Silence (< 0.3s) is discarded.

---

## Inline Emotion Tokens

When the LLM embeds bracketed tokens in its response, they trigger additional blendshape clamps on the corresponding frames.

| Token | Blendshapes |
|-------|-------------|
| `[laughter]` | mouthSmile 0.80, cheekSquint 0.55, eyeSquint 0.50 |
| `[sigh]` | jawOpen 0.30, browInnerUp 0.20 |
| `[surprise-oh]` | jawOpen 0.60, browInnerUp 0.50, browOuterUp 0.35, eyeWide 0.55 |
| `[dissatisfaction-hnn]` | mouthFrown 0.45, browDown 0.35 |

Tokens pass through to OmniVoice as text (it may render them as vocal effects); the blendshape clamping is additive on top of the persistent emotion expression.

---

## Animation Matrix Format

Every endpoint that returns `animation_matrix` follows this structure:

```json
[
  {
    "time": 0.000,
    "blendshapes": {
      "jawOpen": 0.83,
      "mouthSmileLeft": 0.50,
      "mouthSmileRight": 0.48,
      "browInnerUp": 0.0,
      "eyeBlinkLeft": 0.0,
      "eyeBlinkRight": 0.0,
      ...
    }
  },
  ...
]
```

- **Rate:** 30 frames per second (one entry per ~33.3 ms)
- **52 ARKit blendshapes** per frame (all 52 keys present)
- **Values:** 0.0–1.0 float, representing morph target weights
- **Emotion shapes** are additively blended via `_apply_emotion_to_matrix()` — they layer on top of the phoneme-driven PantoMatrix output
- The frontend's Three.js avatar (`avatar3d.js`) interpolates between frames linearly using binary search + lerp

---

## Vite Dev Server Proxy (port 3005)

When running `npm run dev`, Vite proxies these paths to the backend:

| Path | Target | Notes |
|------|--------|-------|
| `/ws` | `ws://localhost:8765` | WebSocket passthrough (`ws: true`) |
| `/chat` | `http://localhost:8765` | |
| `/health` | `http://localhost:8765` | |
| `/api` | `http://localhost:8765` | |
| `/speak` | `http://localhost:8765` | |

In production, configure nginx or similar to proxy the same paths.

---

## Data Flow Summary

```
                                  ┌─────────────┐
                                  │  Frontend   │
                                  │  (port 3005) │
                                  └──────┬──────┘
                                         │
                           ┌─────────────┴─────────────┐
                           │        Vite Proxy         │
                           │   /ws /chat /health /api  │
                           │        /speak             │
                           └─────────────┬─────────────┘
                                         │
                           ┌─────────────┴─────────────┐
                           │    FastAPI (port 8765)    │
                           │                           │
                           │  ┌─────────────────────┐  │
                           │  │   Whisper STT       │  │
                           │  │   (faster-whisper)  │  │
                           │  ├─────────────────────┤  │
                           │  │   SMaLL-100 MT      │  │
                           │  │   (native ↔ EN)     │  │
                           │  ├─────────────────────┤  │
                           │  │   Granite 4.0 Nano  │  │
                           │  │   (LLM + emotion)   │  │
                           │  ├─────────────────────┤  │
                           │  │   OmniVoice TTS     │  │
                           │  │   (24 kHz PCM)      │  │
                           │  ├─────────────────────┤  │
                           │  │   PantoMatrix       │  │
                           │  │   (52 ARKit shapes) │  │
                           │  └─────────────────────┘  │
                           └───────────────────────────┘
```

### Endpoint → Pipeline Mapping

| Endpoint | STT | Translate→EN | LLM | Translate→native | TTS | PantoMatrix | Emotion Blend |
|----------|:---:|:------------:|:---:|:----------------:|:---:|:-----------:|:-------------:|
| `GET /health` | | | | | | | |
| `POST /chat` | | | ✓ | ✓ | | | |
| `WS /ws/tts` | | | | | ✓ | ✓ | ✓ |
| `WS /ws/stt` | ✓ | ✓ | ✓ | ✓ | | | |
| `POST /api/v1/chat` | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| `POST /speak/{emotion}` | | ✓ | | ✓ | ✓ | ✓ | ✓ |
