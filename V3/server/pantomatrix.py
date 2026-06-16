"""
pantomatrix.py — Audio-to-blendshape feature extractor  (v5 — linear cross-fade)

Architecture
────────────
1. Sub-band energy classification  →  7 phoneme classes per frame
2. Each class has a fixed target pose (blendshape dict)
3. Transitions between poses are computed with a **linear cross-fade window**:
   when the class changes, the output blends linearly from the previous pose
   to the new pose over TRANSITION_FRAMES frames:
       weight(k, t) = prev_pose(k) + t/T * (next_pose(k) - prev_pose(k))
   where t ∈ [0, T] and T = TRANSITION_FRAMES.
4. Energy scaling: each pose is multiplied by sqrt(nrg_norm) so the mouth
   moves proportionally to loudness.
5. Rest/silence pose is ALL ZEROS — no lipsSeal, no pucker.
   The animation matrix ends naturally at zero so no shapes are frozen
   on the face after speech finishes.

Phoneme classes
───────────────
  SILENCE    — all zeros (natural rest, no puckering)
  LOW_VOWEL  — /a/ /ɑ/ /ɔ/  — wide jaw, low lip corners
  MID_VOWEL  — /ɛ/ /ɪ/ /ʌ/  — mid jaw, neutral lips
  HIGH_VOWEL — /i/ /e/ /eɪ/ — narrow jaw, spread lips
  FRICATIVE  — /v/ /ð/ /z/  — near-closed jaw, lip press
  SIBILANT   — /s/ /ʃ/ /f/  — minimal jaw, teeth-near, press
  BILABIAL   — /p/ /b/ /m/  — lips closed (seal), then burst
"""

import logging
import math
from typing import List

import numpy as np

logger = logging.getLogger(__name__)

TARGET_FPS         = 30
FRAME_DT           = 1.0 / TARGET_FPS
TRANSITION_FRAMES  = 3          # linear ramp = 3/30 s = 100 ms between poses

# ── 52 ARKit blendshape names ─────────────────────────────────────────────────
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

# ── Class IDs ─────────────────────────────────────────────────────────────────
_CLS_SILENCE    = 0
_CLS_LOW_VOWEL  = 1
_CLS_MID_VOWEL  = 2
_CLS_HIGH_VOWEL = 3
_CLS_FRICATIVE  = 4
_CLS_SIBILANT   = 5
_CLS_BILABIAL   = 6

# ── Pose templates — these are NORMALISED max weights (energy-scaled at runtime)
# ── Rest pose is intentionally ALL ZEROS — no shapes frozen after speech ends.
_POSE = {
    _CLS_SILENCE: {},   # ← empty dict = every shape is 0.0

    _CLS_LOW_VOWEL: {
        # /a/ /ɑ/: wide jaw, upper lip up, lower lip down, slight frown
        "jawOpen":             0.82,
        "jawForward":          0.08,
        "mouthUpperUpLeft":    0.35,
        "mouthUpperUpRight":   0.35,
        "mouthLowerDownLeft":  0.42,
        "mouthLowerDownRight": 0.42,
        "mouthFrownLeft":      0.12,
        "mouthFrownRight":     0.12,
        "mouthShrugLower":     0.18,
        "cheekSquintLeft":     0.08,
        "cheekSquintRight":    0.08,
    },

    _CLS_MID_VOWEL: {
        # /ɛ/ /ɪ/: mid jaw, slightly spread
        "jawOpen":             0.50,
        "jawForward":          0.05,
        "mouthUpperUpLeft":    0.22,
        "mouthUpperUpRight":   0.22,
        "mouthLowerDownLeft":  0.22,
        "mouthLowerDownRight": 0.22,
        "mouthStretchLeft":    0.18,
        "mouthStretchRight":   0.18,
        "mouthShrugUpper":     0.08,
        "mouthRollLower":      0.08,
    },

    _CLS_HIGH_VOWEL: {
        # /i/ /e/: narrow jaw, wide smile, lip corners pulled back
        "jawOpen":             0.25,
        "mouthSmileLeft":      0.55,
        "mouthSmileRight":     0.55,
        "mouthStretchLeft":    0.45,
        "mouthStretchRight":   0.45,
        "mouthDimpleLeft":     0.22,
        "mouthDimpleRight":    0.22,
        "mouthUpperUpLeft":    0.28,
        "mouthUpperUpRight":   0.28,
        "cheekSquintLeft":     0.22,
        "cheekSquintRight":    0.22,
        "browInnerUp":         0.10,
    },

    _CLS_FRICATIVE: {
        # /v/ /ð/ /z/: near-closed, lower lip under teeth
        "jawOpen":             0.16,
        "mouthPressLeft":      0.32,
        "mouthPressRight":     0.32,
        "mouthStretchLeft":    0.12,
        "mouthStretchRight":   0.12,
        "mouthUpperUpLeft":    0.18,
        "mouthUpperUpRight":   0.18,
        "noseSneerLeft":       0.10,
        "noseSneerRight":      0.10,
    },

    _CLS_SIBILANT: {
        # /s/ /ʃ/ /f/: jaw nearly shut, teeth close, lips retracted
        "jawOpen":             0.07,
        "mouthPressLeft":      0.52,
        "mouthPressRight":     0.52,
        "mouthStretchLeft":    0.28,
        "mouthStretchRight":   0.28,
        "mouthUpperUpLeft":    0.22,
        "mouthUpperUpRight":   0.22,
        "noseSneerLeft":       0.18,
        "noseSneerRight":      0.18,
    },

    _CLS_BILABIAL: {
        # /p/ /b/ /m/: lips together, zero jaw, slight cheek puff
        "jawOpen":             0.02,
        "mouthPressLeft":      0.28,
        "mouthPressRight":     0.28,
        "mouthRollUpper":      0.18,
        "mouthRollLower":      0.18,
        "cheekPuff":           0.08,
    },
}

