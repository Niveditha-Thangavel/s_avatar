"""
tts_engine.py — OmniVoice TTS  (GPU-optimised)

GPU path (CUDA):
  • float16 weights + autocast for inference
  • torch.compile (reduce_overhead) on first warm-up call
  • Dedicated single-thread executor — prevents GIL contention with other models
  • CUDA stream isolation per synthesis call

MPS path (Apple Silicon): float16, no compile
CPU path: float32, no compile
"""

import asyncio
import concurrent.futures
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

logger = logging.getLogger(__name__)

SAMPLE_RATE = 24_000
MODEL_ID    = "k2-fsa/OmniVoice"

_HERE             = Path(__file__).parent
DEFAULT_REF_AUDIO = _HERE / "ref_audio.wav"
DEFAULT_REF_TEXT  = _HERE / "ref_text.txt"

_model      = None
_model_lock = asyncio.Lock()

# Single-thread executor for TTS — serialises GPU work, avoids CUDA ctx races
_executor   = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="tts")

# Device cached at load time — used by _run_generate for autocast
_device_cache: Optional[str] = None


def _best_device() -> str:
    forced = os.environ.get("OMNIVOICE_DEVICE", "").lower()
    if forced:
        return forced
    if torch.cuda.is_available():
        return "cuda:0"
    if torch.backends.mps.is_available():
        return "mps"
    return "cpu"


def _best_dtype(device: str) -> torch.dtype:
    if device.startswith("cuda") or device == "mps":
        return torch.float16
    return torch.float32


async def get_model():
    global _model, _device_cache
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

        m = OmniVoice.from_pretrained(MODEL_ID, device_map=device, dtype=dtype)

        # CUDA-only: compile + cudnn tuning
        if device.startswith("cuda"):
            torch.backends.cudnn.benchmark = True
            logger.info("[TTS] torch.compile (reduce_overhead) …")
            try:
                m = torch.compile(m, mode="reduce-overhead", fullgraph=False)
                logger.info("[TTS] torch.compile done")
            except Exception as e:
                logger.warning("[TTS] torch.compile skipped: %s", e)

        _model = m
        _device_cache = device
        elapsed = time.perf_counter() - t0
        logger.info("[TTS] ✅ OmniVoice ready in %.2fs on %s", elapsed, device)
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

    resolved_ref      = str(DEFAULT_REF_AUDIO) if DEFAULT_REF_AUDIO.exists() else None
    resolved_ref_text = _load_ref_text() if resolved_ref else None

    sentences = _split_sentences(text)
    loop      = asyncio.get_event_loop()

    for sentence in sentences:
        sentence = sentence.strip()
        if not sentence:
            continue

        audio_np = await loop.run_in_executor(
            _executor,
            _run_generate,
            model, sentence,
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
    """Runs in the dedicated TTS thread. Uses autocast on CUDA for speed."""
    is_cuda = _device_cache is not None and _device_cache.startswith("cuda")

    strategies = []
    if ref_audio:
        strategies.append(("ref_audio", dict(
            text=text, num_step=num_step, speed=speed,
            ref_audio=ref_audio,
            **({"ref_text": ref_text} if ref_text else {}),
        )))
    if instruct:
        strategies.append(("instruct", dict(
            text=text, num_step=num_step, speed=speed, instruct=instruct,
        )))
    strategies.append(("default", dict(text=text, num_step=num_step, speed=speed)))

    ctx = torch.cuda.amp.autocast(dtype=torch.float16) if is_cuda else _null_ctx()

    for label, kwargs in strategies:
        try:
            with ctx:
                audio_list = model.generate(**kwargs)
            if audio_list:
                return np.concatenate([np.asarray(a, dtype=np.float32) for a in audio_list])
        except Exception as exc:
            logger.warning("[TTS] %s strategy failed for %r: %s", label, text, exc)

    logger.error("[TTS] All strategies failed for: %r", text)
    return None


class _null_ctx:
    """No-op context manager for non-CUDA devices."""
    def __enter__(self): return self
    def __exit__(self, *_): pass


def _split_sentences(text: str) -> list:
    parts = re.split(r"(?<=[.!?])\s+", text.strip())
    if len(parts) <= 1:
        words = text.split()
        parts = [" ".join(words[i:i+60]) for i in range(0, len(words), 60)]
    parts = [p for p in parts if p.strip()]
    merged = []
    for p in parts:
        if merged and len(merged[-1]) < 10:
            merged[-1] += " " + p
        else:
            merged.append(p)
    return merged or parts
