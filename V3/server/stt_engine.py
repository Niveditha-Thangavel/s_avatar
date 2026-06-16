"""
stt_engine.py — Whisper Large-v3 Turbo STT  (GPU-optimised)

Model: openai/whisper-large-v3-turbo
  • 809M params, 4× faster than large-v3, near-identical WER
  • Supports 99 languages + automatic language detection
  • Returns (transcript, detected_lang, detected_emotion)
    - emotion is always "neutral" (Whisper doesn't do emotion; handled by LLM)

GPU path (CUDA):
  • float16 weights
  • torch.compile (reduce-overhead) on first warm-up
  • Dedicated single-thread executor
  • batch_size=16 for short clips

MPS / CPU: float32, no compile
"""

import asyncio
import concurrent.futures
import logging
import os
from typing import Tuple

import numpy as np
import torch

logger      = logging.getLogger(__name__)
SAMPLE_RATE = 16_000   # Whisper always expects 16kHz

WHISPER_MODEL_ID = os.environ.get(
    "WHISPER_MODEL_ID", "openai/whisper-large-v3-turbo"
)
WHISPER_CACHE = os.environ.get(
    "TRANSFORMERS_CACHE",
    os.path.join(os.path.expanduser("~"), ".cache", "huggingface"),
)

_pipe       = None   # transformers pipeline
_model_lock = asyncio.Lock()

# Dedicated single-thread executor — keeps Whisper inference off shared pool
_executor = concurrent.futures.ThreadPoolExecutor(
    max_workers=1, thread_name_prefix="stt"
)


def _get_device() -> str:
    if torch.cuda.is_available():
        return "cuda:0"
    if torch.backends.mps.is_available():
        return "mps"
    return "cpu"


def _get_dtype(device: str) -> torch.dtype:
    if device.startswith("cuda"):
        return torch.float16
    return torch.float32


async def get_model():
    """Load and warm-up the Whisper pipeline."""
    global _pipe
    if _pipe is not None:
        return _pipe
    async with _model_lock:
        if _pipe is not None:
            return _pipe

        from transformers import pipeline, AutoModelForSpeechSeq2Seq, AutoProcessor

        device = _get_device()
        dtype  = _get_dtype(device)

        logger.info("[STT] Loading %s on %s (%s) …", WHISPER_MODEL_ID, device, dtype)

        # Load model separately so we can compile it before wrapping in pipeline
        model = AutoModelForSpeechSeq2Seq.from_pretrained(
            WHISPER_MODEL_ID,
            torch_dtype=dtype,
            low_cpu_mem_usage=True,
            use_safetensors=True,
            cache_dir=WHISPER_CACHE,
        )
        model.to(device)
        model.eval()

        # CUDA: compile + cudnn tuning
        if device.startswith("cuda"):
            torch.backends.cudnn.benchmark = True
            logger.info("[STT] torch.compile (reduce_overhead) …")
            try:
                model = torch.compile(model, mode="reduce-overhead", fullgraph=False)
                logger.info("[STT] torch.compile done")
            except Exception as exc:
                logger.warning("[STT] torch.compile skipped: %s", exc)

        processor = AutoProcessor.from_pretrained(
            WHISPER_MODEL_ID, cache_dir=WHISPER_CACHE
        )

        # Build pipeline with chunked long-form decoding
        _pipe = pipeline(
            "automatic-speech-recognition",
            model=model,
            tokenizer=processor.tokenizer,
            feature_extractor=processor.feature_extractor,
            torch_dtype=dtype,
            device=device,
            # Chunked long-form: 30s chunks with 5s stride
            chunk_length_s=30,
            stride_length_s=5,
        )
        logger.info("[STT] ✅ Whisper large-v3-turbo ready on %s", device)
        return _pipe


async def load_stt_models():
    """Warm-up entry point called at server startup."""
    await get_model()


