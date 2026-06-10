"""
test_pipeline.py
================
End-to-end CLI test for the voice avatar server.

  WAV file  →  /ws/stt (accumulate → Whisper on stop)
            →  transcript → chat.py → reply text
            →  /ws/tts (OmniVoice)
            →  reply.wav

Usage
-----
    # Full pipeline: WAV → STT → chat → TTS → reply.wav
    python test_pipeline.py my_question.wav

    # Skip STT — send text directly to TTS
    python test_pipeline.py --text "Hello, how are you?" --out reply.wav

    # Custom host / port
    python test_pipeline.py my_question.wav --host localhost --port 8765

    # Higher quality synthesis
    python test_pipeline.py my_question.wav --steps 32 --speed 0.9
"""

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

import numpy as np
import soundfile as sf
import websockets

DEFAULT_HOST = "localhost"
DEFAULT_PORT = 8765
CHUNK_MS     = 100       # ms per streaming frame
STT_SR       = 16_000    # server expects 16 kHz mono int16


# ── Audio helpers ─────────────────────────────────────────────────────────────

def load_as_16k_mono_int16(path: str) -> np.ndarray:
    """Load any audio file and convert to 16 kHz mono int16."""
    audio, sr = sf.read(path, dtype="float32", always_2d=True)
    if audio.shape[1] > 1:
        audio = audio.mean(axis=1)
    else:
        audio = audio[:, 0]

    if sr != STT_SR:
        ratio   = STT_SR / sr
        new_len = int(len(audio) * ratio)
        audio   = np.interp(
            np.linspace(0, len(audio) - 1, new_len),
            np.arange(len(audio)),
            audio,
        ).astype(np.float32)

    audio = np.clip(audio, -1.0, 1.0)
    return (audio * 32767).astype(np.int16)


def save_wav(path: str, pcm_chunks: list[bytes], sample_rate: int = 24_000):
    """Concatenate float32-le PCM byte chunks and save as WAV."""
    audio = np.frombuffer(b"".join(pcm_chunks), dtype=np.float32)
    sf.write(path, audio, sample_rate, subtype="FLOAT")
    print(f"  Saved {len(audio)} samples ({len(audio)/sample_rate:.2f}s) → {path}")


# ── STT ───────────────────────────────────────────────────────────────────────

async def run_stt(host: str, port: int, audio_int16: np.ndarray) -> str:
    """
    Stream all audio frames to /ws/stt, then send stop.
    Server accumulates the full buffer and runs Whisper once on stop.
    Returns the reply text (what the avatar will say).
    """
    uri = f"ws://{host}:{port}/ws/stt"
    print(f"\n[STT] Connecting to {uri} ...")

    async with websockets.connect(uri) as ws:
        # Send language config
        await ws.send(json.dumps({"type": "config", "language": None}))

        # Drain the "listening" ack
        ack = json.loads(await asyncio.wait_for(ws.recv(), timeout=5.0))
        print(f"  [STT status] {ack.get('data', ack)}")

        # Stream all audio in chunks
        chunk_samples = int(STT_SR * CHUNK_MS / 1000)
        total_chunks  = (len(audio_int16) + chunk_samples - 1) // chunk_samples
        print(f"[STT] Streaming {len(audio_int16)} samples "
              f"({len(audio_int16)/STT_SR:.2f}s) in {total_chunks} chunks ...")

        t0 = time.perf_counter()
        for i in range(0, len(audio_int16), chunk_samples):
            await ws.send(audio_int16[i : i + chunk_samples].tobytes())
            await asyncio.sleep(CHUNK_MS / 1000 * 0.5)

        # Stop → server flushes entire buffer to Whisper
        print("[STT] All audio sent → sending stop → Whisper runs now ...")
        await ws.send(json.dumps({"type": "stop"}))

        transcript = ""
        reply      = ""

        try:
            while True:
                raw   = await asyncio.wait_for(ws.recv(), timeout=60.0)
                msg   = json.loads(raw)
                mtype = msg.get("type")

                if mtype == "status":
                    print(f"  [STT status] {msg['data']}")
                    if msg["data"] == "stopped":
                        break

                elif mtype == "transcript":
                    transcript = msg["text"]
                    print(f"  [Transcript] {transcript!r}")

                elif mtype == "reply":
                    reply = msg["text"]
                    print(f"  [Reply]      {reply!r}")

                elif mtype == "error":
                    print(f"  [STT error]  {msg.get('message')}", file=sys.stderr)
                    break

        except asyncio.TimeoutError:
            print("  [STT] Timeout waiting for Whisper response", file=sys.stderr)

        print(f"[STT] Done in {time.perf_counter() - t0:.2f}s")
        return reply or transcript


