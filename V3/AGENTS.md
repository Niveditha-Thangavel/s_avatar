# AGENTS — Project State

## Architecture

**Pipeline:** Mic input → Whisper large-v3-turbo STT → SMaLL-100 (→ EN) → Granite 4.0 Nano LLM → SMaLL-100 (→ native) → OmniVoice TTS → PantoMatrix (blendshapes) → unified JSON `{ audio_url, animation_matrix }`

**Two client paths:**
1. **Speak button** (typed text) → `POST /chat` (text) → `/ws/tts` WebSocket → raw Float32 PCM audio → no animation matrix (legacy path)
2. **Mic button** (recorded audio) → `POST /api/v1/chat` (WAV file) → unified pipeline → `{ audio_url, animation_matrix }`

## What's Been Done

### Infrastructure
- Docker: `Dockerfile.server`, `Dockerfile.nginx`, `docker-compose.yml`, `docker-compose.gpu.yml`, `.dockerignore`
- All engines support MPS (Mac) + CUDA + CPU fallback
- Cache paths use `os.path.expanduser("~/.cache/...")` for cross-platform
- Models pre-downloaded at build time via `download_models.py`
- GPU builds use `pytorch/pytorch:2.5.1-cuda12.4-cudnn9-runtime` base image
- `SKIP_TORCH_INSTALL=true` for GPU builds (conda ships torch)

### Engine Files
| File | Description |
|---|---|
| `server/main.py` | FastAPI app — `/api/v1/chat` (unified), `/chat` (text), `/ws/tts`, `/ws/stt`, `/health`, audio serving |
| `server/stt_engine.py` | Whisper large-v3-turbo — returns `(text, lang, emotion)` |
| `server/llm_engine.py` | Granite 4.0 Nano — emotion tokens + user_emotion in system prompt |
| `server/translation_engine.py` | SMaLL-100 — English → native, native → English |
| `server/tts_engine.py` | OmniVoice at 24000 Hz, sentence streaming |
| `server/pantomatrix.py` | 52 ARKit blendshapes at 30 FPS (placeholder heuristics) |
| `server/download_models.py` | Pre-downloads all 4 models |

### Frontend Files
| File | Description |
|---|---|
| `main.js` | App entry — speak button, mic button, audio playback, animation matrix |
| `src/avatar3d.js` | Three.js avatar — `setAnimationMatrix()`, `_updateFromMatrix()`, `isSpeaking` guard |
| `src/behavior.js` | Idle procedural motion only (speech bobbing removed) |
| `src/stt.js` | STTManager — mic recording, `getRecordingBlob()` returns WAV Blob |
| `vite.config.js` | Proxy `/ws`, `/chat`, `/health`, `/api` to localhost:8765 |

### Bug Fixes Applied
- **Legacy text lipsync deleted** (`V3/src/lipsync.js`)
- **`from tts_engine` import** → `from server.tts_engine` (Python 3.12 package imports)
- **`romanized_text` removed** from all endpoints
- **`torch_dtype` → `dtype`** (deprecation fix across all engines)
- **OmniVoice dtype** from string `"float32"` to `torch.float32`
- **SenseVoiceSmall → Whisper large-v3-turbo** (rewrote stt_engine, download_models, requirements)
- **User emotion injected** into LLM system prompt
- **Dual audio race fixed**: `onReply` permanently replaced with no-op during mic flow
- **Lipsync overwrite fixed**: `behavior.emotionWeights` guarded by `!this.isSpeaking`
- **Missing deps**: `anyascii`, `uroman` re-added to `requirements.txt`
- **`__init__.py`** added to `server/` package
- **WAV subtype**: `sf.write(..., subtype="FLOAT")` — was writing PCM_16, frontend parsed as Float32
- **WS TTS animation**: `/ws/tts` now accumulates audio, computes PantoMatrix, sends in complete message
- **Frontend WS handling**: `_speakViaLegacyTTS` captures `animation_matrix` from complete message

## Bug Fixes (latest session)

