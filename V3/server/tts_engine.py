"""
tts_engine.py — OmniVoice TTS
Streams Float32-LE PCM audio sentence by sentence.
"""

import asyncio
import logging
import os
import re
import time
import warnings
from pathlib import Path
from typing import AsyncIterator, Optional

import numpy as np
import torch

warnings.filterwarnings("ignore", message=".*clean_up_tokenization.*")
warnings.filterwarnings("ignore", message=".*forced_decoder_ids.*")
warnings.filterwarnings("ignore", message=".*SuppressTokens.*")
warnings.filterwarnings("ignore", message=".*multilingual Whisper.*")

logger = logging.getLogger(__name__)

SAMPLE_RATE = 24_000
MODEL_ID    = "k2-fsa/OmniVoice"

_HERE              = Path(__file__).parent
DEFAULT_REF_AUDIO  = _HERE / "ref_audio.wav"
DEFAULT_REF_TEXT   = _HERE / "ref_text.txt"

_model      = None
_model_lock = asyncio.Lock()


def _best_device() -> str:
    forced = os.environ.get("OMNIVOICE_DEVICE", "").lower()
    if forced:
        return forced
    if torch.cuda.is_available():
        return "cuda:0"
    return "cpu"


def _best_dtype(device: str) -> torch.dtype:
    return torch.float16 if device.startswith("cuda") else torch.float32


async def get_model():
    global _model
    if _model is not None:
        return _model
    async with _model_lock:
        if _model is not None:
            return _model
        from omnivoice import OmniVoice
        device = _best_device()
        dtype  = _best_dtype(device)
        logger.info("[TTS] Loading OmniVoice on %s (%s) …", device, dtype)
        t0 = time.perf_counter()
        _model = OmniVoice.from_pretrained(MODEL_ID, device_map=device, dtype=dtype)
        logger.info("[TTS] ✅ OmniVoice ready in %.2fs", time.perf_counter() - t0)
        return _model


def _load_ref_text() -> Optional[str]:
    if DEFAULT_REF_TEXT.exists():
        with open(DEFAULT_REF_TEXT, "r", encoding="utf-8") as f:
            return f.read().strip()
    return None


async def synthesize_stream(
    text: str,
    instruct: Optional[str] = None,
    speed: float = 1.0,
    num_step: int = 16,
) -> AsyncIterator[dict]:
    model = await get_model()

    resolved_ref = None
    resolved_ref_text = None
    if DEFAULT_REF_AUDIO.exists():
        resolved_ref = str(DEFAULT_REF_AUDIO)
        resolved_ref_text = _load_ref_text()

    sentences = _split_sentences(text)
    loop = asyncio.get_event_loop()

    for idx, sentence in enumerate(sentences):
        sentence = sentence.strip()
        if not sentence:
            continue

        audio_np = await loop.run_in_executor(
            None, _run_generate, model, sentence,
            resolved_ref, resolved_ref_text,
            instruct, speed, num_step,
        )
        if audio_np is None or len(audio_np) == 0:
            logger.warning("[TTS] Empty audio for: %r", sentence)
            continue

        yield {
            "audio":       audio_np.astype(np.float32).tobytes(),
            "sample_rate": SAMPLE_RATE,
            "text":        sentence,
        }


def _run_generate(model, text, ref_audio, ref_text, instruct, speed, num_step):
    # Try with ref_audio first; fall back to instruct/default on failure
    strategies = []
    if ref_audio:
        strategies.append(("ref_audio", dict(
            text=text, num_step=num_step, speed=speed,
            ref_audio=ref_audio,
            **(dict(ref_text=ref_text) if ref_text else {}),
        )))
    if instruct:
        strategies.append(("instruct", dict(
            text=text, num_step=num_step, speed=speed,
            instruct=instruct,
        )))
    strategies.append(("default", dict(
        text=text, num_step=num_step, speed=speed,
    )))

    for label, kwargs in strategies:
        try:
            audio_list = model.generate(**kwargs)
            if audio_list:
                return np.concatenate([np.asarray(a, dtype=np.float32) for a in audio_list])
        except Exception as exc:
            logger.warning("[TTS] %s strategy failed for %r: %s", label, text, exc)
            continue

    logger.error("[TTS] All generation strategies failed for: %r", text)
    return None


def _split_sentences(text: str) -> list:
    parts = re.split(r"(?<=[.!?])\s+", text.strip())
    if len(parts) <= 1:
        words = text.split()
        parts = [" ".join(words[i:i+60]) for i in range(0, len(words), 60)]
    parts = [p for p in parts if p.strip()]
    merged = []
    for p in parts:
        if merged and len(merged[-1]) < 10:
            merged[-1] = merged[-1] + " " + p
        else:
            merged.append(p)
    return merged or parts
