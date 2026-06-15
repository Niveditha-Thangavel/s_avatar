"""
pantomatrix.py — Audio-to-blendshape feature extractor

Takes raw audio PCM bytes + sample rate → 52 ARKit blendshape frames at 30 FPS.
Extracts frame-level energy, spectral features, zero-crossing rate and maps
them to plausible blendshape weights via deterministic heuristics.

This is a placeholder that produces reasonable facial animation from audio
features alone. Replace with a trained model for production accuracy.
"""

import logging
import math
import struct
from typing import List, Tuple

import numpy as np

logger = logging.getLogger(__name__)

TARGET_FPS = 30
FRAME_DT   = 1.0 / TARGET_FPS

# 52 ARKit blendshape names
_ALL_BLENDSHAPES = [
    "jawOpen", "jawLeft", "jawRight", "jawForward",
    "mouthClose", "mouthFunnel", "mouthPucker",
    "mouthSmileLeft", "mouthSmileRight",
    "mouthFrownLeft", "mouthFrownRight",
    "mouthDimpleLeft", "mouthDimpleRight",
    "mouthStretchLeft", "mouthStretchRight",
    "mouthRollUpper", "mouthRollLower",
    "mouthShrugUpper", "mouthShrugLower",
    "mouthPressLeft", "mouthPressRight",
    "mouthUpperUpLeft", "mouthUpperUpRight",
    "mouthLowerDownLeft", "mouthLowerDownRight",
    "lipsPucker", "lipsSeal",
    "tongueOut",
    "eyeBlinkLeft", "eyeBlinkRight",
    "eyeSquintLeft", "eyeSquintRight",
    "eyeWideLeft", "eyeWideRight",
    "eyeLookUpLeft", "eyeLookUpRight",
    "eyeLookDownLeft", "eyeLookDownRight",
    "eyeLookInLeft", "eyeLookInRight",
    "eyeLookOutLeft", "eyeLookOutRight",
    "browDownLeft", "browDownRight",
    "browInnerUp", "browOuterUpLeft", "browOuterUpRight",
    "cheekPuff", "cheekSquintLeft", "cheekSquintRight",
    "noseSneerLeft", "noseSneerRight",
]


def _rms(frame: np.ndarray) -> float:
    return float(np.sqrt(np.mean(frame ** 2))) if len(frame) else 0.0


def _zcr(frame: np.ndarray) -> float:
    if len(frame) < 2:
        return 0.0
    signs = frame[:-1] * frame[1:]
    return float(np.sum(signs < 0)) / (len(frame) - 1)


def _spectral_centroid(frame: np.ndarray, sr: int) -> float:
    n = len(frame)
    if n < 4:
        return 0.0
    spec = np.abs(np.fft.rfft(frame))
    freqs = np.fft.rfftfreq(n, d=1.0 / sr)
    if np.sum(spec) < 1e-10:
        return 0.0
    return float(np.sum(freqs * spec) / np.sum(spec))


def _smooth(new: float, prev: float, alpha: float = 0.45) -> float:
    return prev + (new - prev) * alpha


