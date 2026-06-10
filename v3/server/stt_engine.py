"""
stt_engine.py
Speech-to-Text using faster-whisper (whisper-large-v3-turbo).

Strategy: NO custom VAD.
  - The client streams raw Int16 PCM frames over WebSocket.
  - All frames are accumulated in a simple buffer.
  - When the client sends { type: "stop" }, the entire buffer is passed
    directly to Whisper in one shot.
  - Whisper's own built-in Silero VAD (vad_filter=True) handles internal
    silence stripping before decoding — no custom energy threshold needed.

This avoids the classic problem of custom energy-VAD splitting natural
inter-word pauses (60–300ms) into separate utterances.

faster-whisper API reference:
    model = WhisperModel("large-v3-turbo", device="cuda", compute_type="float16")
    segments, info = model.transcribe(audio_np_float32_16khz, beam_size=5)
    text = " ".join(seg.text for seg in segments).strip()
    # NOTE: segments is a generator — must be fully consumed to run inference.
"""

import asyncio
import logging
import os
import time
from typing import Optional

import numpy as np
import torch

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Whisper singleton
# ---------------------------------------------------------------------------
_whisper      = None
_whisper_lock = asyncio.Lock()

WHISPER_MODEL = "large-v3-turbo"


def _detect_device() -> tuple[str, str]:
    """
    Return (device, compute_type).
    Reads WHISPER_DEVICE env var first, then auto-detects.

    NVIDIA GPU:  set WHISPER_DEVICE=cuda  → float16
    CPU (Mac):   unset or cpu             → int8
    """
    forced = os.environ.get("WHISPER_DEVICE", "").lower()
    if forced:
        compute = "float16" if forced.startswith("cuda") else "int8"
        return forced, compute
    if torch.cuda.is_available():
        return "cuda", "float16"
    return "cpu", "int8"


async def get_whisper():
    """Load faster-whisper once at startup and return the cached instance."""
    global _whisper
    if _whisper is not None:
        return _whisper

    async with _whisper_lock:
        if _whisper is not None:
            return _whisper

        from faster_whisper import WhisperModel

        device, compute = _detect_device()
        logger.info("[STT] Loading faster-whisper %s on %s (%s) ...",
                    WHISPER_MODEL, device, compute)
        t0 = time.perf_counter()

        _whisper = WhisperModel(WHISPER_MODEL, device=device, compute_type=compute)

        logger.info("[STT] Whisper ready in %.2fs", time.perf_counter() - t0)
        return _whisper


# ---------------------------------------------------------------------------
# Transcribe
# ---------------------------------------------------------------------------

async def transcribe(audio_f32: np.ndarray, language: Optional[str] = None) -> str:
    """
    Transcribe a float32 mono 16 kHz numpy array.
    Passes the whole buffer to Whisper — no pre-segmentation.
    Returns the transcript string, or empty string if nothing recognised.
    """
    if len(audio_f32) < 16_000 * 0.1:   # skip clips shorter than 100ms
        return ""

    model = await get_whisper()
    loop  = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _run_transcribe, model, audio_f32, language)


def _run_transcribe(model, audio: np.ndarray, language: Optional[str]) -> str:
    """
    Blocking inference — runs in thread-pool executor so the event loop
    stays free for other WebSocket connections while Whisper is working.

    Key: segments is a GENERATOR. It must be fully consumed (list() or for loop)
    before inference actually runs.
    """
    try:
        segments_gen, info = model.transcribe(
            audio,
            language=language or None,
            beam_size=5,
            vad_filter=True,                       # Whisper's built-in Silero VAD
            vad_parameters=dict(
                threshold=0.3,                     # lower = more sensitive
                min_speech_duration_ms=100,
                min_silence_duration_ms=300,
            ),
            word_timestamps=False,
            condition_on_previous_text=False,
        )
        # Consume the generator — this is where inference actually executes
        text = " ".join(seg.text for seg in segments_gen).strip()
        logger.info("[STT] Detected language: %s (%.0f%%) | Transcript: %r",
                    info.language, info.language_probability * 100, text)
        return text
    except Exception:
        logger.exception("[STT] Transcription failed")
        return ""


# ---------------------------------------------------------------------------
# AudioBuffer — replaces VADState
# ---------------------------------------------------------------------------

class AudioBuffer:
    """
    Simple accumulator. Collects all incoming Int16 PCM frames
    (already converted to float32 by the caller) and returns the
    full buffer when flush() is called.

    No energy analysis, no segmentation — Whisper handles everything.
    """

    SAMPLE_RATE = 16_000

    def __init__(self):
        self._chunks: list[np.ndarray] = []

    def push(self, pcm_f32: np.ndarray):
        """Append a float32 PCM chunk to the buffer."""
        self._chunks.append(pcm_f32)

    def flush(self) -> Optional[np.ndarray]:
        """
        Return the accumulated audio as a single float32 array and reset.
        Returns None if no audio has been received.
        """
        if not self._chunks:
            return None
        audio = np.concatenate(self._chunks).astype(np.float32)
        self._chunks = []
        duration = len(audio) / self.SAMPLE_RATE
        logger.info("[STT] Flushing %.2fs of audio to Whisper", duration)
        return audio

    def reset(self):
        """Discard buffer without transcribing."""
        self._chunks = []

    @property
    def duration_s(self) -> float:
        """Current buffered duration in seconds."""
        return sum(len(c) for c in self._chunks) / self.SAMPLE_RATE
