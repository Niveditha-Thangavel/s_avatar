"""
stt_engine.py
────────────────────────────────────────────────────────────────────────────
Whisper Large-v3-Turbo — STT only (task="transcribe").
Translation to English is handled by SMaLL-100 in translation_engine.py.

Why not Whisper's built-in task="translate"?
  - Unreliable for short phrases — returns empty or wrong-language output
  - SMaLL-100 is a dedicated translation model and far more accurate
  - Keeps responsibilities clean: Whisper does STT, SMaLL-100 does translation

Returns:
  (original_transcript, detected_lang)
"""

import asyncio
import logging
import os
from typing import Tuple

import numpy as np

logger      = logging.getLogger(__name__)
SAMPLE_RATE = 16_000

_model      = None
_model_lock = asyncio.Lock()


async def get_model():
    global _model
    if _model is not None:
        return _model
    async with _model_lock:
        if _model is not None:
            return _model
        from faster_whisper import WhisperModel
        import torch
        device       = "cuda" if torch.cuda.is_available() else "cpu"
        compute_type = "float16" if device == "cuda" else "int8"
        logger.info("[STT] Loading Whisper large-v3-turbo on %s (%s) …", device, compute_type)
        _model = WhisperModel(
            "large-v3-turbo",
            device=device,
            compute_type=compute_type,
            download_root=os.environ.get("WHISPER_CACHE", "/root/.cache/whisper"),
        )
        logger.info("[STT] ✅ Whisper ready")
        return _model


async def load_stt_models():
    """Warm-up entry point."""
    await get_model()


async def transcribe(audio_np: np.ndarray) -> Tuple[str, str]:
    """
    Transcribe audio in its original language.

    Returns
    -------
    (original_transcript, detected_lang)
      original_transcript — what the user said (native script, shown in UI)
      detected_lang       — ISO 639-1 code, e.g. "en", "hi", "ta", "ja"
    """
    model = await get_model()
    loop  = asyncio.get_event_loop()

    def _run():
        segs, info = model.transcribe(
            audio_np,
            task="transcribe",      # STT only — no translation
            language=None,          # auto-detect language
            beam_size=5,
            vad_filter=True,
            vad_parameters=dict(min_silence_duration_ms=300, speech_pad_ms=200),
        )
        text = " ".join(s.text.strip() for s in segs).strip()
        lang = info.language
        logger.info("[STT] Detected: %s | Transcript: %s", lang, text[:120])
        return text, lang

    return await loop.run_in_executor(None, _run)