### PantoMatrix crash — `smile_factor` NameError (FIXED)
- `cheekSquintLeft/Right` referenced `smile_factor` which was never assigned
- Crashed `extract_blendshapes()` on every call → zero animation matrix always
- Fix: added `smile_factor = max(0.0, (centroid_n - 0.50) / 0.50) * nrg_norm`

### PantoMatrix — viseme-class driven rewrite (v4)
Root problem: single energy+centroid scalar cannot distinguish phoneme shapes.
Every voiced frame had similar centroid → same jaw position for every word.

New architecture:
- Per-frame sub-band energy ratios: B_low (0–500Hz), B_mid (500–2kHz), B_high (2kHz+)
- ZCR (zero-crossing rate) combined with sub-bands to classify 7 phoneme classes:
  SILENCE / LOW_VOWEL(/a/,/o/) / MID_VOWEL(/ɛ/,/ɪ/) / HIGH_VOWEL(/i/,/e/) /
  FRICATIVE(/v/,/ð/) / SIBILANT(/s/,/ʃ/,/f/) / BILABIAL(/p/,/b/,/m/)
- Each class has a distinct target pose (different jaw, lip, press, smile shapes)
- Energy scaling via sqrt(nrg_norm) preserves amplitude dynamics within class
- Asymmetric jaw alpha: fast attack (0.88), slower release (0.66)
- 95th-percentile energy normalization

Validated on real TTS audio:
- jawOpen range: 0.83–0.85 (was 0.67)
- Unique jaw positions per sentence: 41–51 (was ~20)
- mouthPress active for sibilants, mouthSmile for high vowels, lipsSeal for bilabials

### Avatar3D frame lookup and lerp (FIXED)
Problems fixed:
- Frame lookup was O(n) scan with ±0.02s tolerance — wrong frame selected at 30fps
- No interpolation between frames — stepped jaw movement at 60fps
- `lerp(current, target, 0.25)` per render frame added ~4-frame lag on top of already-smoothed data
Fixes applied:
- Binary search for floor frame index (O(log n))
- Linear interpolation between frameA and frameB using fractional position
- Direct assignment of interpolated weights — no additional lerp in render
- Morphtarget application moved into render() with case-insensitive fallback

### Audio/animation start time sync (FIXED)
- `setAnimationMatrix` was called AFTER `source.start(0)` → systematic animation offset
- Fix: `audioStartTime = audioContext.currentTime` → `setAnimationMatrix()` → `source.start(0)` — all atomic
- Render loop passes `audioContext.currentTime - audioStartTime` as elapsed directly to `_updateFromMatrix(elapsed)`
- `_updateFromMatrix` now takes a single `elapsed` arg (no `isElapsed` flag)

## Remaining Known Issues

### Server reload: `--reload` flag broken on macOS
- `uvicorn --reload` spawns a subprocess via multiprocessing
- Subprocess loses venv `sys.path` on macOS framework Python
- Workaround: run without `--reload`, restart manually

## How to Run

```bash
# Terminal 1 — backend (use the script, NOT bare uvicorn — see segfault note below)
cd V3
./start_server.sh

# Terminal 2 — frontend
cd V3
npm run dev
# Open http://localhost:3005
```

### Why bare `uvicorn` segfaults on macOS

macOS Python 3.12 uses `spawn` for multiprocessing by default. When uvicorn
starts its event loop and simultaneously PyTorch initializes the MPS allocator,
the OS-level semaphore for the resource tracker is created in the parent and
then inherited (not copied) by the spawned worker — causing a segfault or
leaked-semaphore warning on shutdown.

`start_server.sh` fixes this by:
1. Setting `OBJC_DISABLE_INITIALIZE_FORK_SAFETY=YES`
2. Running with `--loop asyncio` (no subprocess workers)
3. Killing any stale port 8765 occupant first

## Relevant Ports
- Backend: 8765 (uvicorn)
- Frontend: 3005 (Vite dev)

## Cache Locations
- Whisper large-v3-turbo: `~/.cache/huggingface/` (~1.6GB)
- HuggingFace models (Granite, SMaLL-100, OmniVoice): `~/.cache/huggingface/` (~6.5GB)
