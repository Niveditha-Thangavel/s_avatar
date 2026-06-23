# API Reference — S2S Voice Avatar Server (v2.0)

All endpoints are served by the FastAPI orchestrator on port **8765**.

---

## `GET /health`

Service health check and model readiness.

**Response 200:**
```json
{
  "status": "ok",
  "timestamp": 1234567890.0,
  "version": "2.0-s2s",
  "services": {
    "translator": "loaded",
    "vexyl_stt":  "ws://localhost:8080",
    "llm":        "http://localhost:8000/v1/chat/completions",
    "tts":        "http://localhost:8091/v1/audio/speech"
  },
  "models": {
    "indic_en": "~/.cache/ctranslate2/indic-en-1B",
    "en_indic": "~/.cache/ctranslate2/en-indic-1B",
    "llm":      "granite-4.0-nano",
    "tts":      "k2-fsa/OmniVoice"
  }
}
```

---

## `WS /ws/s2s`

Full Speech-to-Speech pipeline WebSocket.

**Protocol — Client → Server:**

| Message | Description |
|---|---|
| `{"type":"start","lang":"hi-IN","session_id":"abc"}` | Initiate session. `lang` is BCP-47 (see supported languages below). |
| `[binary Int16 PCM @ 16kHz mono]` | Streaming audio chunks from the microphone. |
| `{"type":"stop"}` | End recording gracefully — server finishes processing. |
| `{"type":"cancel"}` | Abort session immediately. |

**Protocol — Server → Client:**

| Message | Description |
|---|---|
| `{"type":"transcript","text":"...","lang":"..."}` | Real-time STT transcript from Vexyl. |
| `{"type":"tts_start","seq":1,"text":"...","lang":"..."}` | TTS starting for sentence `seq`. |
| `{"type":"audio_chunk","seq":1,"sample_rate":24000,"byte_length":N}` | Audio metadata — the next binary frame contains `N` bytes of float32 PCM. |
| `[binary float32 PCM]` | Raw audio data for the current sentence. |
| `{"type":"tts_end","seq":1}` | All audio chunks for sentence `seq` have been sent. |
| `{"type":"pipeline_status","session_id":"...","seq":2,"lang":"..."}` | Heartbeat every 2 seconds. |
| `{"type":"error","message":"..."}` | Pipeline error. |

**Supported languages (BCP-47 → FLORES-200):**

| BCP-47 | Language | FLORES |
|---|---|---|
| hi-IN | Hindi | hin_Deva |
| bn-IN | Bengali | ben_Beng |
| gu-IN | Gujarati | guj_Gujr |
| kn-IN | Kannada | kan_Knda |
| ml-IN | Malayalam | mal_Mlym |
| mr-IN | Marathi | mar_Deva |
| pa-IN | Punjabi | pan_Guru |
| ta-IN | Tamil | tam_Taml |
| te-IN | Telugu | tel_Telu |
| ur-IN | Urdu | urd_Arab |
| as-IN | Assamese | asm_Beng |
| or-IN | Odia | ory_Orya |

---

## `POST /chat`

Debug endpoint — text → Indic-to-English translation only (no audio).
Useful for smoke-testing the IndicTrans2 engine without a full session.

**Request:**
```json
{ "text": "नमस्ते! आप कैसे हैं?", "lang": "hi" }
```

**Response:**
```json
{
  "original": "नमस्ते! आप कैसे हैं?",
  "english":  "Hello! How are you?",
  "stub":     "LLM + TTS bypassed in debug mode",
  "note":     "Use /ws/s2s for full pipeline"
}
```
