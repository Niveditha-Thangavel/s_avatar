"""
translation_engine.py — SMaLL-100 (alirezamsh/small100)
────────────────────────────────────────────────────────────────────────────
Step 3 of the pipeline (output side):
  English response from Granite → SMaLL-100 → user's original language

This is a 300M distilled M2M-100 model covering 100+ languages.
Uses a custom tokenizer (SMALL100Tokenizer) shipped in the model repo.
MIT license — no HF token needed.

romanized_text:
  Uses `uroman` (universal romanizer) to convert any non-Latin script to
  Latin characters for lipsync visemes.  Fully offline — no API calls.
  Falls back to `anyascii` if uroman is unavailable.

  For Latin-script languages (fr, es, de, etc.) the native text is already
  in Latin characters and used directly.
"""

import asyncio
import importlib.util
import logging
import os
import re
from typing import Optional, Tuple

import torch
from anyascii import anyascii

logger = logging.getLogger(__name__)

MODEL_ID   = "alirezamsh/small100"
_model     = None
_tokenizer = None
_lock      = asyncio.Lock()


def _load_tokenizer_class():
    """Download and dynamically import SMALL100Tokenizer from the model repo.
    Also patches the file for transformers 5.x compatibility:
      BatchEncoding moved from tokenization_utils → tokenization_utils_base.
    """
    from huggingface_hub import hf_hub_download
    cache = os.environ.get("TRANSFORMERS_CACHE", "/app/.cache/huggingface")
    path  = hf_hub_download(
        repo_id=MODEL_ID,
        filename="tokenization_small100.py",
        cache_dir=cache,
    )

    # ── Patch for transformers 5.x ────────────────────────────────────────────
    # In transformers 5.x, BatchEncoding was moved out of tokenization_utils.
    # We patch the downloaded file in-place so it imports from the right place.
    with open(path, "r", encoding="utf-8") as f:
        src = f.read()

    old = "from transformers.tokenization_utils import BatchEncoding, PreTrainedTokenizer"
    new = ("from transformers.tokenization_utils_base import BatchEncoding\n"
           "from transformers.tokenization_utils import PreTrainedTokenizer")

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
        device = "cuda" if torch.cuda.is_available() else "cpu"
        dtype  = torch.float16 if device == "cuda" else torch.float32
        logger.info("[Trans] Loading SMaLL-100 on %s (%s) …", device, dtype)
        cache = os.environ.get("TRANSFORMERS_CACHE", "/app/.cache/huggingface")
        SMALL100Tokenizer = _load_tokenizer_class()
        _tokenizer = SMALL100Tokenizer.from_pretrained(MODEL_ID, cache_dir=cache)
        _model     = M2M100ForConditionalGeneration.from_pretrained(
            MODEL_ID, torch_dtype=dtype, cache_dir=cache
        ).to(device)
        _model.eval()
        logger.info("[Trans] ✅ SMaLL-100 ready on %s", device)
        return _model, _tokenizer


async def load_translation_model():
    """Warm-up: preload at startup."""
    await get_model()


# Map Whisper's ISO 639-1 codes to SMaLL-100 lang tokens where they differ
_LANG_MAP = {
    "zh":  "zh",
    "pt":  "pt",
    "jw":  "jv",   # Javanese
    "sr":  "sr",
}

def _resolve_lang(lang_code: str) -> str:
    return _LANG_MAP.get(lang_code, lang_code)


# ── Romanization for lipsync ──────────────────────────────────────────────────
# Uses `uroman` (universal romanizer) — fully offline, context-aware transliteration
# for all non-Latin scripts.  Falls back to `anyascii` if uroman fails.

_LATIN_SCRIPT_LANGS = {
    "en", "fr", "es", "de", "pt", "it", "nl", "sv", "da", "no", "fi",
    "ro", "pl", "cs", "hu", "vi", "id", "ms", "tl", "sw", "af", "sq",
    "bs", "ca", "hr", "cs", "et", "lv", "lt", "mk", "sk", "sl",
}

# ISO 639-1 → ISO 639-3 mapping for uroman
_ISO_639_1_TO_3 = {
    "hi": "hin", "mr": "mar", "ne": "nep", "sa": "san",
    "ta": "tam", "te": "tel", "kn": "kan", "ml": "mal",
    "bn": "ben", "gu": "guj", "pa": "pan", "or": "ori",
    "si": "sin",
    "ar": "ara", "fa": "fas", "ur": "urd", "he": "heb",
    "ja": "jpn", "zh": "cmn", "yue": "yue",
    "ko": "kor",
    "th": "tha", "lo": "lao", "my": "mya", "km": "khm",
    "ru": "rus", "uk": "ukr", "be": "bel", "bg": "bul",
    "sr": "srp", "hr": "hrv", "sl": "slv",
    "el": "ell",
    "am": "amh", "ti": "tir",
    "ka": "kat", "hy": "hye",
    "dv": "div", "ps": "pus", "ku": "kur",
    "bo": "bod", "dz": "dzo",
}

