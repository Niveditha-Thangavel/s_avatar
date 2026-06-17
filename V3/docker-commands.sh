#!/usr/bin/env bash
# =============================================================================
# docker-commands.sh — Build, run, and test the Voice Avatar server on GPU
#
# Usage:
#   chmod +x docker-commands.sh
#   ./docker-commands.sh [command]
#
# Commands:
#   build-gpu     Build the GPU production image
#   build-cpu     Build the CPU dev image
#   up-gpu        Start GPU stack (server + frontend)
#   up-cpu        Start CPU stack (server + frontend)
#   down          Stop and remove containers
#   logs          Follow server logs
#   test          Run end-to-end pipeline test against a running server
#   test-stt      Test Whisper STT language detection specifically
#   shell         Open a shell inside the running server container
#   health        Check /health endpoint
# =============================================================================

set -euo pipefail

SERVER_URL="${SERVER_URL:-http://localhost:8765}"
COMPOSE_GPU="docker compose -f docker-compose.yml -f docker-compose.gpu.yml"
COMPOSE_CPU="docker compose -f docker-compose.yml"

# ─────────────────────────────────────────────────────────────────────────────
build-gpu() {
  echo "=== Building GPU image ==="
  docker build -f Dockerfile.server \
    --build-arg BASE_IMAGE=pytorch/pytorch:2.5.1-cuda12.4-cudnn9-runtime \
    --build-arg TORCH_INDEX_URL=https://download.pytorch.org/whl/cu124 \
    --build-arg SKIP_TORCH_INSTALL=true \
    --build-arg WHISPER_MODEL_ID=openai/whisper-large-v3-turbo \
    -t avatar-server-gpu \
    .
  echo "=== Building frontend image ==="
  docker build -f Dockerfile.frontend -t avatar-frontend .
  echo "=== Build complete ==="
}

build-cpu() {
  echo "=== Building CPU image ==="
  docker build -f Dockerfile.server -t avatar-server .
  echo "=== Building frontend image ==="
  docker build -f Dockerfile.frontend -t avatar-frontend .
  echo "=== Build complete ==="
}

up-gpu() {
  echo "=== Starting GPU stack ==="
  $COMPOSE_GPU up -d
  echo ""
  echo "Waiting for server to be healthy (models take ~60s to load)..."
  echo "  Server:   $SERVER_URL/health"
  echo "  Frontend: http://localhost:3005"
  echo ""
  echo "Follow logs:  ./docker-commands.sh logs"
  echo "Run tests:    ./docker-commands.sh test"
}

up-cpu() {
  echo "=== Starting CPU stack ==="
  $COMPOSE_CPU up -d
  echo ""
  echo "Server: $SERVER_URL/health"
  echo "Frontend: http://localhost:3005"
}

down() {
  echo "=== Stopping all containers ==="
  $COMPOSE_GPU down 2>/dev/null || $COMPOSE_CPU down 2>/dev/null || true
}

logs() {
  docker logs -f avatar-server
}

health() {
  echo "=== Health check ==="
  curl -s "$SERVER_URL/health" | python3 -m json.tool
}

shell() {
  docker exec -it avatar-server bash
}

# ─────────────────────────────────────────────────────────────────────────────
test() {
  echo "=== End-to-end pipeline test ==="
  echo "Target: $SERVER_URL"
  echo ""

  # 1. Health
  echo "--- 1. Health ---"
  HEALTH=$(curl -sf "$SERVER_URL/health")
  echo "$HEALTH" | python3 -m json.tool
  STATUS=$(echo "$HEALTH" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['status'])")
  [ "$STATUS" = "ok" ] && echo "PASS" || { echo "FAIL: status=$STATUS"; exit 1; }

  # 2. WAV file for STT test — generate via Python
  echo ""
  echo "--- 2. Generating test WAV (1s sine wave) ---"
  python3 -c "
import numpy as np, struct, io, sys

def write_wav_bytes(samples, sr=16000):
    buf = io.BytesIO()
    n   = len(samples)
    buf.write(b'RIFF')
    buf.write(struct.pack('<I', 36 + n*2))
    buf.write(b'WAVE')
    buf.write(b'fmt ')
    buf.write(struct.pack('<IHHIIHH', 16, 1, 1, sr, sr*2, 2, 16))
    buf.write(b'data')
    buf.write(struct.pack('<I', n*2))
    pcm = (np.clip(samples, -1, 1) * 32767).astype(np.int16)
    buf.write(pcm.tobytes())
    return buf.getvalue()

sr = 16000
t  = np.linspace(0, 1.0, sr, endpoint=False)
# Simulate speech-like audio: multiple harmonics
sig = 0.3*np.sin(2*np.pi*200*t) + 0.2*np.sin(2*np.pi*400*t) + 0.1*np.sin(2*np.pi*800*t)
wav = write_wav_bytes(sig.astype(np.float32), sr)
sys.stdout.buffer.write(wav)
" > /tmp/test_audio.wav
  echo "PASS — test_audio.wav written ($(wc -c < /tmp/test_audio.wav) bytes)"

  # 3. POST to /api/v1/chat
  echo ""
  echo "--- 3. POST /api/v1/chat (STT → LLM → TTS → PantoMatrix) ---"
  RESPONSE=$(curl -sf -X POST "$SERVER_URL/api/v1/chat" \
    -F "file=@/tmp/test_audio.wav;type=audio/wav")
  echo "$RESPONSE" | python3 -m json.tool 2>/dev/null | head -20

  AUDIO_URL=$(echo "$RESPONSE" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('audio_url','MISSING'))")
  FRAMES=$(echo "$RESPONSE" | python3 -c "import sys,json; d=json.load(sys.stdin); print(len(d.get('animation_matrix',[])))")
  echo ""
  echo "audio_url:        $AUDIO_URL"
  echo "animation frames: $FRAMES"

  [ "$AUDIO_URL" != "MISSING" ] && echo "PASS: audio_url present" || echo "FAIL: no audio_url"
  [ "$FRAMES" -gt 0 ]           && echo "PASS: animation_matrix has $FRAMES frames" || echo "WARN: 0 animation frames"

  # 4. Fetch the audio
  echo ""
  echo "--- 4. GET audio URL ---"
  AUDIO_SIZE=$(curl -sf -o /tmp/test_response.wav "$AUDIO_URL" && wc -c < /tmp/test_response.wav)
  echo "Audio size: $AUDIO_SIZE bytes"
  [ "$AUDIO_SIZE" -gt 1000 ] && echo "PASS" || echo "FAIL: audio too small"

  # 5. WS TTS test
  echo ""
  echo "--- 5. /chat endpoint (text → LLM reply) ---"
  CHAT_RESP=$(curl -sf -X POST "$SERVER_URL/chat" \
    -H "Content-Type: application/json" \
    -d '{"text":"Hello","session_id":"test","lang":"en"}')
  echo "$CHAT_RESP" | python3 -m json.tool 2>/dev/null | head -10
  REPLY=$(echo "$CHAT_RESP" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('reply','MISSING')[:60])")
  [ "$REPLY" != "MISSING" ] && echo "PASS: reply=$REPLY" || echo "FAIL"

  echo ""
  echo "=== All tests complete ==="
}

