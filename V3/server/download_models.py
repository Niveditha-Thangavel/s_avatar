"""
download_models.py — Pre-download all models at Docker build time.

Runs on CPU only. All models are open-weight — no HF_TOKEN required.
Cache paths are set via env vars and match the runtime engine defaults.
"""

import importlib.util
import logging
import os

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("download_models")

# Cache dirs — must match what engines use at runtime
HF_CACHE   = os.environ.get("TRANSFORMERS_CACHE", "/root/.cache/huggingface")
WHISPER_CACHE = os.environ.get("WHISPER_CACHE",   "/root/.cache/whisper")

os.makedirs(HF_CACHE, exist_ok=True)
os.makedirs(WHISPER_CACHE, exist_ok=True)

os.environ["TOKENIZERS_PARALLELISM"] = "false"


# ── 1. Whisper large-v3-turbo ─────────────────────────────────────────────
log.info("[1/4] Whisper large-v3-turbo …")
try:
    from faster_whisper import WhisperModel
    WhisperModel("large-v3-turbo", device="cpu", compute_type="int8",
                 download_root=WHISPER_CACHE)
    log.info("[1/4] ✅ Whisper")
except Exception as e:
    log.error("[1/4] ❌ %s", e)


# ── 2. Granite 4.0 Nano ───────────────────────────────────────────────────
log.info("[2/4] Granite ibm-granite/granite-4.0-1b …")
try:
    from transformers import AutoModelForCausalLM, AutoTokenizer
    import torch
    tok = AutoTokenizer.from_pretrained("ibm-granite/granite-4.0-1b", cache_dir=HF_CACHE)
    mdl = AutoModelForCausalLM.from_pretrained(
        "ibm-granite/granite-4.0-1b",
        device_map="cpu", dtype=torch.float32, cache_dir=HF_CACHE,
    )
    # Quick sanity — generate one token to verify
    ids = tok(tok.apply_chat_template(
        [{"role": "user", "content": "Hi."}],
        tokenize=False, add_generation_prompt=True,
    ), return_tensors="pt")
    out = mdl.generate(**ids, max_new_tokens=4)
    log.info("[2/4] ✅ Granite: %s",
             tok.decode(out[0][ids["input_ids"].shape[1]:], skip_special_tokens=True))
except Exception as e:
    log.error("[2/4] ❌ %s", e)


# ── 3. SMaLL-100 (tokenizer + model) ──────────────────────────────────────
log.info("[3/4] SMaLL-100 alirezamsh/small100 …")
try:
    from huggingface_hub import hf_hub_download
    from transformers import M2M100ForConditionalGeneration

    # Download & patch tokenizer for transformers 5.x
    tok_path = hf_hub_download(
        "alirezamsh/small100", "tokenization_small100.py", cache_dir=HF_CACHE,
    )
    with open(tok_path, "r") as f:
        src = f.read()
    old = "from transformers.tokenization_utils import BatchEncoding, PreTrainedTokenizer"
    new = ("from transformers.tokenization_utils_base import BatchEncoding\n"
           "from transformers.tokenization_utils import PreTrainedTokenizer")
    if old in src:
        with open(tok_path, "w") as f:
            f.write(src.replace(old, new))
        log.info("[3/4] Patched tokenization_small100.py for transformers 5.x")

    spec = importlib.util.spec_from_file_location("tok100", tok_path)
    mod  = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    tok  = mod.SMALL100Tokenizer.from_pretrained("alirezamsh/small100", cache_dir=HF_CACHE)
    mdl  = M2M100ForConditionalGeneration.from_pretrained(
        "alirezamsh/small100", dtype=torch.float32, cache_dir=HF_CACHE,
    )

    # Quick sanity
    tok.tgt_lang = "hi"
    enc = tok("Hello.", return_tensors="pt")
    out = mdl.generate(**enc, forced_bos_token_id=tok.get_lang_id("hi"),
                       num_beams=2, max_length=16)
    log.info("[3/4] ✅ SMaLL-100 en→hi: %s",
             tok.batch_decode(out, skip_special_tokens=True))
except Exception as e:
    log.error("[3/4] ❌ %s", e)


# ── 4. OmniVoice ──────────────────────────────────────────────────────────
log.info("[4/4] OmniVoice k2-fsa/OmniVoice …")
try:
    from omnivoice import OmniVoice
    import torch
    OmniVoice.from_pretrained("k2-fsa/OmniVoice", device_map="cpu", dtype=torch.float32)
    log.info("[4/4] ✅ OmniVoice")
except Exception as e:
    log.error("[4/4] ❌ %s", e)


log.info("Download complete — models cached at:")
log.info("  HF:     %s", HF_CACHE)
log.info("  Whisper: %s", WHISPER_CACHE)
