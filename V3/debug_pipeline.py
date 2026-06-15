#!/usr/bin/env python3
"""
debug_pipeline.py — Debug script for voice avatar pipeline.
Simulates the frontend: sends audio → /ws/stt → prints pipeline steps → saves TTS as .wav

Usage:
  python3 debug_pipeline.py [--server ws://localhost:8765] [--audio path/to/file.wav] [--output tts_output.wav]
"""

import argparse
import asyncio
import json
import logging
import sys
import time
from pathlib import Path

import numpy as np

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("debug")

STT_TARGET_SR = 16000  # Whisper input rate
TTS_SAMPLE_RATE = 24000  # OmniVoice output rate


# ── Audio I/O ──────────────────────────────────────────────────────────

def load_audio(path: str):
    import soundfile as sf
    data, sr = sf.read(path)
    if data.ndim > 1:
        data = data.mean(axis=1)
    return data.astype(np.float32), sr


def resample(data: np.ndarray, orig_sr: int, target_sr: int):
    from scipy import signal
    if orig_sr == target_sr:
        return data
    duration = len(data) / orig_sr
    num_out = int(round(duration * target_sr))
    return signal.resample(data, num_out).astype(np.float32)


def save_wav(path: str, audio: np.ndarray, sr: int):
    import soundfile as sf
    sf.write(path, audio, sr)
    log.info("  ✅ WAV saved: %s (%.2fs @ %dHz)", path, len(audio) / sr, sr)


# ── Helpers ────────────────────────────────────────────────────────────

def _fmt_time(sec: float) -> str:
    if sec < 60:
        return f"{sec:.1f}s"
    return f"{int(sec//60)}m{int(sec%60)}s"


async def _recv_json(ws) -> dict:
    raw = await ws.recv()
    if isinstance(raw, bytes):
        return None  # not JSON
    return json.loads(raw)


async def _drain_until(ws, status_target: str):
    """Receive messages until a status with target value is seen."""
    while True:
        raw = await ws.recv()
        if isinstance(raw, bytes):
            continue
        msg = json.loads(raw)
        if msg.get("type") == "status" and msg.get("data") == status_target:
            return
        if msg.get("type") == "error":
            log.error("  [←] ERROR: %s", msg.get("message", ""))


# ── Step 1: STT ────────────────────────────────────────────────────────

