"""
tts_engine.py
Wraps k2-fsa/OmniVoice with a streaming async interface.

Real API (from https://github.com/k2-fsa/OmniVoice):

    from omnivoice import OmniVoice
    import torch

    model = OmniVoice.from_pretrained(
        "k2-fsa/OmniVoice",
        device_map="cuda:0",   # or "cpu", "mps"
        dtype=torch.float16    # or torch.float32 on CPU/MPS
    )

    # Voice cloning (ref_audio required)
    audio_list = model.generate(
        text="Hello world.",
        ref_audio="ref.wav",       # 3-10 second WAV/MP3/FLAC
        ref_text="transcript...",  # optional – omit to let Whisper auto-transcribe
        num_step=16,               # 16=fast, 32=high quality
        speed=1.0,
    )

    # Voice design (no ref_audio)
    audio_list = model.generate(
        text="Hello world.",
        instruct="female, low pitch, british accent",
    )

    # Auto voice (no ref_audio, no instruct)
    audio_list = model.generate(text="Hello world.")

    # Returns: list of np.ndarray, shape (T,), float32, 24 kHz

One model instance is loaded at startup and shared across all requests.
The ref_audio file is placed at server/ref_audio.wav – users can replace it.
"""

import os
import warnings

# Suppress transformers BPE/WordPiece tokenizer warning from OmniVoice's
# internal Whisper ASR (used to auto-transcribe ref_audio)
warnings.filterwarnings("ignore", message=".*clean_up_tokenization.*")
warnings.filterwarnings("ignore", message=".*forced_decoder_ids.*")
warnings.filterwarnings("ignore", message=".*SuppressTokens.*")
warnings.filterwarnings("ignore", message=".*multilingual Whisper.*")

import asyncio
import logging
import time
from pathlib import Path
from typing import AsyncIterator, Optional

import numpy as np
import torch

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
SAMPLE_RATE = 24_000          # OmniVoice output is always 24 kHz
MODEL_ID    = "k2-fsa/OmniVoice"

# Default reference audio – place a 3-10s WAV next to this file.
# Users replace this file with their own voice sample.
_HERE = Path(__file__).parent
DEFAULT_REF_AUDIO = _HERE / "ref_audio.wav"

# Auto-detect best device
def _best_device() -> str:
    # MPS (Apple Silicon GPU) writes large Metal shader cache files to disk
    # which fails when disk is low, and is less stable than CPU for inference.
    # Force CPU on Mac; enable MPS explicitly by setting OMNIVOICE_DEVICE=mps.
    import os
    forced = os.environ.get("OMNIVOICE_DEVICE", "").lower()
    if forced:
        return forced
    if torch.cuda.is_available():
        return "cuda:0"
    return "cpu"

def _best_dtype(device: str) -> torch.dtype:
    # float16 only works on CUDA; everything else needs float32
    return torch.float16 if device.startswith("cuda") else torch.float32

# ---------------------------------------------------------------------------
# Lazy singleton
# ---------------------------------------------------------------------------
_model      = None
_model_lock = asyncio.Lock()


async def get_model():
    """Load OmniVoice once and return the cached instance."""
    global _model
    if _model is not None:
        return _model

    async with _model_lock:
        if _model is not None:
            return _model

        from omnivoice import OmniVoice  # deferred – heavy import

        device = _best_device()
        dtype  = _best_dtype(device)

        logger.info("[TTS] Loading OmniVoice on %s (%s) ...", device, dtype)
        t0 = time.perf_counter()

        _model = OmniVoice.from_pretrained(
            MODEL_ID,
            device_map=device,
            dtype=dtype,
        )

        logger.info("[TTS] OmniVoice ready in %.2fs", time.perf_counter() - t0)
        return _model


# ---------------------------------------------------------------------------
# Streaming synthesis
# ---------------------------------------------------------------------------

