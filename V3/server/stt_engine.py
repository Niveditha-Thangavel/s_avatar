"""
stt_engine.py — Whisper large-v3-turbo STT  (GPU-optimised)

Model: openai/whisper-large-v3-turbo
  • 809M params, ~4× faster than large-v3, near-identical WER
  • 99-language auto-detection via model.detect_language()
  • Returns (transcript, detected_lang_iso639, "neutral")

Language detection strategy
────────────────────────────
  1. Call model.detect_language(input_features) — returns a single token ID
     from the Whisper vocab (50259 = <|en|>, 50276 = <|hi|>, …)
  2. Convert that token ID to ISO 639-1 using the tokenizer's vocab
  3. Pass the detected language string to model.generate() so the
     transcription stays in the speaker's native script
     (NOT forced to English)

GPU path (CUDA):
  float16, torch.compile(reduce-overhead), dedicated thread executor
MPS / CPU:
  float32, no compile
"""

import asyncio
import concurrent.futures
import logging
import os
from typing import Tuple

import numpy as np
import torch

logger      = logging.getLogger(__name__)
SAMPLE_RATE = 16_000   # Whisper always expects 16 kHz mono float32

WHISPER_MODEL_ID = os.environ.get(
    "WHISPER_MODEL_ID", "openai/whisper-large-v3-turbo"
)
WHISPER_CACHE = os.environ.get(
    "TRANSFORMERS_CACHE",
    os.path.join(os.path.expanduser("~"), ".cache", "huggingface"),
)

_model      = None   # AutoModelForSpeechSeq2Seq
_processor  = None   # AutoProcessor
_model_lock = asyncio.Lock()

# Dedicated single-thread executor — keeps Whisper off the shared thread pool
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
    return torch.float16 if device.startswith("cuda") else torch.float32


async def get_model():
    global _model, _processor
    if _model is not None:
        return _model, _processor

    async with _model_lock:
        if _model is not None:
            return _model, _processor

        from transformers import AutoModelForSpeechSeq2Seq, AutoProcessor

        device = _get_device()
        dtype  = _get_dtype(device)
        logger.info("[STT] Loading %s on %s (%s) …", WHISPER_MODEL_ID, device, dtype)

        m = AutoModelForSpeechSeq2Seq.from_pretrained(
            WHISPER_MODEL_ID,
            torch_dtype=dtype,
            low_cpu_mem_usage=True,
            use_safetensors=True,
            cache_dir=WHISPER_CACHE,
        )
        m.to(device).eval()

        if device.startswith("cuda"):
            torch.backends.cudnn.benchmark = True
            logger.info("[STT] torch.compile (reduce_overhead) …")
            try:
                m = torch.compile(m, mode="reduce-overhead", fullgraph=False)
                logger.info("[STT] torch.compile done")
            except Exception as exc:
                logger.warning("[STT] torch.compile skipped: %s", exc)

        p = AutoProcessor.from_pretrained(WHISPER_MODEL_ID, cache_dir=WHISPER_CACHE)

        _model     = m
        _processor = p
        logger.info("[STT] ✅ Whisper large-v3-turbo ready on %s", device)
        return _model, _processor


async def load_stt_models():
    """Warm-up entry point called at server startup."""
    await get_model()


async def transcribe(audio_np: np.ndarray) -> Tuple[str, str, str]:
    """
    Transcribe audio_np (float32 mono, 16 kHz) with Whisper large-v3-turbo.

    Strategy
    --------
    1. model.detect_language()  → token ID → ISO 639-1 code
       This is Whisper's own language-ID head — much more reliable than
       reading raw token positions from model.generate() output.
    2. model.generate(language=detected_code, task='transcribe')
       Forces the decoder to output the speaker's native script.
    3. tokenizer.decode(skip_special_tokens=True) → clean transcript.

    Returns
    -------
    (transcript, lang_iso639, "neutral")
    """
    model, processor = await get_model()
    loop = asyncio.get_event_loop()

    def _run():
        device = _get_device()
        dtype  = _get_dtype(device)
        feat   = processor.feature_extractor

        # ── 1. Compute mel features ───────────────────────────────────────
        inputs         = feat(audio_np, return_tensors="pt", sampling_rate=SAMPLE_RATE)
        input_features = inputs.input_features.to(device).to(dtype)

        ctx = (
            torch.cuda.amp.autocast(dtype=torch.float16)
            if device.startswith("cuda")
            else _null_ctx()
        )

        # ── 2. Detect language (Whisper's own LID head) ───────────────────
        with torch.no_grad(), ctx:
            lang_token_ids = model.detect_language(input_features)

        # lang_token_ids is a 1-D tensor of shape [batch], each value is a
        # token ID in the Whisper vocab e.g. 50259 (<|en|>), 50276 (<|hi|>)
        lang_token_id = int(lang_token_ids[0].item())

        # Convert token ID → ISO 639-1 string
        # The token string looks like "<|en|>" or "<|hi|>"
        raw_lang_str = processor.tokenizer.decode([lang_token_id]).strip()
        # strip angle brackets and pipes: "<|en|>" → "en"
        lang = raw_lang_str.replace("<|", "").replace("|>", "").strip()
        if not lang or len(lang) > 5:
            lang = "en"

        logger.info("[STT] detected lang token=%d → %s", lang_token_id, lang)

        # ── 3. Transcribe in detected language (native script) ────────────
        with torch.no_grad(), ctx:
            generated_ids = model.generate(
                input_features,
                language=lang,            # ← native language, NOT "en"
                task="transcribe",        # ← keep native script
                temperature=0.0,          # greedy — fastest + most stable
                return_timestamps=False,
                # Disable fallback thresholds — they require logprobs which are
                # unavailable under greedy (temperature=0) in transformers ≥5.x
                compression_ratio_threshold=None,
                logprob_threshold=None,
                no_speech_threshold=None,
            )

        text = processor.tokenizer.decode(
            generated_ids[0], skip_special_tokens=True
        ).strip()
        logger.info("[STT] lang=%s text=%s", lang, text[:80])
        return text, lang, "neutral"

    return await loop.run_in_executor(_executor, _run)


class _null_ctx:
    def __enter__(self): return self
    def __exit__(self, *_): pass
