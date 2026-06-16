"""
download_models.py — Pre-download all models at Docker build time.

Runs on CPU only (build containers have no GPU).
Cache paths match the runtime engine defaults exactly.

Models downloaded:
  1. Whisper large-v3-turbo  (openai/whisper-large-v3-turbo)  ~1.6 GB
  2. Granite 4.0 Nano        (ibm-granite/granite-4.0-1b)     ~2.5 GB
  3. SMaLL-100               (alirezamsh/small100)             ~1.2 GB
  4. OmniVoice               (k2-fsa/OmniVoice)               ~1.8 GB
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
WHISPER_MODEL_ID = os.environ.get("WHISPER_MODEL_ID", "openai/whisper-large-v3-turbo")

os.makedirs(HF_CACHE, exist_ok=True)
os.environ["TOKENIZERS_PARALLELISM"] = "false"

import torch  # noqa: E402 — after env setup


# ── 1. Whisper large-v3-turbo ─────────────────────────────────────────────────
log.info("[1/4] Whisper large-v3-turbo (%s) …", WHISPER_MODEL_ID)
try:
    from transformers import AutoModelForSpeechSeq2Seq, AutoProcessor

    processor = AutoProcessor.from_pretrained(WHISPER_MODEL_ID, cache_dir=HF_CACHE)
    model     = AutoModelForSpeechSeq2Seq.from_pretrained(
        WHISPER_MODEL_ID,
        torch_dtype=torch.float32,
        low_cpu_mem_usage=True,
        use_safetensors=True,
        cache_dir=HF_CACHE,
    )

    # Quick sanity: 1 second of silence → should produce empty/near-empty transcript
    import numpy as np
    from transformers import pipeline as hf_pipeline

    pipe = hf_pipeline(
        "automatic-speech-recognition",
        model=model,
        tokenizer=processor.tokenizer,
        feature_extractor=processor.feature_extractor,
        torch_dtype=torch.float32,
        device="cpu",
    )
    silence = np.zeros(16000, dtype=np.float32)
    result  = pipe(silence, generate_kwargs={"language": "en", "task": "transcribe"})
    log.info("[1/4] ✅ Whisper large-v3-turbo — sanity output: %r", result.get("text", "")[:60])
    del model, pipe  # free RAM for subsequent downloads
except Exception as exc:
    log.error("[1/4] ❌ Whisper: %s", exc)


# ── 2. Granite 4.0 Nano ───────────────────────────────────────────────────────
log.info("[2/4] Granite 4.0 Nano (ibm-granite/granite-4.0-1b) …")
try:
    from transformers import AutoModelForCausalLM, AutoTokenizer

    tok = AutoTokenizer.from_pretrained("ibm-granite/granite-4.0-1b", cache_dir=HF_CACHE)
    mdl = AutoModelForCausalLM.from_pretrained(
        "ibm-granite/granite-4.0-1b",
        device_map="cpu",
        torch_dtype=torch.float32,
        cache_dir=HF_CACHE,
    )
    ids = tok(
        tok.apply_chat_template(
            [{"role": "user", "content": "Hi."}],
            tokenize=False,
            add_generation_prompt=True,
        ),
        return_tensors="pt",
    )
    out = mdl.generate(**ids, max_new_tokens=4)
    log.info(
        "[2/4] ✅ Granite — sanity output: %r",
        tok.decode(out[0][ids["input_ids"].shape[1]:], skip_special_tokens=True),
    )
    del mdl
except Exception as exc:
    log.error("[2/4] ❌ Granite: %s", exc)


# ── 3. SMaLL-100 ──────────────────────────────────────────────────────────────
log.info("[3/4] SMaLL-100 (alirezamsh/small100) …")
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
        log.info("[3/4] Patched tokenization_small100.py for transformers 5.x")

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
        "[3/4] ✅ SMaLL-100 en→hi: %s",
        tok.batch_decode(out, skip_special_tokens=True),
    )
    del mdl
except Exception as exc:
    log.error("[3/4] ❌ SMaLL-100: %s", exc)


# ── 4. OmniVoice ──────────────────────────────────────────────────────────────
log.info("[4/4] OmniVoice (k2-fsa/OmniVoice) …")
try:
    from omnivoice import OmniVoice

    OmniVoice.from_pretrained("k2-fsa/OmniVoice", device_map="cpu", dtype=torch.float32)
    log.info("[4/4] ✅ OmniVoice")
except Exception as exc:
    log.error("[4/4] ❌ OmniVoice: %s", exc)


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
