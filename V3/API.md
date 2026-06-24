# API & Integration Reference — S2S Voice Avatar (v3.0)

This document describes both the server-side API endpoints served by the FastAPI Orchestrator (port **8765**) and the client-side Widget APIs (including the procedural emotion system) used to embed the 3D avatar.

---

## 1. Server API Endpoints

All backend requests are served by the orchestrator service.

### `GET /health`
Retrieves server health status and verifies that translation, STT, and TTS engines are fully loaded on the GPU.

*   **URL:** `/health`
*   **Method:** `GET`
*   **Response Payload (200 OK):**
    ```json
    {
      "status": "ok",
      "timestamp": 1719216000.42,
      "version": "2.0-s2s",
      "services": {
        "translator": "loaded",
        "vexyl_stt": "ws://vexyl-stt:8080",
        "llm": "disabled (using static mock responses)",
        "tts": "loaded (local omnivoice)"
      },
      "models": {
        "indic_en": "/root/.cache/ctranslate2/nllb-200-distilled-600M",
        "en_indic": "/root/.cache/ctranslate2/nllb-200-distilled-600M",
        "llm": "disabled",
        "tts": "k2-fsa/OmniVoice"
      }
    }
    ```

---

### `POST /chat`
A synchronous text translation debug endpoint. It translates Indic input into English to smoke-test the NLLB-200 translation pipeline.

*   **URL:** `/chat`
*   **Method:** `POST`
*   **Request Parameters:**
    ```json
    {
      "text": "नमस्ते! आप कैसे हैं?",
      "lang": "hi" // "hi" for Hindi, "en" for English
    }
    ```
*   **Response Payload (200 OK):**
    ```json
    {
      "original": "नमस्ते! आप कैसे हैं?",
      "english": "Hello! How are you?",
      "stub": "LLM + TTS bypassed in debug mode",
      "note": "Use /ws/s2s for full pipeline"
    }
    ```

---

### `WS /ws/s2s`
The real-time bidirectional Speech-to-Speech WebSocket server. The frontend streams user audio to this socket and receives live transcription, visual blendshapes, and audio output.

*   **URL:** `/ws/s2s`
*   **Binary Type:** `arraybuffer`

#### A. Client → Server Messages (Control & Media)
*   **Start Handshake (`type: "start"`):** Must be sent immediately on connect to configure the session:
    ```json
    {
      "type": "start",
      "lang": "hi-IN", // BCP-47 language code (see table below)
      "session_id": "optional-unique-session-id"
    }
    ```
*   **User Audio Stream (Binary):** Stream of raw **16kHz, 16-bit, Mono PCM** audio bytes recorded from the user's microphone.
*   **Flush/Stop Speech (`type: "stop"`):** Sent to notify the server that the user finished speaking:
    ```json
    {
      "type": "stop"
    }
    ```
*   **Cancel Output (`type: "cancel"`):** Sent to immediately interrupt and cancel the avatar's current speech sequence.
    ```json
    {
      "type": "cancel"
    }
    ```

#### B. Server → Client Messages (Events & Media)
*   **Heartbeat / Pipeline Status (`type: "pipeline_status"`):** Sent every 2 seconds to maintain connection health:
    ```json
    {
      "type": "pipeline_status",
      "session_id": "...",
      "seq": 0,
      "lang": "hi-IN"
    }
    ```
*   **User Live Transcript (`type: "transcript"`):** Real-time recognized text sent incrementally:
    ```json
    {
      "type": "transcript",
      "text": "नमस्ते",
      "lang": "hi-IN"
    }
    ```
*   **Avatar Speech Started (`type: "tts_start"`):** Sent when a response sentence is scheduled:
    ```json
    {
      "type": "tts_start",
      "seq": 0, // Incremental sentence sequence ID
      "text": "नमस्ते! मैं आपका वॉयस अवतार हूँ।",
      "lang": "hi-IN"
    }
    ```
*   **Audio Metadata Chunk (`type: "audio_chunk"`):** Precedes the raw audio binary message:
    ```json
    {
      "type": "audio_chunk",
      "seq": 0,
      "sample_rate": 24000,
      "byte_length": 1920
    }
    ```
*   **Raw Audio Data (Binary):** Immediate binary packet following the `audio_chunk` header containing raw **24kHz, 32-bit Float, Mono PCM** speaker audio.
*   **Blendshape Matrix (`type: "blendshape_matrix"`):** Time-series viseme frames calculated by the PantoMatrix classifier:
    ```json
    {
      "type": "blendshape_matrix",
      "seq": 0,
      "matrix": [
        [0.0, 0.05, 0.1, ...], // Frame 0 (61 float morph values)
        [0.01, 0.07, 0.08, ...], // Frame 1
        ...
      ]
    }
    ```
*   **Avatar Speech Ended (`type: "tts_end"`):** Sent when all audio chunks for sentence `seq` have completed:
    ```json
    {
      "type": "tts_end",
      "seq": 0
    }
    ```

---

## 2. Supported Languages

Provide these BCP-47 codes in the `start` WebSocket handshake parameters:

| Language Code | Language Name | Translation Target (FLORES-200) |
|---|---|---|
| `hi-IN` | Hindi | `hin_Deva` |
| `ta-IN` | Tamil | `tam_Taml` |
| `te-IN` | Telugu | `tel_Telu` |
| `ml-IN` | Malayalam | `mal_Mlym` |
| `kn-IN` | Kannada | `kan_Knda` |
| `mr-IN` | Marathi | `mar_Deva` |
| `gu-IN` | Gujarati | `guj_Gujr` |
| `bn-IN` | Bengali | `ben_Beng` |
| `pa-IN` | Punjabi | `pan_Guru` |
| `or-IN` | Odia | `ory_Orya` |
| `as-IN` | Assamese | `asm_Beng` |
| `ur-IN` | Urdu | `urd_Arab` |

---

## 3. Client-Side Widget Emotion API

The 3D avatar's emotion state is managed procedurally on the client side to keep latency low. The server sends raw mouth-shape movements (lip-sync matrices), while the client-side engine layers secondary animations (breathing rate, head sway, eyes, brow shapes) based on the active emotion.

### Valid Emotions
The widget supports 6 distinct procedural emotion states:
*   `neutral`
*   `happy`
*   `sad`
*   `angry`
*   `surprised`
*   `fearful`

### Method Call
Set the emotion directly using the `AvatarWidget` instance in your frontend JavaScript:

```javascript
// Set the avatar's expression to happy (drives brow, eyes, mouth corners, and breathing speed)
widget.setEmotion('happy');

// Revert to neutral
widget.setEmotion('neutral');
```

### Under the Hood Behavior:
*   **Idle Phase:** When the avatar is not speaking, `BehaviorManager.emotionWeights` lerps the morph targets (e.g., `eyeWideLeft`, `browInnerUp`) toward the designated emotion poses at `5.0 rad/s`. It also dynamically alters body idle params (e.g., faster breathing for `angry`/`happy`, slower breathing for `sad`).
*   **Speaking Phase:** When a `blendshape_matrix` is actively driving the mouth, the emotional morph targets are blended down so they do not distort the spoken visemes. Poses return to the active idle emotion smoothly once speech ends.
