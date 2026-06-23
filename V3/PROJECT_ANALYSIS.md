# Project Analysis — S2S Voice Avatar (V3 / v2.0)

## Stack Summary

**Backend:** Python 3.11 + FastAPI + uvicorn (port 8765)  
**Frontend:** Vite 8 + Three.js (port 3005, proxied to backend)  
**Voice Pipeline:** Vexyl STT → IndicTrans2 (CTranslate2) → External LLM → IndicTrans2 → vLLM-Omni TTS  
**Lip-sync:** Server-side PantoMatrix (`server/pantomatrix.py`) runs in a thread executor after TTS streaming, sends `blendshape_matrix` over WebSocket at 30 FPS ARKit blendshapes  
**Container:** Docker with multi-arch (CPU + GPU/CUDA 12.4)

---

## File Structure

```
V3/
├── server/                    # Python backend package
│   ├── main.py                # FastAPI app — /ws/s2s, /health, /chat
│   ├── pipeline_orchestrator.py # PipelineOrchestrator + IndicTrans2Engine (CT2)
│   ├── sentence_buffer.py     # SentenceBuffer — streaming text fragmentation
│   └── download_models.py     # Pre-downloads IndicTrans2 CT2 models
├── src/
│   ├── avatar3d.js            # Three.js 3D avatar, blendshape animation matrix
│   ├── behavior.js            # BehaviorManager — idle breathing, blink, gaze, emotions
│   └── stt.js                 # S2SManager — /ws/s2s WebSocket client
├── main.js                    # App entry: S2SManager wiring + avatar driving
├── avatar-widget.js           # Embeddable standalone widget (PantoMatrix + emotions)
├── index.html                 # Main UI
├── widget-demo.html           # Standalone widget demo
├── index.css                  # Styles
├── vite.config.js             # Vite dev server + proxy config
├── Dockerfile.server          # Server container (no PyTorch — CTranslate2 only)
├── Dockerfile.frontend        # Nginx static frontend container
└── docker-compose.yml         # Orchestrates: orchestrator + vexyl-stt + vllm-omni + frontend
```

---

## Pipeline Flow

```
Mic (Int16 PCM 16kHz)
   │
   ▼ WS /ws/s2s
┌──────────────────────────────────────────────────────┐
│                 PipelineOrchestrator                  │
│  Vexyl STT → SentenceBuffer → IndicTrans2 (→EN)       │
│            → LLM → IndicTrans2 (→Indic) → OmniVoice  │
└──────────────────────────────────────────────────────┘
   │ float32 PCM chunks + JSON control messages
   ▼
Browser (S2SManager)
    │ schedules audio via AudioContext
    │ buffers PCM until blendshape_matrix arrives (max 500ms)
    ▼
Server-side PantoMatrix (run_in_executor)
    │ [{time, blendshapes}] @ 30 FPS
    ▼
Avatar3D.setAnimationMatrix()     ← drives ARKit blendshapes
    + BehaviorManager              ← idle breathing, blink, gaze, emotions
```

---

## Key Latency Strategies

| Strategy | Implementation |
|---|---|
| Sentence chunking | `SentenceBuffer` fires on `.?!।\|` without waiting for full utterance |
| Async pipeline stages | 5 `asyncio.Queue`-connected workers — each stage runs independently |
| Parallel processing | Sentence N+1 translates while sentence N plays |
| Gapless audio | `nextPlayTime` cursor on `AudioContext` — chunks scheduled back-to-back |
| Client-side blendshapes | PantoMatrix runs in JS after last PCM chunk arrives (no server round-trip) |

---

## Emotion System

6 emotions fully implemented: `neutral`, `happy`, `sad`, `angry`, `surprised`, `fearful`

- **`BehaviorManager`** — interpolates breathing speed, head sway, and 19 blendshape weights per emotion
- **`Avatar3D.setEmotion()`** — immediately forces morph targets with no 1-frame lag  
- **During speech**: emotion weights dormant; `Avatar3D` drives all shapes from the PantoMatrix output  
- **During idle**: `BehaviorManager.emotionWeights` lerp smoothly at 5 rad/s
