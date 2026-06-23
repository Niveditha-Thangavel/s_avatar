import os
import numpy as np
import torch
import logging

logger = logging.getLogger(__name__)

class LocalTTS:
    """Simple local TTS wrapper using k2-fsa/OmniVoice model.

    The generate() method returns 16-bit PCM bytes at the global TTS_SAMPLE_RATE (24000 Hz).
    """

    def __init__(self, sample_rate: int = 24000):
        self.sample_rate = sample_rate
        
        # Determine device and dtype
        self.device = "cuda" if torch.cuda.is_available() else "cpu"
        self.dtype = torch.float16 if self.device == "cuda" else torch.float32
        
        logger.info(f"[LocalTTS] Loading k2-fsa/OmniVoice model on device={self.device} with dtype={self.dtype}...")
        
        from omnivoice import OmniVoice
        self.model = OmniVoice.from_pretrained(
            "k2-fsa/OmniVoice",
            device_map=self.device,
            dtype=self.dtype
        )
        
        # Load ref_audio and ref_text from current directory (server/)
        current_dir = os.path.dirname(os.path.abspath(__file__))
        self.ref_audio_path = os.path.join(current_dir, "ref_audio.wav")
        self.ref_text_path = os.path.join(current_dir, "ref_text.txt")
        
        if not os.path.exists(self.ref_audio_path):
            raise FileNotFoundError(f"Reference audio not found at: {self.ref_audio_path}")
            
        if os.path.exists(self.ref_text_path):
            with open(self.ref_text_path, "r", encoding="utf-8") as f:
                self.ref_text = f.read().strip()
        else:
            self.ref_text = ""
            logger.warning(f"Reference text file not found at: {self.ref_text_path}")
            
        logger.info(f"[LocalTTS] Loaded ref_audio={self.ref_audio_path}, ref_text='{self.ref_text}'")

    def generate(self, text: str) -> bytes:
        """Return 16-bit PCM audio for *text*."""
        if not text:
            return b""
        
        logger.info(f"[LocalTTS] Generating audio for text: {text}")
        
        # Generate audio via OmniVoice
        audio_output = self.model.generate(
            text=text,
            ref_audio=self.ref_audio_path,
            ref_text=self.ref_text
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
        
        # Convert float waveform (-1.0 to 1.0) to int16 PCM bytes
        pcm = (wav * 32767.0).astype(np.int16)
        return pcm.tobytes()