# Lazy-loaded uroman instance
_uroman = None

def _get_uroman():
    global _uroman
    if _uroman is None:
        import uroman as ur
        _uroman = ur.Uroman()
    return _uroman


def _romanize_text(text: str, lang: str) -> str:
    """Convert native-script text to Latin approximation for lipsync visemes.

    Uses `uroman` (universal romanizer) for all non-Latin scripts:
    context-aware, n-to-m character mapping, fully offline.
    Falls back to `anyascii` if uroman encounters an error.

    Latin-script languages (en, fr, de, etc.) are returned as-is.
    """
    if not text.strip():
        return text
    if lang in _LATIN_SCRIPT_LANGS:
        return text

    try:
        u = _get_uroman()
        lcode = _ISO_639_1_TO_3.get(lang)
        result = u.romanize_string(text, lcode=lcode)
        # anyascii cleanup: strip any stray non-ASCII characters
        # (uroman output is pure ASCII for all tested scripts, but
        #  this guards against edge cases)
        return anyascii(result)
    except Exception as exc:
        logger.warning("[Trans] uroman failed for %s: %s", lang, exc)
        return anyascii(text)


async def translate_from_english(english_text: str, target_lang: str) -> Tuple[str, str]:
    """
    Translates English text → target_lang using SMaLL-100.

    Parameters
    ----------
    english_text  : response from Granite LLM (English)
    target_lang   : ISO 639-1 code detected by Whisper (e.g. "hi", "ta", "fr")

    Returns
    -------
    (native_text, romanized_text)
    native_text    — response in user's language (sent to OmniVoice TTS)
    romanized_text — Latin-script romanization of native_text for the frontend
                     lipsync viseme mapper.  Uses uroman (universal romanizer)
                     for all non-Latin scripts (e.g. "नमस्ते" → "namaste",
                     "你好" → "nihao"). Falls back to anyascii on error.
    """
    # If user spoke English, no translation needed
    if not english_text.strip():
        return "", ""
    if target_lang == "en":
        return english_text, english_text

    tgt = _resolve_lang(target_lang)

    model, tokenizer = await get_model()
    loop = asyncio.get_event_loop()

    def _run():
        tokenizer.tgt_lang = tgt
        enc = tokenizer(
            english_text,
            return_tensors="pt",
            truncation=True,
            max_length=512,
        ).to(model.device)

        try:
            lang_id = tokenizer.get_lang_id(tgt)
        except Exception:
            # Unsupported language — return English as fallback
            logger.warning("[Trans] Lang '%s' not supported by SMaLL-100, using English", tgt)
            return english_text, english_text

        with torch.no_grad():
            gen = model.generate(
                **enc,
                forced_bos_token_id=lang_id,
                num_beams=5,
                max_length=256,
            )

        native = tokenizer.batch_decode(gen, skip_special_tokens=True)[0].strip()
        logger.info("[Trans] en → %s: %s", tgt, native[:100])
        # Romanize the translated text so the lipsync viseme mapper gets Latin
        # characters that phonetically match the audio OmniVoice will speak.
        romanized = _romanize_text(native, target_lang)
        logger.info("[Trans] romanized: %s", romanized[:100])
        return native, romanized

    return await loop.run_in_executor(None, _run)


async def translate_to_english(text: str, src_lang: str) -> str:
    """
    Translates text from src_lang → English using SMaLL-100.
    Used on the INPUT side: user speech transcript → English for Granite LLM.

    Parameters
    ----------
    text     : original-language transcript from Whisper
    src_lang : ISO 639-1 code detected by Whisper

    Returns
    -------
    str — English translation (or original text if already English / unsupported)
    """
    if not text.strip():
        return ""
    if src_lang == "en":
        return text

    src = _resolve_lang(src_lang)
    model, tokenizer = await get_model()
    loop = asyncio.get_event_loop()

    def _run():
        # Check source lang is supported
        try:
            src_id = tokenizer.get_lang_id(src)
        except Exception:
            logger.warning("[Trans] Source lang '%s' not in SMaLL-100, using original text", src)
            return text

        # Set target to English
        tokenizer.tgt_lang = "en"
        enc = tokenizer(
            text,
            return_tensors="pt",
            truncation=True,
            max_length=512,
        ).to(model.device)

        try:
            en_id = tokenizer.get_lang_id("en")
        except Exception:
            return text

        with torch.no_grad():
            gen = model.generate(
                **enc,
                forced_bos_token_id=en_id,
                num_beams=5,
                max_length=256,
            )

        english = tokenizer.batch_decode(gen, skip_special_tokens=True)[0].strip()
        logger.info("[Trans] %s → en: %s", src, english[:120])
        return english if english.strip() else text

    return await loop.run_in_executor(None, _run)
