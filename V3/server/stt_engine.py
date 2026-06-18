"""
stt_engine.py — Faster-Whisper large-v3 STT  (CTranslate2 backend)

Model: Systran/faster-whisper-large-v3
  • CTranslate2 backend — ~4× faster than vanilla HuggingFace transformers
  • 99-language auto-detection via model.transcribe(language=None)
  • int8 compute for CPU/Mac, float16 for CUDA
  • Returns (transcript, detected_lang_iso639, "neutral")

Device strategy
────────────────
  CUDA: float16 compute
  MPS:  not supported by CTranslate2 → falls back to CPU + int8
  CPU:  int8 compute (highly optimised on Apple Silicon)
"""

import asyncio
import logging
import os
from typing import Tuple

import numpy as np

logger      = logging.getLogger(__name__)
SAMPLE_RATE = 16_000   # faster-whisper expects 16 kHz mono float32

WHISPER_MODEL_ID = os.environ.get(
    "WHISPER_MODEL_ID", "Systran/faster-whisper-large-v3"
)
# faster-whisper caches converted CTranslate2 models to ~/.cache/faster_whisper/
WHISPER_CACHE = os.environ.get(
    "FASTER_WHISPER_CACHE",
    os.path.join(os.path.expanduser("~"), ".cache", "faster_whisper"),
)

_model      = None
_model_lock = asyncio.Lock()


def _get_device() -> str:
    import torch
    if torch.cuda.is_available():
        return "cuda"
    return "cpu"


def _get_compute_type(device: str) -> str:
    if device == "cuda":
        return "float16"
    return "int8"


async def get_model():
    global _model
    if _model is not None:
        return _model

    async with _model_lock:
        if _model is not None:
            return _model

        from faster_whisper import WhisperModel

        device       = _get_device()
        compute_type = _get_compute_type(device)
        logger.info("[STT] Loading %s on %s (%s) …", WHISPER_MODEL_ID, device, compute_type)

        m = WhisperModel(
            WHISPER_MODEL_ID,
            device=device,
            compute_type=compute_type,
            download_root=WHISPER_CACHE,
            cpu_threads=4,
            num_workers=1,
        )

        _model = m
        logger.info("[STT] Faster-Whisper ready on %s (%s)", device, compute_type)
        return _model


async def load_stt_models():
    """Warm-up entry point called at server startup."""
    await get_model()


async def transcribe(audio_np: np.ndarray) -> Tuple[str, str, str]:
    """
    Transcribe audio_np (float32 mono, 16 kHz) with Faster-Whisper.

    Returns
    -------
    (transcript, lang_iso639, "neutral")
    """
    model = await get_model()

    def _run():
        segments, info = model.transcribe(
            audio_np,
            language=None,        # auto-detect
            beam_size=5,
            vad_filter=False,
            condition_on_previous_text=True,
        )

        lang = info.language
        if not lang or len(lang) > 5:
            lang = "en"

        text = " ".join(segment.text.strip() for segment in segments)

        logger.info(
            "[STT] lang=%s (prob=%.3f) text=%s",
            lang, info.language_probability, text[:80],
        )
        return text, lang, "neutral"

    import concurrent.futures
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="stt"),
        _run,
    )
