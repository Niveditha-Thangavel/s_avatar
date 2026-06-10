# Voice Avatar Server

Self-hosted Python backend for the 3D Digital Human. Runs two AI models
entirely on-device — no cloud APIs, no API keys required.

| Model | Task | Library |
|---|---|---|
| `k2-fsa/OmniVoice` | Text-to-Speech (voice cloning) | `omnivoice` |
| `whisper-large-v3-turbo` | Speech-to-Text | `faster-whisper` |

---

## Architecture

```
Browser / test script
       │
       ├── WS  /ws/stt  ── raw Int16 PCM (16 kHz mono)
       │                    └─ accumulate all frames
       │                    └─ on "stop": Whisper transcribes full buffer
       │                    └─ chat.py generates reply text
       │                    └─ sends back: transcript + reply
       │
       ├── WS  /ws/tts  ── { type:"speak", text, speed, numStep }
       │                    └─ OmniVoice synthesises audio
       │                    └─ streams back JSON header + binary PCM per sentence
       │
       └── HTTP /chat   ── { text } → { reply }  (typed text shortcut)
```

STT design note: There is **no custom VAD**. All incoming audio frames are
buffered and sent to Whisper in one shot when the client sends `stop`.
Whisper's own built-in Silero VAD (`vad_filter=True`) handles internal
silence stripping. This avoids the classic problem of energy-based VAD
splitting natural inter-word pauses into separate utterances.

---

## File Structure

```
server/
├── main.py            # FastAPI app — WebSocket + HTTP endpoints
├── tts_engine.py      # OmniVoice wrapper (lazy singleton, streaming)
├── stt_engine.py      # faster-whisper wrapper + AudioBuffer accumulator
├── chat.py            # Response layer (placeholder → swap in your LLM)
├── ref_audio.wav      # Reference voice for OmniVoice cloning (you provide this)
├── requirements.txt   # Python dependencies
├── start.sh           # Startup script with disk check + env setup
└── test_pipeline.py   # CLI end-to-end test script
```

---

## Quick Start (Mac / CPU)

```bash
# 1. Install PyTorch for CPU
pip install torch==2.5.1 torchaudio==2.5.1 \
  --index-url https://download.pytorch.org/whl/cpu

# 2. Start the server (creates venv, installs deps, checks disk)
bash start.sh
```

The first run downloads model weights (~3 GB OmniVoice + ~1.5 GB Whisper)
into `~/.cache/huggingface/`. Subsequent starts load from cache in ~2s.

---

## Reference Voice

OmniVoice requires a reference audio file for voice cloning.

- Place a **3–10 second** clear WAV/MP3/FLAC recording at `server/ref_audio.wav`
- The speaker's voice in this file is what the avatar will sound like
- If the file is missing, OmniVoice falls back to auto-voice mode

No transcript file is needed — OmniVoice uses its internal Whisper to
auto-transcribe the reference on the first synthesis call.

---

## Testing

```bash
# Full pipeline: WAV → STT → chat → TTS → reply.wav
python test_pipeline.py my_question.wav

# Skip STT — send text directly to TTS
python test_pipeline.py --text "Hello, how are you today?" --out reply.wav

# Higher quality synthesis (slower)
python test_pipeline.py my_question.wav --steps 32 --speed 0.9
```

---

## Swapping in a Real LLM

Open `chat.py` and replace `get_response()`. The rest of the server is
untouched. **Only self-hosted LLMs are allowed — no external API calls.**

```python
# Example: Ollama (local)
import httpx

async def get_response(user_text: str) -> str:
    r = await httpx.AsyncClient().post(
        "http://localhost:11434/api/generate",
        json={"model": "llama3", "prompt": user_text, "stream": False},
        timeout=60.0,
    )
    return r.json()["response"]
```

```python
# Example: llama-cpp-python (in-process)
from llama_cpp import Llama
_llm = Llama(model_path="models/llama3.gguf", n_ctx=2048)

async def get_response(user_text: str) -> str:
    out = _llm(user_text, max_tokens=256)
    return out["choices"][0]["text"].strip()
```

---

## Running on an NVIDIA GPU

### 1. Install PyTorch with CUDA