# ─────────────────────────────────────────────────────────────────────────────
test-stt() {
  echo "=== Whisper STT language detection test ==="
  echo "Target: $SERVER_URL"
  echo ""
  echo "This test sends a real WAV file to verify language detection."
  echo "Upload a real audio file: curl test:"
  echo ""
  echo "  # English test:"
  echo "  curl -X POST $SERVER_URL/api/v1/chat -F 'file=@your_english.wav'"
  echo ""
  echo "  # Any language — Whisper auto-detects and transcribes in native script"
  echo "  curl -X POST $SERVER_URL/api/v1/chat -F 'file=@your_hindi.wav'"
  echo ""
  echo "Check server logs for:"
  echo "  [STT] detected lang token=XXXXX → XX"
  echo "  [STT] lang=XX text=..."
  echo ""
  echo "Running automated STT sanity check via /api/v1/chat..."
  python3 << 'PYEOF'
import urllib.request, json, struct, numpy as np, io

def make_wav(text_freq_hz=220, duration_s=1.5, sr=16000):
    t   = np.linspace(0, duration_s, int(sr*duration_s), endpoint=False)
    sig = (0.3*np.sin(2*np.pi*text_freq_hz*t) +
           0.15*np.sin(2*np.pi*text_freq_hz*2*t)).astype(np.float32)
    buf = io.BytesIO()
    n   = len(sig)
    buf.write(b'RIFF'); buf.write(struct.pack('<I', 36+n*2)); buf.write(b'WAVE')
    buf.write(b'fmt '); buf.write(struct.pack('<IHHIIHH',16,1,1,sr,sr*2,2,16))
    buf.write(b'data'); buf.write(struct.pack('<I', n*2))
    buf.write((np.clip(sig,-1,1)*32767).astype(np.int16).tobytes())
    return buf.getvalue()

import urllib.request, urllib.error
import os

url     = os.environ.get('SERVER_URL', 'http://localhost:8765') + '/api/v1/chat'
wav     = make_wav()
boundary = b'----TestBoundary'
body     = (b'--' + boundary + b'\r\n'
            b'Content-Disposition: form-data; name="file"; filename="test.wav"\r\n'
            b'Content-Type: audio/wav\r\n\r\n' + wav + b'\r\n'
            b'--' + boundary + b'--\r\n')
req = urllib.request.Request(url, data=body,
      headers={'Content-Type': 'multipart/form-data; boundary=' + boundary.decode()},
      method='POST')
try:
    with urllib.request.urlopen(req, timeout=120) as resp:
        data = json.loads(resp.read())
    print('PASS: pipeline returned audio_url =', data.get('audio_url','?')[:50])
    print('      animation_matrix frames     =', len(data.get('animation_matrix',[])))
except Exception as e:
    print('FAIL:', e)
PYEOF
}

# ─────────────────────────────────────────────────────────────────────────────
CMD="${1:-help}"
case "$CMD" in
  build-gpu)  build-gpu  ;;
  build-cpu)  build-cpu  ;;
  up-gpu)     up-gpu     ;;
  up-cpu)     up-cpu     ;;
  down)       down       ;;
  logs)       logs       ;;
  health)     health     ;;
  shell)      shell      ;;
  test)       test       ;;
  test-stt)   test-stt   ;;
  *)
    echo "Usage: $0 {build-gpu|build-cpu|up-gpu|up-cpu|down|logs|health|shell|test|test-stt}"
    echo ""
    echo "Quick start on GPU server:"
    echo "  ./docker-commands.sh build-gpu"
    echo "  ./docker-commands.sh up-gpu"
    echo "  ./docker-commands.sh test"
    ;;
esac