# ── TTS ───────────────────────────────────────────────────────────────────────

async def run_tts(host: str, port: int, text: str, out_path: str,
                  speed: float = 1.0, num_step: int = 16):
    """Send text to /ws/tts, collect PCM chunks, save to WAV."""
    uri = f"ws://{host}:{port}/ws/tts"
    print(f"\n[TTS] Connecting to {uri} ...")
    print(f"[TTS] Synthesising: {text!r}")

    pcm_chunks:    list[bytes] = []
    sample_rate:   int         = 24_000
    pending_header             = None
    t0 = time.perf_counter()

    async with websockets.connect(uri) as ws:
        await ws.send(json.dumps({
            "type":    "speak",
            "text":    text,
            "speed":   speed,
            "numStep": num_step,
        }))

        async for message in ws:
            if isinstance(message, bytes):
                if pending_header is None:
                    print("  [TTS] Warning: binary without header", file=sys.stderr)
                    continue
                pcm_chunks.append(message)
                secs = len(message) / 4 / pending_header["sampleRate"]
                print(f"  [TTS chunk] {pending_header['text']!r:45s} {secs:.2f}s")
                sample_rate    = pending_header["sampleRate"]
                pending_header = None
                continue

            msg   = json.loads(message)
            mtype = msg.get("type")

            if mtype == "chunk":
                pending_header = msg
            elif mtype == "status":
                print(f"  [TTS status] {msg['data']}")
                if msg["data"] == "complete":
                    break
            elif mtype == "error":
                print(f"  [TTS error]  {msg.get('message')}", file=sys.stderr)
                break

    print(f"[TTS] Done in {time.perf_counter() - t0:.2f}s  ({len(pcm_chunks)} chunks)")

    if pcm_chunks:
        save_wav(out_path, pcm_chunks, sample_rate)
    else:
        print("[TTS] No audio received.", file=sys.stderr)


# ── Main ──────────────────────────────────────────────────────────────────────

async def main():
    p = argparse.ArgumentParser(description="Voice avatar pipeline test")
    p.add_argument("input_wav", nargs="?",
                   help="Input audio file (WAV/MP3/FLAC) to transcribe.")
    p.add_argument("--text",  "-t", default=None,
                   help="Skip STT: send this text directly to TTS.")
    p.add_argument("--out",   "-o", default="reply.wav",
                   help="Output WAV path (default: reply.wav).")
    p.add_argument("--host",  default=DEFAULT_HOST)
    p.add_argument("--port",  default=DEFAULT_PORT, type=int)
    p.add_argument("--speed", default=1.0, type=float,
                   help="TTS speed factor (default: 1.0).")
    p.add_argument("--steps", default=16, type=int,
                   help="OmniVoice diffusion steps: 16=fast, 32=quality (default: 16).")
    args = p.parse_args()

    if not args.input_wav and not args.text:
        p.error("Provide an input WAV file or --text")

    # ── Text-only mode ────────────────────────────────────────────────────────
    if args.text:
        await run_tts(args.host, args.port, args.text, args.out,
                      speed=args.speed, num_step=args.steps)
        return

    # ── Full pipeline ─────────────────────────────────────────────────────────
    if not Path(args.input_wav).exists():
        print(f"Error: file not found: {args.input_wav}", file=sys.stderr)
        sys.exit(1)

    print(f"[Pipeline] Input : {args.input_wav}")
    print(f"[Pipeline] Output: {args.out}")
    print(f"[Pipeline] Server: {args.host}:{args.port}")

    print("\n[Load] Reading audio ...")
    audio_i16 = load_as_16k_mono_int16(args.input_wav)
    print(f"  {len(audio_i16)} samples @ {STT_SR} Hz ({len(audio_i16)/STT_SR:.2f}s)")

    reply = await run_stt(args.host, args.port, audio_i16)

    if not reply:
        print("\n[Pipeline] No reply — cannot synthesise audio.", file=sys.stderr)
        sys.exit(1)

    await run_tts(args.host, args.port, reply, args.out,
                  speed=args.speed, num_step=args.steps)

    print(f"\n[Pipeline] Complete → {args.out}")


if __name__ == "__main__":
    asyncio.run(main())