async def synthesize_stream(
    text: str,
    romanized_text: Optional[str] = None,
    ref_audio: Optional[str] = None,
    ref_text:  Optional[str] = None,
    instruct:  Optional[str] = None,
    speed:     float = 1.0,
    num_step:  int   = 16,
) -> AsyncIterator[dict]:
    """
    Async generator – yields one dict per sentence:
    {
        "audio":          bytes,   # raw float32-le PCM, mono, 24 kHz
        "sample_rate":    int,     # 24000
        "text":           str,     # the native sentence fragment
        "romanized_text": str,     # the matching romanized sentence fragment
    }

    Priority:
      1. ref_audio provided  →  voice cloning
      2. instruct provided   →  voice design
      3. neither             →  auto voice
    """
    model = await get_model()

    # Resolve reference audio path
    # If caller didn't supply one, fall back to the default on disk
    resolved_ref = None
    if ref_audio and Path(ref_audio).exists():
        resolved_ref = str(ref_audio)
    elif DEFAULT_REF_AUDIO.exists():
        resolved_ref = str(DEFAULT_REF_AUDIO)
        logger.info("[TTS] Using default ref audio: %s", resolved_ref)

    sentences = _split_sentences(text)
    rom_sentences = _split_sentences(romanized_text) if romanized_text else []
    
    # Align romanized sentences to native sentences
    aligned_rom_sentences = []
    for i in range(len(sentences)):
        if i < len(rom_sentences):
            aligned_rom_sentences.append(rom_sentences[i])
        else:
            aligned_rom_sentences.append(sentences[i])

    loop      = asyncio.get_event_loop()

    for idx, sentence in enumerate(sentences):
        sentence = sentence.strip()
        if not sentence:
            continue

        rom_sentence = aligned_rom_sentences[idx] if idx < len(aligned_rom_sentences) else sentence

        audio_np: Optional[np.ndarray] = await loop.run_in_executor(
            None,
            _run_generate,
            model,
            sentence,
            resolved_ref,
            ref_text,
            instruct,
            speed,
            num_step,
        )

        if audio_np is None or len(audio_np) == 0:
            logger.warning("[TTS] Empty audio returned for: %r", sentence)
            continue

        yield {
            "audio":          audio_np.astype(np.float32).tobytes(),
            "sample_rate":    SAMPLE_RATE,
            "text":           sentence,
            "romanized_text": rom_sentence,
        }


def _run_generate(
    model,
    text:      str,
    ref_audio: Optional[str],
    ref_text:  Optional[str],
    instruct:  Optional[str],
    speed:     float,
    num_step:  int,
) -> Optional[np.ndarray]:
    """
    Blocking OmniVoice inference – runs in a thread-pool executor.

    model.generate() returns: list[np.ndarray]  (one array per text segment)
    Each array is float32, shape (T,), at 24 kHz.
    """
    try:
        kwargs: dict = dict(text=text, num_step=num_step, speed=speed)

        if ref_audio:
            kwargs["ref_audio"] = ref_audio
            # ref_text is optional – OmniVoice uses Whisper to auto-transcribe if omitted
            if ref_text:
                kwargs["ref_text"] = ref_text
        elif instruct:
            kwargs["instruct"] = instruct
        # else: auto voice – no extra kwargs needed

        audio_list: list = model.generate(**kwargs)

        if not audio_list:
            return None

        # Concatenate all segments into one array
        return np.concatenate([np.asarray(a, dtype=np.float32) for a in audio_list])

    except Exception:
        logger.exception("[TTS] generate() failed for text: %r", text)
        return None


# ---------------------------------------------------------------------------
# Text splitting helper
# ---------------------------------------------------------------------------

def _split_sentences(text: str) -> list[str]:
    """Split on sentence-ending punctuation; fall back to word-count chunks."""
    import re
    parts = re.split(r"(?<=[.!?])\s+", text.strip())
    if len(parts) <= 1:
        words      = text.split()
        chunk_size = 60   # ~15s at average speaking rate
        parts = [
            " ".join(words[i: i + chunk_size])
            for i in range(0, len(words), chunk_size)
        ]
    return [p for p in parts if p.strip()]
