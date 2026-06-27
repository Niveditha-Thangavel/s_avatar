import os
import logging

logger = logging.getLogger(__name__)


class LocalTTS:
    """Local TTS wrapper using k2-fsa/OmniVoice model.

    The generate() method returns 16-bit PCM bytes at the configured sample_rate (24000 Hz).
    All heavy imports (torch, omnivoice) are deferred to __init__ so that a
    failing TTS doesn't prevent the rest of the server from starting.
    """

    def __init__(self, sample_rate: int = 24000):
        self.sample_rate = sample_rate
        self._ready = False
        self.model = None

        try:
            import torch
            import numpy as np  # noqa: F401 — confirm numpy is available

            # Determine device and dtype
            self.device = "cuda" if torch.cuda.is_available() else "cpu"
            self.dtype = torch.float16 if self.device == "cuda" else torch.float32

            logger.info(
                "[LocalTTS] Loading k2-fsa/OmniVoice model on device=%s dtype=%s …",
                self.device, self.dtype,
            )

            from omnivoice import OmniVoice

            self.model = OmniVoice.from_pretrained(
                "k2-fsa/OmniVoice",
                device_map=self.device,
                dtype=self.dtype,
            )

            # Load ref_audio and ref_text from current directory (server/)
            current_dir = os.path.dirname(os.path.abspath(__file__))
            self.ref_audio_path = os.path.join(current_dir, "ref_audio.wav")
            self.ref_text_path = os.path.join(current_dir, "ref_text.txt")

            if not os.path.exists(self.ref_audio_path):
                raise FileNotFoundError(
                    f"Reference audio not found at: {self.ref_audio_path}"
                )

            if os.path.exists(self.ref_text_path):
                with open(self.ref_text_path, "r", encoding="utf-8") as f:
                    self.ref_text = f.read().strip()
            else:
                self.ref_text = ""
                logger.warning(
                    "[LocalTTS] Reference text file not found at: %s", self.ref_text_path
                )

            self._ready = True
            logger.info(
                "[LocalTTS] ✅ Ready — ref_audio=%s, ref_text='%s'",
                self.ref_audio_path, self.ref_text,
            )

        except Exception as exc:
            logger.error(
                "[LocalTTS] ❌ Failed to initialise OmniVoice — "
                "TTS will return silent audio until this is fixed: %s",
                exc,
                exc_info=True,
            )

    @property
    def is_ready(self) -> bool:
        return self._ready

    def generate(self, text: str, ref_audio: str = None, ref_text: str = None) -> bytes:
        """Return 16-bit PCM audio for *text*.

        If the model failed to load, returns 1 second of silence so the
        pipeline can still be tested end-to-end.
        """
        import numpy as np
        import os

        if not text:
            return b""

        # ── Fallback: silent PCM ─────────────────────────────────────────
        if not self._ready or self.model is None:
            logger.warning("[LocalTTS] Model not loaded — returning 1s silence for: %s", text)
            silence = np.zeros(self.sample_rate, dtype=np.float32)
            return silence.tobytes()

        # ── Normal path: OmniVoice inference ─────────────────────────────
        import torch

        logger.info("[LocalTTS] Generating audio for: %s", text[:80])

        # Dynamic reference voice mapping
        ref_audio_to_use = ref_audio if (ref_audio and os.path.exists(ref_audio)) else self.ref_audio_path
        ref_text_to_use = ref_text if ref_text is not None else self.ref_text

        audio_output = self.model.generate(
            text=text,
            ref_audio=ref_audio_to_use,
            ref_text=ref_text_to_use,
        )

        # Extract audio numpy array
        if isinstance(audio_output, (list, tuple)):
            wav = audio_output[0]
        else:
            wav = audio_output

        if isinstance(wav, torch.Tensor):
            wav = wav.cpu().numpy()

        # Ensure we have a 1D numpy array
        wav = np.squeeze(wav)

        # Convert float waveform (-1.0 to 1.0) to float32 PCM bytes
        pcm = wav.astype(np.float32)
        return pcm.tobytes()
