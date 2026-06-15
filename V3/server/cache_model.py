"""
cache_model.py — pre-downloads all models at Docker build time.
Runs on CPU only (no GPU needed during build).

Pipeline models cached:
  1. Whisper Large-v3-Turbo  (openai/whisper-large-v3-turbo via faster-whisper)
  2. Granite 4.0 Nano 1B     (ibm-granite/granite-4.0-1b)     Apache 2.0
  3. SMaLL-100               (alirezamsh/small100)             MIT
  4. OmniVoice               (k2-fsa/OmniVoice)                Apache 2.0
All open-weight — no HF_TOKEN required.
"""
import importlib.util
import logging
import os
import sys
import torch

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log    = logging.getLogger(__name__)
CACHE  = os.environ.get("TRANSFORMERS_CACHE", "/app/.cache/huggingface")
WCACHE = os.environ.get("WHISPER_CACHE",      "/app/.cache/whisper")

# ── 1. Whisper ────────────────────────────────────────────────────────────────
log.info("[Cache] 1/4 Whisper large-v3-turbo …")
try:
    from faster_whisper import WhisperModel
    WhisperModel("large-v3-turbo", device="cpu", compute_type="int8", download_root=WCACHE)
    log.info("[Cache] ✅ Whisper")
except Exception as e:
    log.error("[Cache] ❌ Whisper: %s", e)

# ── 2. Granite 4.0 Nano ───────────────────────────────────────────────────────
log.info("[Cache] 2/4 Granite ibm-granite/granite-4.0-1b …")
try:
    from transformers import AutoModelForCausalLM, AutoTokenizer
    tok = AutoTokenizer.from_pretrained("ibm-granite/granite-4.0-1b", cache_dir=CACHE)
    mdl = AutoModelForCausalLM.from_pretrained(
        "ibm-granite/granite-4.0-1b",
        device_map="cpu", torch_dtype=torch.float32, cache_dir=CACHE,
    )
    ids  = tok(tok.apply_chat_template(
        [{"role":"user","content":"Hi."}], tokenize=False, add_generation_prompt=True
    ), return_tensors="pt")
    out  = mdl.generate(**ids, max_new_tokens=8)
    log.info("[Cache] ✅ Granite: %s",
             tok.decode(out[0][ids["input_ids"].shape[1]:], skip_special_tokens=True))
except Exception as e:
    log.error("[Cache] ❌ Granite: %s", e)

# ── 3. SMaLL-100 ──────────────────────────────────────────────────────────────
log.info("[Cache] 3/4 SMaLL-100 alirezamsh/small100 …")
try:
    from huggingface_hub import hf_hub_download
    from transformers import M2M100ForConditionalGeneration

    tok_path = hf_hub_download(
        "alirezamsh/small100", "tokenization_small100.py", cache_dir=CACHE
    )
    # Patch for transformers 5.x — BatchEncoding moved module
    with open(tok_path, "r") as f: src = f.read()
    old = "from transformers.tokenization_utils import BatchEncoding, PreTrainedTokenizer"
    new = ("from transformers.tokenization_utils_base import BatchEncoding\n"
           "from transformers.tokenization_utils import PreTrainedTokenizer")
    if old in src:
        with open(tok_path, "w") as f: f.write(src.replace(old, new))
        log.info("[Cache] Patched tokenization_small100.py for transformers 5.x")
    spec = importlib.util.spec_from_file_location("tok100", tok_path)
    mod  = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    tok  = mod.SMALL100Tokenizer.from_pretrained("alirezamsh/small100", cache_dir=CACHE)
    mdl  = M2M100ForConditionalGeneration.from_pretrained(
        "alirezamsh/small100", torch_dtype=torch.float32, cache_dir=CACHE
    )
    tok.tgt_lang = "hi"
    enc = tok("Hello, how are you?", return_tensors="pt")
    out = mdl.generate(**enc, forced_bos_token_id=tok.get_lang_id("hi"),
                       num_beams=2, max_length=32)
    log.info("[Cache] ✅ SMaLL-100 en→hi: %s",
             tok.batch_decode(out, skip_special_tokens=True))
except Exception as e:
    log.error("[Cache] ❌ SMaLL-100: %s", e)

# ── 4. OmniVoice ─────────────────────────────────────────────────────────────
log.info("[Cache] 4/4 OmniVoice k2-fsa/OmniVoice …")
try:
    from omnivoice import OmniVoice
    OmniVoice.from_pretrained("k2-fsa/OmniVoice", device_map="cpu", dtype=torch.float32)
    log.info("[Cache] ✅ OmniVoice")
except Exception as e:
    log.error("[Cache] ❌ OmniVoice: %s", e)

log.info("[Cache] Done — failures will download at first request")
sys.exit(0)