# Pre-build zero-filled full pose for every class (vectorised below)
_ZERO_POSE = {k: 0.0 for k in _ALL_BLENDSHAPES}

def _full_pose(cls_id: int) -> dict:
    """Return a complete 52-shape pose dict for a class, unscaled."""
    base = _ZERO_POSE.copy()
    base.update(_POSE[cls_id])
    return base


# ── Feature helpers ───────────────────────────────────────────────────────────

def _rms(frame: np.ndarray) -> float:
    return float(np.sqrt(np.mean(frame ** 2))) if len(frame) else 0.0


def _zcr(frame: np.ndarray) -> float:
    if len(frame) < 2:
        return 0.0
    return float(np.sum(frame[:-1] * frame[1:] < 0)) / (len(frame) - 1)


def _subband_ratios(frame: np.ndarray, sr: int):
    n = len(frame)
    if n < 8:
        return 0.0, 0.0, 0.0
    spec  = np.abs(np.fft.rfft(frame * np.hanning(n))) ** 2
    freqs = np.fft.rfftfreq(n, 1.0 / sr)
    total = np.sum(spec)
    if total < 1e-14:
        return 0.0, 0.0, 0.0
    b_low  = float(np.sum(spec[freqs < 500])                         / total)
    b_mid  = float(np.sum(spec[(freqs >= 500) & (freqs < 2000)])     / total)
    b_high = float(np.sum(spec[freqs >= 2000])                       / total)
    return b_low, b_mid, b_high


def _classify(nrg_norm: float, zcr: float,
              b_low: float, b_mid: float, b_high: float,
              prev_cls: int) -> int:
    if nrg_norm < 0.04:
        return _CLS_SILENCE
    if zcr > 0.28 and b_high > 0.55:
        return _CLS_SIBILANT
    if zcr > 0.16 and b_high > 0.35:
        return _CLS_FRICATIVE
    if nrg_norm < 0.15 and zcr < 0.08 and b_low > 0.75 and prev_cls in (_CLS_SILENCE, _CLS_BILABIAL):
        return _CLS_BILABIAL
    if b_mid > 0.65 and b_low < 0.25:
        return _CLS_HIGH_VOWEL
    if b_low > 0.75:
        return _CLS_LOW_VOWEL
    return _CLS_MID_VOWEL


# ── Linear interpolation between two full poses ───────────────────────────────

def _lerp_poses(pose_a: dict, pose_b: dict, t: float) -> dict:
    """
    Linear interpolation: result(k) = pose_a(k) + t * (pose_b(k) - pose_a(k))
    t ∈ [0.0, 1.0]  →  0.0 = pure A, 1.0 = pure B.
    """
    result = {}
    # All keys are the union of the two poses — missing key treated as 0.
    all_keys = set(pose_a.keys()) | set(pose_b.keys())
    for k in all_keys:
        a = pose_a.get(k, 0.0)
        b = pose_b.get(k, 0.0)
        result[k] = a + t * (b - a)
    return result


