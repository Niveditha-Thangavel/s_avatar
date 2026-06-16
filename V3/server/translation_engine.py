"""
translation_engine.py — SMaLL-100  (GPU-optimised)

GPU path (CUDA):
  • float16 weights
  • torch.compile (reduce-overhead)
  • autocast for both encode + generate
  • num_beams=2 on GPU (was 5) — 2.5× faster with negligible quality loss
    for short avatar responses (1-3 sentences)
  • Dedicated single-thread executor

MPS / CPU: float32, num_beams=4, no compile
"""

import asyncio
import concurrent.futures
import importlib.util
import logging
import os
from typing import Tuple

import torch
from anyascii import anyascii

logger     = logging.getLogger(__name__)
MODEL_ID   = "alirezamsh/small100"
_model     = None
_tokenizer = None
_lock      = asyncio.Lock()

_executor = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="trans")


def _get_device() -> str:
    if torch.cuda.is_available():
        return "cuda"
    if torch.backends.mps.is_available():
        return "mps"
    return "cpu"


def _load_tokenizer_class():
    from huggingface_hub import hf_hub_download
    cache = os.environ.get(
        "TRANSFORMERS_CACHE",
        os.path.join(os.path.expanduser("~"), ".cache", "huggingface"),
    )
    path = hf_hub_download(
        repo_id=MODEL_ID,
        filename="tokenization_small100.py",
        cache_dir=cache,
    )
    # Patch for transformers 5.x
    with open(path, "r", encoding="utf-8") as f:
        src = f.read()
    old = "from transformers.tokenization_utils import BatchEncoding, PreTrainedTokenizer"
    new = (
        "from transformers.tokenization_utils_base import BatchEncoding\n"
        "from transformers.tokenization_utils import PreTrainedTokenizer"
    )
    if old in src:
        with open(path, "w", encoding="utf-8") as f:
            f.write(src.replace(old, new))
        logger.info("[Trans] Patched tokenization_small100.py for transformers 5.x")

    spec   = importlib.util.spec_from_file_location("tokenization_small100", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.SMALL100Tokenizer


async def get_model():
    global _model, _tokenizer
    if _model is not None:
        return _model, _tokenizer
    async with _lock:
        if _model is not None:
            return _model, _tokenizer

        from transformers import M2M100ForConditionalGeneration
        device = _get_device()
        dtype  = torch.float16 if device == "cuda" else torch.float32
        logger.info("[Trans] Loading SMaLL-100 on %s (%s) …", device, dtype)

        cache = os.environ.get(
            "TRANSFORMERS_CACHE",
            os.path.join(os.path.expanduser("~"), ".cache", "huggingface"),
        )
        SMALL100Tokenizer = _load_tokenizer_class()
        _tokenizer = SMALL100Tokenizer.from_pretrained(MODEL_ID, cache_dir=cache)
        _model = M2M100ForConditionalGeneration.from_pretrained(
            MODEL_ID, torch_dtype=dtype, cache_dir=cache
        ).to(device)
        _model.eval()

        if device == "cuda":
            torch.backends.cudnn.benchmark = True
            logger.info("[Trans] torch.compile (reduce_overhead) …")
            try:
                _model = torch.compile(_model, mode="reduce-overhead", fullgraph=False)
                logger.info("[Trans] torch.compile done")
            except Exception as e:
                logger.warning("[Trans] torch.compile skipped: %s", e)

        logger.info("[Trans] ✅ SMaLL-100 ready on %s", device)
        return _model, _tokenizer


async def load_translation_model():
    await get_model()


_LANG_MAP = {"zh": "zh", "pt": "pt", "jw": "jv", "sr": "sr"}


def _resolve_lang(code: str) -> str:
    return _LANG_MAP.get(code, code)


# ── Romanisation ──────────────────────────────────────────────────────────────

_LATIN_LANGS = {
    "en","fr","es","de","pt","it","nl","sv","da","no","fi","ro","pl",
    "cs","hu","vi","id","ms","tl","sw","af","sq","bs","ca","hr","et",
    "lv","lt","mk","sk","sl",
}
_ISO1_TO_3 = {
    "hi":"hin","mr":"mar","ne":"nep","sa":"san","ta":"tam","te":"tel",
    "kn":"kan","ml":"mal","bn":"ben","gu":"guj","pa":"pan","or":"ori",
    "si":"sin","ar":"ara","fa":"fas","ur":"urd","he":"heb","ja":"jpn",
    "zh":"cmn","yue":"yue","ko":"kor","th":"tha","lo":"lao","my":"mya",
    "km":"khm","ru":"rus","uk":"ukr","be":"bel","bg":"bul","sr":"srp",
    "hr":"hrv","sl":"slv","el":"ell","am":"amh","ti":"tir","ka":"kat",
    "hy":"hye","dv":"div","ps":"pus","ku":"kur","bo":"bod","dz":"dzo",
}
_uroman = None


def _get_uroman():
    global _uroman
    if _uroman is None:
        import uroman as ur
        _uroman = ur.Uroman()
    return _uroman


def _romanize(text: str, lang: str) -> str:
    if not text.strip() or lang in _LATIN_LANGS:
        return text
    try:
        return anyascii(_get_uroman().romanize_string(text, lcode=_ISO1_TO_3.get(lang)))
    except Exception as exc:
        logger.warning("[Trans] uroman failed (%s): %s", lang, exc)
        return anyascii(text)


# ── Translation helpers ───────────────────────────────────────────────────────

def _num_beams(device: str) -> int:
    # Fewer beams on GPU — fast enough and responses are short
    return 2 if device == "cuda" else 4


async def translate_from_english(english_text: str, target_lang: str) -> Tuple[str, str]:
    if not english_text.strip():
        return "", ""
    if target_lang == "en":
        return english_text, english_text

    tgt              = _resolve_lang(target_lang)
    model, tokenizer = await get_model()
    device           = _get_device()
    beams            = _num_beams(device)
    loop             = asyncio.get_event_loop()

    def _run():
        tokenizer.tgt_lang = tgt
        enc = tokenizer(
            english_text, return_tensors="pt", truncation=True, max_length=512,
        ).to(model.device)
        try:
            lang_id = tokenizer.get_lang_id(tgt)
        except Exception:
            logger.warning("[Trans] Lang '%s' unsupported, using English", tgt)
            return english_text, english_text

        ctx = (
            torch.cuda.amp.autocast(dtype=torch.float16)
            if device == "cuda"
            else _null_ctx()
        )
        with torch.no_grad(), ctx:
            gen = model.generate(
                **enc,
                forced_bos_token_id=lang_id,
                num_beams=beams,
                max_length=256,
            )
        native    = tokenizer.batch_decode(gen, skip_special_tokens=True)[0].strip()
        romanized = _romanize(native, target_lang)
        logger.info("[Trans] en→%s: %s", tgt, native[:100])
        return native, romanized

    return await loop.run_in_executor(_executor, _run)


async def translate_to_english(text: str, src_lang: str) -> str:
    if not text.strip() or src_lang == "en":
        return text

    src              = _resolve_lang(src_lang)
    model, tokenizer = await get_model()
    device           = _get_device()
    beams            = _num_beams(device)
    loop             = asyncio.get_event_loop()

    def _run():
        try:
            tokenizer.get_lang_id(src)
        except Exception:
            logger.warning("[Trans] Source lang '%s' unsupported, using original", src)
            return text

        tokenizer.tgt_lang = "en"
        enc = tokenizer(
            text, return_tensors="pt", truncation=True, max_length=512,
        ).to(model.device)
        try:
            en_id = tokenizer.get_lang_id("en")
        except Exception:
            return text

        ctx = (
            torch.cuda.amp.autocast(dtype=torch.float16)
            if device == "cuda"
            else _null_ctx()
        )
        with torch.no_grad(), ctx:
            gen = model.generate(
                **enc,
                forced_bos_token_id=en_id,
                num_beams=beams,
                max_length=256,
            )
        english = tokenizer.batch_decode(gen, skip_special_tokens=True)[0].strip()
        logger.info("[Trans] %s→en: %s", src, english[:120])
        return english or text

    return await loop.run_in_executor(_executor, _run)


class _null_ctx:
    def __enter__(self): return self
    def __exit__(self, *_): pass