async def step_stt(ws_url: str, audio_path: str):
    uri = f"{ws_url}/ws/stt"
    log.info("\n" + "=" * 60)
    log.info("STEP 1: STT (Speech-to-Text)")
    log.info("=" * 60)

    # Load & prepare audio
    data, orig_sr = load_audio(audio_path)
    log.info("  Audio:  %s", Path(audio_path).name)
    log.info("  Source: %.2fs @ %dHz %s", len(data) / orig_sr, orig_sr,
             "mono" if data.ndim == 1 or data.shape[1] == 1 else "stereo→mono")

    audio_16k = resample(data, orig_sr, STT_TARGET_SR)
    int16 = (np.clip(audio_16k, -1, 1) * 32767).astype(np.int16)
    log.info("  → Resampled to %dHz: %.2fs (%d samples)", STT_TARGET_SR,
             len(int16) / STT_TARGET_SR, len(int16))

    import websockets
    async with websockets.connect(uri, ping_interval=None) as ws:
        ws: websockets.WebSocketClientProtocol

        log.info("  Connected to /ws/stt")

        # 1. Config
        t0 = time.perf_counter()
        await ws.send(json.dumps({"type": "config"}))
        log.info("  [→] {type: config}")

        # 2. Stream audio in ~0.5s chunks
        chunk_size = int(STT_TARGET_SR * 0.5)  # 8000 samples per chunk
        total_sent = 0
        total_bytes = len(int16) * 2
        for i in range(0, len(int16), chunk_size):
            chunk = int16[i:i + chunk_size].tobytes()
            await ws.send(chunk)
            total_sent += len(chunk)
            pct = total_sent * 100 / total_bytes
            log.info("  [→] Audio %.1f%% (%d/%d bytes)", pct, total_sent, total_bytes)

        log.info("  [→] Audio streaming complete (%.2fs in %d chunks)",
                 len(int16) / STT_TARGET_SR, (len(int16) + chunk_size - 1) // chunk_size)

        # 3. Stop
        await ws.send(json.dumps({"type": "stop"}))
        log.info("  [→] {type: stop} — waiting for server...")

        # 4. Receive responses
        transcript = ""
        reply = {}

        while True:
            raw = await ws.recv()

            if isinstance(raw, bytes):
                continue

            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue

            t = msg.get("type")

            if t == "transcript":
                transcript = msg.get("text", "")
                lang = msg.get("language", "?")
                log.info("  [←] transcript: %s", transcript)

            elif t == "reply":
                reply = msg
                log.info("  [←] reply:")
                log.info("         native_text:    %s", msg.get("native_text", ""))
                log.info("         romanized_text: %s", msg.get("romanized_text", ""))
                log.info("         emotion:        %s", msg.get("emotion", ""))
                log.info("         intent:         %s", msg.get("intent", ""))

            elif t == "status":
                status = msg.get("data", "")
                dt = time.perf_counter() - t0
                log.info("  [←] status: %s  (+%s)", status, _fmt_time(dt))
                if status in ("stopped", "cancelled"):
                    break

            elif t == "error":
                log.error("  [←] ERROR: %s", msg.get("message", ""))
                return None

    log.info("  ── STT complete in %s ──", _fmt_time(time.perf_counter() - t0))

    if not reply:
        log.error("  ❌ No reply received!")
        return None

    return reply


# ── Step 2: TTS ────────────────────────────────────────────────────────

async def step_tts(ws_url: str, text: str, romanized: str, output_path: str):
    uri = f"{ws_url}/ws/tts"
    log.info("\n" + "=" * 60)
    log.info("STEP 2: TTS (Text-to-Speech)")
    log.info("=" * 60)
    log.info("  Text:      %s", text[:80])
    log.info("  Romanized: %s", romanized[:80])
    log.info("  Output:    %s", output_path)

    import websockets
    async with websockets.connect(uri, ping_interval=None) as ws:
        ws: websockets.WebSocketClientProtocol

        log.info("  Connected to /ws/tts")

        t0 = time.perf_counter()
        await ws.send(json.dumps({
            "type": "speak",
            "text": text,
            "romanized_text": romanized,
            "speed": 1.0,
            "numStep": 16,
        }))
        log.info("  [→] {type: speak}")

        audio_buffers = []
        sample_rate = TTS_SAMPLE_RATE
        chunk_count = 0
        total_audio_samples = 0

        while True:
            raw = await ws.recv()

            if isinstance(raw, bytes):
                pcm = np.frombuffer(raw, dtype=np.float32)
                audio_buffers.append(pcm)
                total_audio_samples += len(pcm)
                chunk_count += 1
                dur = len(pcm) / sample_rate
                log.info("  [←] Audio chunk #%d: %d samples (%.2fs)", chunk_count, len(pcm), dur)
                continue

            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue

            t = msg.get("type")

            if t == "chunk":
                sample_rate = msg.get("sampleRate", TTS_SAMPLE_RATE)
                rom = msg.get("romanized_text", "")
                log.info("  [←] chunk header: text=%s, rom=%s", msg.get("text", ""), rom)

            elif t == "status":
                status = msg.get("data", "")
                dt = time.perf_counter() - t0
                log.info("  [←] status: %s  (+%s)", status, _fmt_time(dt))
                if status in ("complete", "stopped"):
                    break

            elif t == "error":
                log.error("  [←] ERROR: %s", msg.get("message", ""))
                return None

    log.info("  ── TTS complete in %s ──", _fmt_time(time.perf_counter() - t0))
    log.info("  Received %d chunks, %.2fs audio", chunk_count, total_audio_samples / sample_rate)

    if audio_buffers:
        full = np.concatenate(audio_buffers)
        save_wav(output_path, full, sample_rate)
    else:
        log.error("  ❌ No audio received!")
        return False

    return True


# ── Main ───────────────────────────────────────────────────────────────

async def main():
    parser = argparse.ArgumentParser(description="Debug pipeline: STT → LLM → TTS → WAV")
    parser.add_argument("--server", default="ws://localhost:8765",
                        help="Server WebSocket URL (default: ws://localhost:8765)")
    parser.add_argument("--audio",
                        default="/Users/kabil/Desktop/Projects/Simplyfi/JUN-JUL/voice_avatar/testing/voice_server/server/State Bank of India.wav",
                        help="Input WAV file path")
    parser.add_argument("--output", default="tts_output.wav",
                        help="Output WAV file path (default: tts_output.wav)")
    args = parser.parse_args()

    audio_path = args.audio
    if not Path(audio_path).exists():
        log.error("Audio file not found: %s", audio_path)
        sys.exit(1)

    t_start = time.perf_counter()

    # ── Step 1: STT ──
    reply = await step_stt(args.server, audio_path)
    if not reply:
        log.error("STT step failed — aborting")
        sys.exit(1)

    native = reply.get("native_text", "")
    romanized = reply.get("romanized_text", "")
    emotion = reply.get("emotion", "neutral")
    intent = reply.get("intent", "unknown")

    if not native:
        log.error("Empty native_text in reply — aborting")
        sys.exit(1)

    # ── Step 2: TTS ──
    ok = await step_tts(args.server, native, romanized, args.output)
    if not ok:
        log.error("TTS step failed — aborting")
        sys.exit(1)

    t_total = time.perf_counter() - t_start
    log.info("\n" + "=" * 60)
    log.info("✅ PIPELINE COMPLETE")
    log.info("   Total time:  %s", _fmt_time(t_total))
    log.info("   Intent:      %s", intent)
    log.info("   Emotion:     %s", emotion)
    log.info("   Native:      %s", native[:80])
    log.info("   Romanized:   %s", romanized[:80])
    log.info("   TTS output:  %s", args.output)
    log.info("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
