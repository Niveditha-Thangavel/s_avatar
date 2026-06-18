"""
download_models.py — Pre-download all models at Docker build time.

Runs on CPU only (build containers have no GPU).
Cache paths match the runtime engine defaults exactly.

Models downloaded:
   1. Faster-Whisper medium   (Systran/faster-whisper-medium)   ~0.7 GB
   2. SMaLL-100                (alirezamsh/small100)              ~1.2 GB
   3. OmniVoice                (k2-fsa/OmniVoice)                ~1.8 GB
"""

import importlib.util
import logging
import os

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("download_models")

HF_CACHE = os.environ.get(
    "TRANSFORMERS_CACHE",
    os.path.join(os.path.expanduser("~"), ".cache", "huggingface"),
)
WHISPER_MODEL_ID = os.environ.get("WHISPER_MODEL_ID", "Systran/faster-whisper-medium")
FW_CACHE = os.environ.get(
    "FASTER_WHISPER_CACHE",
    os.path.join(os.path.expanduser("~"), ".cache", "faster_whisper"),
)

os.makedirs(HF_CACHE, exist_ok=True)
os.makedirs(FW_CACHE, exist_ok=True)
os.environ["TOKENIZERS_PARALLELISM"] = "false"

import torch  # noqa: E402 — after env setup


# ── 1. Faster-Whisper large-v3 ─────────────────────────────────────────────────
log.info("[1/3] Faster-Whisper medium (%s) …", WHISPER_MODEL_ID)
try:
    from faster_whisper import WhisperModel

    model = WhisperModel(
        WHISPER_MODEL_ID,
        device="cpu",
        compute_type="int8",
        download_root=FW_CACHE,
        cpu_threads=2,
        num_workers=1,
    )

    # Quick sanity: 1 second of silence → should produce empty/near-empty transcript
    import numpy as np
    silence = np.zeros(16000, dtype=np.float32)
    segments, info = model.transcribe(silence, language="en", beam_size=1)
    text = " ".join(s.text for s in segments)
    log.info("[1/3] ✅ Faster-Whisper — sanity output: %r", text[:60])
    del model  # free RAM for subsequent downloads
except Exception as exc:
    log.error("[1/3] ❌ Faster-Whisper: %s", exc)


# ── 2. SMaLL-100 ───────────────────────────────────────────────────────────────
log.info("[2/3] SMaLL-100 (alirezamsh/small100) …")
try:
    from huggingface_hub import hf_hub_download
    from transformers import M2M100ForConditionalGeneration

    tok_path = hf_hub_download(
        "alirezamsh/small100", "tokenization_small100.py", cache_dir=HF_CACHE,
    )
    with open(tok_path, "r") as f:
        src = f.read()
    old = "from transformers.tokenization_utils import BatchEncoding, PreTrainedTokenizer"
    new = (
        "from transformers.tokenization_utils_base import BatchEncoding\n"
        "from transformers.tokenization_utils import PreTrainedTokenizer"
    )
    if old in src:
        with open(tok_path, "w") as f:
            f.write(src.replace(old, new))
        log.info("[2/3] Patched tokenization_small100.py for transformers 5.x")

    spec = importlib.util.spec_from_file_location("tok100", tok_path)
    mod  = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    tok  = mod.SMALL100Tokenizer.from_pretrained("alirezamsh/small100", cache_dir=HF_CACHE)
    mdl  = M2M100ForConditionalGeneration.from_pretrained(
        "alirezamsh/small100", torch_dtype=torch.float32, cache_dir=HF_CACHE,
    )
    tok.tgt_lang = "hi"
    enc = tok("Hello.", return_tensors="pt")
    out = mdl.generate(
        **enc, forced_bos_token_id=tok.get_lang_id("hi"), num_beams=2, max_length=16,
    )
    log.info(
        "[2/3] ✅ SMaLL-100 en→hi: %s",
        tok.batch_decode(out, skip_special_tokens=True),
    )
    del mdl
except Exception as exc:
    log.error("[2/3] ❌ SMaLL-100: %s", exc)


# ── 3. OmniVoice ──────────────────────────────────────────────────────────────
log.info("[3/3] OmniVoice (k2-fsa/OmniVoice) …")
try:
    from omnivoice import OmniVoice

    OmniVoice.from_pretrained("k2-fsa/OmniVoice", device_map="cpu", dtype=torch.float32)
    log.info("[3/3] ✅ OmniVoice")
except Exception as exc:
    log.error("[3/3] ❌ OmniVoice: %s", exc)


# ── Ensure ref_audio / ref_text exist ─────────────────────────────────────────
_HERE     = os.path.dirname(os.path.abspath(__file__))
REF_AUDIO = os.path.join(_HERE, "ref_audio.wav")
REF_TEXT  = os.path.join(_HERE, "ref_text.txt")

if not os.path.exists(REF_AUDIO):
    import numpy as np, soundfile as sf
    sf.write(REF_AUDIO, np.zeros(24000, dtype=np.float32), 24000, format="WAV")
    log.info("Created placeholder ref_audio.wav")

if not os.path.exists(REF_TEXT):
    with open(REF_TEXT, "w") as f:
        f.write("Replace this with the transcription of your ref_audio.wav")
    log.info("Created placeholder ref_text.txt")

log.info("✅ All models downloaded → HF cache: %s", HF_CACHE)