# ── Main extractor ─────────────────────────────────────────────────────────────

def extract_blendshapes(
    audio_bytes: bytes,
    sample_rate: int,
) -> List[dict]:
    """
    Extract 52-blendshape ARKit frames at 30 FPS from raw float32 PCM audio.

    Parameters
    ----------
    audio_bytes : bytes   — raw float32 PCM, little-endian
    sample_rate : int     — Hz (expected 24000 from OmniVoice)

    Returns
    -------
    List[dict] — [{"time": float, "blendshapes": {name: weight}}, ...]
    """
    audio = np.frombuffer(audio_bytes, dtype=np.float32)
    if len(audio) == 0:
        return []

    total_duration = len(audio) / sample_rate
    frame_len      = int(sample_rate * FRAME_DT)
    num_frames     = max(1, int(total_duration * TARGET_FPS))

    # ── 1. Per-frame features ─────────────────────────────────────────────────
    raw_rms   = np.zeros(num_frames)
    raw_zcr   = np.zeros(num_frames)
    raw_blow  = np.zeros(num_frames)
    raw_bmid  = np.zeros(num_frames)
    raw_bhigh = np.zeros(num_frames)

    for i in range(num_frames):
        s = i * frame_len
        e = min(s + frame_len, len(audio))
        if s >= len(audio):
            break
        f             = audio[s:e]
        raw_rms[i]    = _rms(f)
        raw_zcr[i]    = _zcr(f)
        bl, bm, bh    = _subband_ratios(f, sample_rate)
        raw_blow[i]   = bl
        raw_bmid[i]   = bm
        raw_bhigh[i]  = bh

    # ── 2. Energy normalisation (95th-percentile) ─────────────────────────────
    smoothed_rms = np.convolve(raw_rms, [0.20, 0.60, 0.20], mode="same")
    nz           = smoothed_rms[smoothed_rms > 1e-7]
    peak_rms     = float(np.percentile(nz, 95)) if len(nz) else 1e-7
    nrg_norms    = np.clip(smoothed_rms / peak_rms, 0.0, 1.0)

    # ── 3. Classify every frame ───────────────────────────────────────────────
    classes   = np.zeros(num_frames, dtype=int)
    prev_cls  = _CLS_SILENCE
    for i in range(num_frames):
        cls        = _classify(nrg_norms[i], raw_zcr[i],
                               raw_blow[i], raw_bmid[i], raw_bhigh[i],
                               prev_cls)
        classes[i] = cls
        prev_cls   = cls

    # ── 4. Build target poses per frame (energy-scaled) ───────────────────────
    # target_poses[i] = full 52-shape dict scaled by sqrt(nrg_norm)
    target_poses: List[dict] = []
    for i in range(num_frames):
        base  = _full_pose(classes[i])
        scale = math.sqrt(nrg_norms[i]) if classes[i] != _CLS_SILENCE else 1.0
        # silence scale = 1.0 but _POSE[SILENCE] is all zeros, so result is 0.
        scaled = {k: v * scale for k, v in base.items()}
        target_poses.append(scaled)

    # ── 5. Linear cross-fade between consecutive class transitions ────────────
    #
    # For every frame i, if classes[i] differs from classes[i-1]:
    #   we ramp linearly from target_poses[i-1]  →  target_poses[i]
    #   over TRANSITION_FRAMES frames.
    #
    # This replaces the IIR smoothing (which caused every class to
    # feel sluggish) with an explicit linear ramp that:
    #   • Always reaches the target within TRANSITION_FRAMES frames
    #   • Doesn't accumulate lag across multiple class changes
    #   • Produces predictable, tunable transition timing
    #
    blended_poses: List[dict] = [None] * num_frames

    # Walk through and apply cross-fades
    i = 0
    while i < num_frames:
        blended_poses[i] = target_poses[i].copy()

        # Detect class change starting at i+1
        if i < num_frames - 1 and classes[i + 1] != classes[i]:
            pose_from = target_poses[i]
            # Find the target pose for the new class — use i+1
            pose_to   = target_poses[i + 1]
            T         = TRANSITION_FRAMES

            # Apply linear ramp over T frames (from i+1 to i+T inclusive)
            for step in range(1, T + 1):
                j = i + step
                if j >= num_frames:
                    break
                t_norm = step / T   # 0 < t_norm ≤ 1.0  — strictly linear
                blended = _lerp_poses(pose_from, pose_to, t_norm)
                blended_poses[j] = blended

            # Skip forward past the transition window (those frames are set)
            i += T
        else:
            i += 1

    # Fill any gaps (shouldn't happen, but guard)
    for i in range(num_frames):
        if blended_poses[i] is None:
            blended_poses[i] = target_poses[i].copy()

    # ── 6. Eye / brow / gaze tracks (independent of phonemes) ────────────────
    blink_counter  = 0
    blink_interval = int(3.2 * TARGET_FPS)

    result = []

    for idx in range(num_frames):
        t        = idx * FRAME_DT
        nrg_norm = float(nrg_norms[idx])
        pose     = blended_poses[idx]

        # Build full frame from blended phoneme pose
        frame = {k: max(0.0, min(1.0, pose.get(k, 0.0))) for k in _ALL_BLENDSHAPES}

        # Jaw micro-tremor (prevents robotic stillness on sustained vowels)
        if classes[idx] not in (_CLS_SILENCE, _CLS_BILABIAL, _CLS_SIBILANT):
            micro = math.sin(t * 31.4) * 0.012 * nrg_norm
            frame["jawOpen"] = max(0.0, min(1.0, frame["jawOpen"] + micro))

        # Jaw lateral drift
        if classes[idx] not in (_CLS_SILENCE, _CLS_BILABIAL):
            drift = math.sin(t * 17.0) * 0.007 * nrg_norm
            frame["jawLeft"]    = max(0.0,  drift)
            frame["jawRight"]   = max(0.0, -drift)
            frame["jawForward"] = max(0.0, frame.get("jawForward", 0.0))

        # Tongue out: always 0
        frame["tongueOut"] = 0.0

        # Blink
        blink_counter += 1
        if blink_counter >= blink_interval:
            prog = (blink_counter - blink_interval) / max(1, int(0.10 * TARGET_FPS))
            if prog < 1.0:
                bv = math.sin(prog * math.pi)
            else:
                bv             = 0.0
                blink_counter  = 0
                blink_interval = int((2.5 + 3.0 * np.random.random()) * TARGET_FPS)
        else:
            bv = 0.0
        frame["eyeBlinkLeft"]  = bv
        frame["eyeBlinkRight"] = bv

        sq = max(0.0, nrg_norm - 0.65) * 1.8 * 0.28
        frame["eyeSquintLeft"]  = sq
        frame["eyeSquintRight"] = sq
        frame["eyeWideLeft"]    = max(0.0, 1.0 - sq * 4.0) * 0.18
        frame["eyeWideRight"]   = max(0.0, 1.0 - sq * 4.0) * 0.18

        # Gaze saccade
        gx = math.sin(t * 2.5) * 0.05
        gy = math.cos(t * 1.8) * 0.03
        frame["eyeLookInLeft"]    = max(0.0,  gx) * 0.5
        frame["eyeLookOutLeft"]   = max(0.0, -gx) * 0.5
        frame["eyeLookInRight"]   = max(0.0, -gx) * 0.5
        frame["eyeLookOutRight"]  = max(0.0,  gx) * 0.5
        frame["eyeLookDownLeft"]  = max(0.0,  gy) * 0.5
        frame["eyeLookDownRight"] = max(0.0,  gy) * 0.5
        frame["eyeLookUpLeft"]    = max(0.0, -gy) * 0.5
        frame["eyeLookUpRight"]   = max(0.0, -gy) * 0.5

        # Brows
        frame["browDownLeft"]     = max(0.0, nrg_norm - 0.55) * 0.22
        frame["browDownRight"]    = max(0.0, nrg_norm - 0.55) * 0.22
        frame["browInnerUp"]      = frame.get("browInnerUp", 0.0)   # from pose
        brow_r                    = (1.0 - min(nrg_norm * 2.0, 1.0)) * 0.09
        frame["browOuterUpLeft"]  = brow_r
        frame["browOuterUpRight"] = brow_r

        # Final clip
        for k in _ALL_BLENDSHAPES:
            frame[k] = max(0.0, min(1.0, frame.get(k, 0.0)))

        result.append({"time": round(t, 4), "blendshapes": frame})

    logger.info(
        "[PantoMatrix] %d frames, %.2fs  classes=%s",
        len(result), total_duration,
        {c: int(np.sum(classes == c)) for c in range(7)},
    )
    return result