```bash
# CUDA 12.4 (most common for RTX 30xx / 40xx)
pip install torch==2.5.1 torchaudio==2.5.1 \
  --index-url https://download.pytorch.org/whl/cu124

# CUDA 11.8 (older GPUs)
pip install torch==2.5.1 torchaudio==2.5.1 \
  --index-url https://download.pytorch.org/whl/cu118
```

### 2. Set environment variables before starting

```bash
export OMNIVOICE_DEVICE=cuda:0
export WHISPER_DEVICE=cuda
```

Or edit `start.sh` — replace the CPU lines:

```bash
# FROM (Mac/CPU defaults):
export OMNIVOICE_DEVICE=cpu
export WHISPER_DEVICE=cpu

# TO (NVIDIA GPU):
export OMNIVOICE_DEVICE=cuda:0
export WHISPER_DEVICE=cuda
```

### 3. What changes internally

| Setting | Mac CPU | NVIDIA GPU |
|---|---|---|
| `OMNIVOICE_DEVICE` | `cpu` | `cuda:0` |
| OmniVoice dtype | `torch.float32` | `torch.float16` (auto) |
| `WHISPER_DEVICE` | `cpu` | `cuda` |
| Whisper compute type | `int8` | `float16` (auto) |
| OmniVoice speed | ~12× slower than real-time | ~2–5× faster than real-time |
| Whisper speed | ~2× real-time | ~30× real-time |

### 4. NVIDIA prerequisites

faster-whisper requires these CUDA libraries to be installed on the system:

- cuBLAS for CUDA 12 — [install guide](https://developer.nvidia.com/cublas)
- cuDNN 9 for CUDA 12 — [install guide](https://developer.nvidia.com/cudnn)

On Ubuntu these can be installed via:
```bash
sudo apt install libcublas-12-4 libcudnn9-cuda-12
```

### 5. Multi-GPU

To split models across GPUs:
```bash
export OMNIVOICE_DEVICE=cuda:0   # OmniVoice on GPU 0
export WHISPER_DEVICE=cuda:1     # Whisper on GPU 1
```

---

## WebSocket Protocol Reference

### `/ws/stt`

| Direction | Frame | Meaning |
|---|---|---|
| Client → Server | Binary | Raw Int16 PCM, 16 kHz mono. Send as many frames as needed. |
| Client → Server | `{"type":"config","language":"en"}` | Optional language hint. Send before streaming. |
| Client → Server | `{"type":"stop"}` | Flush buffer → Whisper runs → reply sent back. |
| Client → Server | `{"type":"cancel"}` | Discard buffer without transcribing. |
| Server → Client | `{"type":"status","data":"listening"}` | Ready for audio. |
| Server → Client | `{"type":"status","data":"transcribing"}` | Whisper is running. |
| Server → Client | `{"type":"status","data":"thinking"}` | chat.py is generating a reply. |
| Server → Client | `{"type":"transcript","text":"..."}` | What the user said. |
| Server → Client | `{"type":"reply","text":"..."}` | What the avatar will say. |
| Server → Client | `{"type":"status","data":"stopped"}` | Final state — done. |

### `/ws/tts`

| Direction | Frame | Meaning |
|---|---|---|
| Client → Server | `{"type":"speak","text":"...","speed":1.0,"numStep":16}` | Start synthesis. |
| Client → Server | `{"type":"stop"}` | Acknowledge stop. |
| Server → Client | `{"type":"status","data":"generating"}` | Synthesis started. |
| Server → Client | `{"type":"chunk","text":"...","sampleRate":24000,"byteLength":N}` | Audio header. |
| Server → Client | Binary | Raw float32-le PCM immediately after each chunk header. |
| Server → Client | `{"type":"status","data":"complete"}` | All audio sent. |

Audio format out of TTS: **float32-le, mono, 24 kHz**.

---

## Troubleshooting

**`Killed: 9` on startup**
Disk is full. OmniVoice needs ~3 GB free for weights + MPS shader cache.
Run `bash start.sh` — it checks available space before starting.

**Only first word transcribed**
Old issue from custom energy-VAD — now removed. All audio is buffered and
sent to Whisper in one shot on `stop`.

**TTS is very slow**
Expected on CPU — OmniVoice is a diffusion model. Use `--steps 16` (default)
for fastest output. Switch to NVIDIA GPU for real-time performance.

**OmniVoice sounds like a random voice**
`server/ref_audio.wav` is missing. Place a 3–10s voice sample there.