def extract_blendshapes(
    audio_bytes: bytes,
    sample_rate: int,
) -> List[dict]:
    """
    Extract blendshape frames from raw PCM float32 audio.

    Parameters
    ----------
    audio_bytes : bytes
        Raw float32 PCM audio data (little-endian).
    sample_rate : int
        Sample rate of the audio (Hz).

    Returns
    -------
    List[dict]
        Each dict: { "time": float, "blendshapes": { name: weight, ... } }
        at 30 FPS.
    """
    audio = np.frombuffer(audio_bytes, dtype=np.float32)
    if len(audio) == 0:
        return []

    total_duration = len(audio) / sample_rate
    frame_len = int(sample_rate * FRAME_DT)
    num_frames = max(1, int(total_duration * TARGET_FPS))

    # Pre-compute frame features
    features = []
    for i in range(num_frames):
        start = int(i * frame_len)
        end = min(start + frame_len, len(audio))
        if start >= len(audio):
            break
        frame = audio[start:end]
        energy = _rms(frame)
        zcr = _zcr(frame)
        centroid = _spectral_centroid(frame, sample_rate)
        features.append({
            "energy": energy,
            "zcr": zcr,
            "centroid": centroid,
        })

    # Smooth energy envelope
    energies = np.array([f["energy"] for f in features])
    if len(energies) > 1:
        smoothed_energy = np.convolve(energies, np.ones(3) / 3, mode="same")
    else:
        smoothed_energy = energies
    max_energy = max(smoothed_energy) if len(smoothed_energy) else 1.0
    if max_energy < 1e-8:
        max_energy = 1.0

    # Smoothed centroids
    centroids = np.array([f["centroid"] for f in features])
    if len(centroids) > 1:
        smooth_centroids = np.convolve(centroids, np.ones(5) / 5, mode="same")
    else:
        smooth_centroids = centroids
    max_centroid = max(smooth_centroids) if len(smooth_centroids) else 1.0
    if max_centroid < 1e-8:
        max_centroid = 1.0

    norm_centroids = (smooth_centroids / max_centroid).tolist() if max_centroid > 0 else [0.0] * len(features)

    # Track previous frame for smoothing
    prev = {k: 0.0 for k in _ALL_BLENDSHAPES}

    # Blink state
    blink_counter = 0
    blink_interval = int(3.0 * TARGET_FPS)

    result = []
    for idx, (feat, nrg) in enumerate(zip(features, smoothed_energy.tolist())):
        t = idx * FRAME_DT
        nrg_norm = min(nrg / max_energy, 1.0) if max_energy > 0 else 0.0
        centroid_n = norm_centroids[idx] if idx < len(norm_centroids) else 0.5
        zcr = feat["zcr"]

        frame = {}

        # ── Jaw (driven by energy) ──
        frame["jawOpen"] = nrg_norm * 0.85
        frame["jawLeft"] = math.sin(t * 23.0) * 0.015
        frame["jawRight"] = math.sin(t * 23.0 + 1.2) * 0.015
        frame["jawForward"] = nrg_norm * 0.08

        # ── Mouth shapes ──
        smile_factor = max(0.0, (centroid_n - 0.35) / 0.65) * nrg_norm
        frame["mouthSmileLeft"] = smile_factor * 0.6
        frame["mouthSmileRight"] = smile_factor * 0.6
        frame["mouthFrownLeft"] = max(0.0, (1.0 - centroid_n - 0.2)) * nrg_norm * 0.3
        frame["mouthFrownRight"] = max(0.0, (1.0 - centroid_n - 0.2)) * nrg_norm * 0.3

        frame["mouthClose"] = max(0.0, 1.0 - frame["jawOpen"] * 2.0) * 0.3
        mouth_stretch = nrg_norm * 0.2
        frame["mouthStretchLeft"] = mouth_stretch
        frame["mouthStretchRight"] = mouth_stretch
        frame["mouthDimpleLeft"] = smile_factor * 0.15
        frame["mouthDimpleRight"] = smile_factor * 0.15

        pucker = max(0.0, (0.25 - centroid_n) * 2.0) * nrg_norm
        frame["mouthFunnel"] = pucker * 0.7
        frame["mouthPucker"] = pucker * 0.5
        frame["lipsPucker"] = pucker * 0.4
        frame["lipsSeal"] = max(0.0, 1.0 - nrg_norm * 3.0) * 0.2

        frame["mouthRollUpper"] = nrg_norm * 0.1
        frame["mouthRollLower"] = nrg_norm * 0.1
        frame["mouthShrugUpper"] = nrg_norm * 0.05
        frame["mouthShrugLower"] = nrg_norm * 0.05
        frame["mouthPressLeft"] = (1.0 - nrg_norm) * zcr * 0.3
        frame["mouthPressRight"] = (1.0 - nrg_norm) * zcr * 0.3
        frame["mouthUpperUpLeft"] = nrg_norm * 0.2
        frame["mouthUpperUpRight"] = nrg_norm * 0.2
        frame["mouthLowerDownLeft"] = nrg_norm * 0.15
        frame["mouthLowerDownRight"] = nrg_norm * 0.15
        frame["tongueOut"] = 0.0

        # ── Eyes ──
        blink_counter += 1
        if blink_counter >= blink_interval:
            blink_progress = (blink_counter - blink_interval) / (0.15 * TARGET_FPS)
            if blink_progress < 1.0:
                blink_val = math.sin(blink_progress * math.pi)
            else:
                blink_val = 0.0
                blink_counter = 0
                blink_interval = int((2.5 + 3.0 * np.random.random()) * TARGET_FPS)
        else:
            blink_val = 0.0

        frame["eyeBlinkLeft"] = blink_val
        frame["eyeBlinkRight"] = blink_val

        squint = max(0.0, nrg_norm - 0.5) * 2.0 * 0.4
        frame["eyeSquintLeft"] = squint
        frame["eyeSquintRight"] = squint
        frame["eyeWideLeft"] = max(0.0, 1.0 - squint * 2.0) * 0.3
        frame["eyeWideRight"] = max(0.0, 1.0 - squint * 2.0) * 0.3

        gaze_x = math.sin(t * 2.7) * 0.06
        gaze_y = math.cos(t * 1.9) * 0.04
        if gaze_x > 0:
            frame["eyeLookInLeft"] = gaze_x * 0.5
            frame["eyeLookOutRight"] = gaze_x * 0.5
        else:
            frame["eyeLookOutLeft"] = -gaze_x * 0.5
            frame["eyeLookInRight"] = -gaze_x * 0.5
        if gaze_y > 0:
            frame["eyeLookDownLeft"] = gaze_y * 0.5
            frame["eyeLookDownRight"] = gaze_y * 0.5
        else:
            frame["eyeLookUpLeft"] = -gaze_y * 0.5
            frame["eyeLookUpRight"] = -gaze_y * 0.5

        # ── Brows ──
        brow_furrow = max(0.0, nrg_norm - 0.4) * 0.35
        frame["browDownLeft"] = brow_furrow
        frame["browDownRight"] = brow_furrow
        frame["browInnerUp"] = max(0.0, (centroid_n - 0.6) * 0.4)
        brow_raise = max(0.0, 1.0 - nrg_norm * 1.5) * 0.15
        frame["browOuterUpLeft"] = brow_raise
        frame["browOuterUpRight"] = brow_raise

        # ── Cheeks ──
        frame["cheekPuff"] = nrg_norm * zcr * 0.15
        frame["cheekSquintLeft"] = smile_factor * 0.35
        frame["cheekSquintRight"] = smile_factor * 0.35

        # ── Nose ──
        frame["noseSneerLeft"] = nrg_norm * zcr * 0.1
        frame["noseSneerRight"] = nrg_norm * zcr * 0.1

        # Clip all values to [0, 1]
        for k in list(frame.keys()):
            frame[k] = max(0.0, min(1.0, frame[k]))

        # Temporal smoothing
        for k in _ALL_BLENDSHAPES:
            val = frame.get(k, 0.0)
            smoothed = _smooth(val, prev.get(k, 0.0))
            frame[k] = max(0.0, min(1.0, smoothed))
            prev[k] = frame[k]

        result.append({
            "time": round(t, 3),
            "blendshapes": frame,
        })

    logger.info("[PantoMatrix] Extracted %d blendshape frames (%.2fs @ %dFPS)",
                len(result), total_duration, TARGET_FPS)
    return result