# ── Language code normalisation ───────────────────────────────────────────────
# Whisper returns full language names or BCP-47 codes; normalise to ISO 639-1.
_WHISPER_LANG_MAP = {
    "english": "en", "chinese": "zh", "german": "de", "spanish": "es",
    "russian": "ru", "korean": "ko", "french": "fr", "japanese": "ja",
    "portuguese": "pt", "turkish": "tr", "polish": "pl", "catalan": "ca",
    "dutch": "nl", "arabic": "ar", "swedish": "sv", "italian": "it",
    "indonesian": "id", "hindi": "hi", "finnish": "fi", "vietnamese": "vi",
    "hebrew": "he", "ukrainian": "uk", "greek": "el", "malay": "ms",
    "czech": "cs", "romanian": "ro", "danish": "da", "hungarian": "hu",
    "tamil": "ta", "norwegian": "no", "thai": "th", "urdu": "ur",
    "croatian": "hr", "bulgarian": "bg", "lithuanian": "lt", "latin": "la",
    "maori": "mi", "malayalam": "ml", "welsh": "cy", "slovak": "sk",
    "telugu": "te", "persian": "fa", "latvian": "lv", "bengali": "bn",
    "serbian": "sr", "azerbaijani": "az", "slovenian": "sl", "kannada": "kn",
    "estonian": "et", "macedonian": "mk", "breton": "br", "basque": "eu",
    "icelandic": "is", "armenian": "hy", "nepali": "ne", "mongolian": "mn",
    "bosnian": "bs", "kazakh": "kk", "albanian": "sq", "swahili": "sw",
    "galician": "gl", "marathi": "mr", "punjabi": "pa", "sinhala": "si",
    "khmer": "km", "shona": "sn", "yoruba": "yo", "somali": "so",
    "afrikaans": "af", "occitan": "oc", "georgian": "ka", "belarusian": "be",
    "tajik": "tg", "sindhi": "sd", "gujarati": "gu", "amharic": "am",
    "yiddish": "yi", "lao": "lo", "uzbek": "uz", "faroese": "fo",
    "haitian creole": "ht", "pashto": "ps", "turkmen": "tk",
    "nynorsk": "nn", "maltese": "mt", "sanskrit": "sa", "luxembourgish": "lb",
    "myanmar": "my", "tibetan": "bo", "tagalog": "tl", "malagasy": "mg",
    "assamese": "as", "tatar": "tt", "hawaiian": "haw", "lingala": "ln",
    "hausa": "ha", "bashkir": "ba", "javanese": "jv", "sundanese": "su",
    "burmese": "my",
}


def _normalise_lang(lang: str | None) -> str:
    """Convert Whisper language output to ISO 639-1 code."""
    if not lang:
        return "en"
    lang = lang.lower().strip()
    # Already a short code
    if len(lang) <= 3 and lang.isalpha():
        return lang
    return _WHISPER_LANG_MAP.get(lang, "en")


async def transcribe(audio_np: np.ndarray) -> Tuple[str, str, str]:
    """
    Transcribe audio_np (float32, 16kHz mono) with Whisper large-v3-turbo.

    Returns
    -------
    (transcript, detected_lang, emotion)
      transcript    — clean text, no special tokens
      detected_lang — ISO 639-1 code (e.g. "en", "hi", "ta")
      emotion       — always "neutral" (Whisper doesn't detect emotion)
    """
    pipe = await get_model()
    loop = asyncio.get_event_loop()

    def _run():
        device = _get_device()
        ctx = (
            torch.cuda.amp.autocast(dtype=torch.float16)
            if device.startswith("cuda")
            else _null_ctx()
        )
        with ctx:
            result = pipe(
                audio_np,
                return_timestamps=False,
                generate_kwargs={
                    "language": None,          # auto-detect
                    "task": "transcribe",
                    "temperature": 0.0,        # greedy — fastest + most stable
                },
            )
        text = result.get("text", "").strip()
        lang = _normalise_lang(result.get("chunks", [{}])[0].get("language") if result.get("chunks") else None)

        # Whisper pipeline puts detected language in the chunks or in top-level
        # depending on version — also check the raw chunks key
        if lang == "en" and hasattr(result, "get"):
            raw_lang = result.get("language") or result.get("detected_language")
            if raw_lang:
                lang = _normalise_lang(raw_lang)

        logger.info("[STT] lang=%s text=%s", lang, text[:80])
        return text, lang, "neutral"

    return await loop.run_in_executor(_executor, _run)


class _null_ctx:
    def __enter__(self): return self
    def __exit__(self, *_): pass
