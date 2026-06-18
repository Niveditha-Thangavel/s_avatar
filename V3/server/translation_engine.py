"""
translation_engine.py — SMaLL-100  (GPU-optimised)

Supports all 100 SMaLL-100 languages — same list as the FLORES-101 benchmark:
  af am ar ast az ba be bg bn br bs ca ceb cs cy da de el en es et fa ff fi
  fr fy ga gd gl gu ha he hi hr ht hu hy id ig ilo is it ja jv ka kk km kn
  ko lb lg ln lo lt lv mg mk ml mn mr ms my ne nl no ns oc or pa pl ps pt ro
  ru sd si sk sl so sq sr ss su sv sw ta th tl tn tr uk ur uz vi wo xh yi yo
  zh zu

Whisper → SMaLL-100 code mapping:
  Whisper uses ISO 639-1 codes. SMaLL-100 uses the same codes with a few
  exceptions listed in _WHISPER_TO_SMALL100. All others pass through unchanged.

GPU path (CUDA):
  float16, torch.compile(reduce-overhead), autocast, num_beams=2
MPS / CPU:
  float32, num_beams=4, no compile
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

        logger.info("[Trans] ✅ SMaLL-100 ready on %s (%d languages)", device,
                    len(_tokenizer.lang_code_to_token))
        return _model, _tokenizer


async def load_translation_model():
    await get_model()


# ── Language code mapping ─────────────────────────────────────────────────────
# Whisper detects languages using ISO 639-1 codes.
# SMaLL-100 uses the same codes for all 100 languages it supports EXCEPT
# Javanese: Whisper returns "jw", SMaLL-100 expects "jv".
# All other codes pass through unchanged.

_WHISPER_TO_SMALL100 = {
    "jw": "jv",   # Javanese: Whisper code → SMaLL-100 code
}

# Full set of 100 SMaLL-100 language codes (verified from tokenizer.lang_code_to_token)
# Edge cases:
#   kn (Kannada):  tokenizer accepts it but en→kn generation produces English output
#                  (SMaLL-100 training gap) — pipeline serves English to Kannada users
#   wo (Wolof):    en→wo produces repetitive output — rare language, known model weakness
#   mn (Mongolian): uses Cyrillic script (0x0400-0x04FF), not traditional Mongolian script
_SMALL100_LANGS = frozenset({
    "af","am","ar","ast","az","ba","be","bg","bn","br","bs","ca","ceb","cs",
    "cy","da","de","el","en","es","et","fa","ff","fi","fr","fy","ga","gd","gl",
    "gu","ha","he","hi","hr","ht","hu","hy","id","ig","ilo","is","it","ja","jv",
    "ka","kk","km","kn","ko","lb","lg","ln","lo","lt","lv","mg","mk","ml","mn",
    "mr","ms","my","ne","nl","no","ns","oc","or","pa","pl","ps","pt","ro","ru",
    "sd","si","sk","sl","so","sq","sr","ss","su","sv","sw","ta","th","tl","tn",
    "tr","uk","ur","uz","vi","wo","xh","yi","yo","zh","zu",
})


def _resolve(whisper_code: str) -> str | None:
    """
    Map a Whisper language code to the SMaLL-100 code.
    Returns None if the language is not supported by SMaLL-100.
    """
    code = _WHISPER_TO_SMALL100.get(whisper_code, whisper_code)
    return code if code in _SMALL100_LANGS else None


def _num_beams(device: str) -> int:
    return 2 if device == "cuda" else 4


# ── Romanisation ──────────────────────────────────────────────────────────────
_LATIN_LANGS = {
    "af","ast","az","bs","ca","cs","cy","da","de","en","es","et","fi","fr","fy",
    "ga","gd","gl","hr","ht","hu","id","ilo","is","it","lb","lt","lv","mg","mk",
    "ms","nl","no","ns","oc","pl","pt","ro","sk","sl","so","sq","ss","su","sv",
    "sw","tl","tn","tr","uz","vi","wo","xh","zu",
}

_ISO1_TO_3 = {
    "hi":"hin","mr":"mar","ne":"nep","ta":"tam","te":"tel","kn":"kan","ml":"mal",
    "bn":"ben","gu":"guj","pa":"pan","or":"ori","si":"sin","ur":"urd","sd":"snd",
    "ar":"ara","fa":"fas","he":"heb","ja":"jpn","zh":"cmn","ko":"kor","th":"tha",
    "lo":"lao","my":"mya","km":"khm","ru":"rus","uk":"ukr","be":"bel","bg":"bul",
    "sr":"srp","el":"ell","am":"amh","ka":"kat","hy":"hye","ps":"pus","mn":"mon",
    "kk":"kaz","az":"aze","ba":"bak","tt":"tat","uz":"uzb","yi":"yid","yo":"yor",
    "ha":"hau","ig":"ibo","ln":"lin","lg":"lug","ff":"ful","wo":"wol",
}

_uroman = None


def _get_uroman():
    global _uroman
    if _uroman is None:
        import uroman as ur
        _uroman = ur.Uroman()
    return _uroman


def _romanize(text: str, lang: str) -> str:
    """Convert native-script text to Latin for lipsync. Latin-script langs returned as-is."""
    if not text.strip() or lang in _LATIN_LANGS:
        return text
    try:
        return anyascii(_get_uroman().romanize_string(text, lcode=_ISO1_TO_3.get(lang)))
    except Exception as exc:
        logger.warning("[Trans] uroman failed (%s): %s", lang, exc)
        return anyascii(text)


# ── Public API ────────────────────────────────────────────────────────────────

async def translate_from_english(english_text: str, target_lang: str) -> Tuple[str, str]:
    """
    Translate English LLM response → target_lang native script.

    Returns (native_text, romanized_text).
    Falls back to (english_text, english_text) for unsupported languages.
    """
    if not english_text.strip():
        return "", ""
    if target_lang == "en":
        return english_text, english_text

    tgt = _resolve(target_lang)
    if tgt is None:
        logger.warning("[Trans] '%s' not in SMaLL-100 — returning English", target_lang)
        return english_text, english_text

    model, tokenizer = await get_model()
    device = _get_device()
    beams  = _num_beams(device)
    loop   = asyncio.get_event_loop()

    def _run():
        try:
            tokenizer.tgt_lang = tgt
        except KeyError:
            logger.warning("[Trans] tokenizer.tgt_lang='%s' failed — returning English", tgt)
            return english_text, english_text

        enc = tokenizer(
            english_text, return_tensors="pt", truncation=True, max_length=512,
        ).to(model.device)

        try:
            lang_id = tokenizer.get_lang_id(tgt)
        except Exception:
            logger.warning("[Trans] get_lang_id('%s') failed — returning English", tgt)
            return english_text, english_text

        ctx = (torch.cuda.amp.autocast(dtype=torch.float16)
               if device == "cuda" else _null_ctx())
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
    """
    Translate src_lang native text → English for the LLM.

    Falls back to original text for unsupported languages
    (LLM handles many scripts directly).
    """
    if not text.strip() or src_lang == "en":
        return text

    src = _resolve(src_lang)
    if src is None:
        logger.warning("[Trans] src '%s' not in SMaLL-100 — passing text as-is to LLM", src_lang)
        return text

    model, tokenizer = await get_model()
    device = _get_device()
    beams  = _num_beams(device)
    loop   = asyncio.get_event_loop()

    def _run():
        try:
            tokenizer.tgt_lang = "en"
        except KeyError:
            return text

        enc = tokenizer(
            text, return_tensors="pt", truncation=True, max_length=512,
        ).to(model.device)

        try:
            en_id = tokenizer.get_lang_id("en")
        except Exception:
            return text

        ctx = (torch.cuda.amp.autocast(dtype=torch.float16)
               if device == "cuda" else _null_ctx())
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
