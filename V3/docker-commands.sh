#!/usr/bin/env bash
# =============================================================================
# docker-commands.sh — Build, run, and test the Voice Avatar stack
#
# Usage:
#   chmod +x docker-commands.sh
#   ./docker-commands.sh <command>
#
# Commands:
#   build-gpu   Build GPU production images (CUDA 12.4)
#   build-cpu   Build CPU dev images (no GPU required)
#   up-gpu      Start GPU stack
#   up-cpu      Start CPU stack
#   down        Stop and remove all containers
#   logs        Follow server logs
#   health      Check /health endpoint
#   shell       Open a shell inside the running server container
#   test        Run end-to-end pipeline test against a running server
# =============================================================================

set -euo pipefail
SERVER_URL="${SERVER_URL:-http://localhost:8765}"

# ─────────────────────────────────────────────────────────────────────────────
build-gpu() {
  echo "=== Building GPU server image ==="
  docker compose -f docker-compose.yml -f docker-compose.gpu.yml build server
  echo "=== Building frontend image ==="
  docker compose -f docker-compose.yml build frontend
  echo "=== Done ==="
}

build-cpu() {
  echo "=== Building CPU images ==="
  docker compose -f docker-compose.yml build
  echo "=== Done ==="
}

up-gpu() {
  echo "=== Starting GPU stack ==="
  docker compose -f docker-compose.yml -f docker-compose.gpu.yml up -d
  echo ""
  echo "  Server:   $SERVER_URL/health"
  echo "  Frontend: http://localhost:3005"
  echo ""
  echo "  Follow logs:  ./docker-commands.sh logs"
  echo "  Run tests:    ./docker-commands.sh test"
}

up-cpu() {
  echo "=== Starting CPU stack ==="
  docker compose -f docker-compose.yml up -d
  echo ""
  echo "  Server:   $SERVER_URL/health"
  echo "  Frontend: http://localhost:3005"
}

down() {
  echo "=== Stopping all containers ==="
  docker compose -f docker-compose.yml -f docker-compose.gpu.yml down 2>/dev/null || \
  docker compose -f docker-compose.yml down
}

logs() {
  docker logs -f avatar-server
}

health() {
  echo "=== Health check ==="
  curl -sf "$SERVER_URL/health" | python3 -m json.tool
}

shell() {
  docker exec -it avatar-server bash
}

# ─────────────────────────────────────────────────────────────────────────────
test() {
  echo "=== End-to-end pipeline test: $SERVER_URL ==="

  echo ""
  echo "--- 1. Health ---"
  HEALTH=$(curl -sf "$SERVER_URL/health")
  echo "$HEALTH" | python3 -m json.tool
  STATUS=$(echo "$HEALTH" | python3 -c "import sys,json; print(json.load(sys.stdin)['status'])")
  [ "$STATUS" = "ok" ] && echo "PASS" || { echo "FAIL: status=$STATUS"; exit 1; }

  echo ""
  echo "--- 2. POST /chat (text → LLM → reply) ---"
  CHAT=$(curl -sf -X POST "$SERVER_URL/chat" \
    -H "Content-Type: application/json" \
    -d '{"text":"Hello there!","lang":"en"}')
  echo "$CHAT" | python3 -m json.tool | head -8
  REPLY=$(echo "$CHAT" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('reply','MISSING')[:60])")
  [ "$REPLY" != "MISSING" ] && echo "PASS: reply=$REPLY" || echo "FAIL"

  echo ""
  echo "--- 3. POST /api/v1/chat (WAV → full pipeline) ---"
  # Generate a 1.5s sine-wave WAV as test audio
  python3 - <<'PYEOF'
import struct, numpy as np, sys, io

def write_wav(samples, sr=16000):
    n   = len(samples)
    buf = io.BytesIO()
    buf.write(b'RIFF'); buf.write(struct.pack('<I', 36 + n*2)); buf.write(b'WAVE')
    buf.write(b'fmt '); buf.write(struct.pack('<IHHIIHH', 16, 1, 1, sr, sr*2, 2, 16))
    buf.write(b'data'); buf.write(struct.pack('<I', n*2))
    buf.write((np.clip(samples, -1, 1) * 32767).astype(np.int16).tobytes())
    return buf.getvalue()

t   = np.linspace(0, 1.5, 16000*1, endpoint=False)
sig = (0.3*np.sin(2*np.pi*200*t) + 0.2*np.sin(2*np.pi*400*t)).astype(np.float32)
with open('/tmp/test_audio.wav', 'wb') as f:
    f.write(write_wav(sig))
print("Generated /tmp/test_audio.wav")
PYEOF

  RESPONSE=$(curl -sf -X POST "$SERVER_URL/api/v1/chat" \
    -F "file=@/tmp/test_audio.wav;type=audio/wav")
  AUDIO_URL=$(echo "$RESPONSE" | python3 -c "import sys,json; print(json.load(sys.stdin).get('audio_url','MISSING'))")
  FRAMES=$(echo "$RESPONSE" | python3 -c "import sys,json; print(len(json.load(sys.stdin).get('animation_matrix',[])))")
  EMOTION=$(echo "$RESPONSE" | python3 -c "import sys,json; print(json.load(sys.stdin).get('emotion','MISSING'))")
  echo "  audio_url:        $AUDIO_URL"
  echo "  animation_frames: $FRAMES"
  echo "  emotion:          $EMOTION"
  [ "$AUDIO_URL" != "MISSING" ] && echo "PASS" || echo "FAIL: no audio_url"
  [ "$FRAMES" -gt 0 ]           && echo "PASS: $FRAMES animation frames" || echo "WARN: 0 frames"

  echo ""
  echo "--- 4. GET audio ---"
  BYTES=$(curl -sf -o /dev/null -w "%{size_download}" "$AUDIO_URL")
  [ "$BYTES" -gt 1000 ] && echo "PASS: $BYTES bytes" || echo "FAIL: audio too small"

  echo ""
  echo "=== All tests complete ==="
}

# ─────────────────────────────────────────────────────────────────────────────
CMD="${1:-help}"
case "$CMD" in
  build-gpu)  build-gpu ;;
  build-cpu)  build-cpu ;;
  up-gpu)     up-gpu    ;;
  up-cpu)     up-cpu    ;;
  down)       down      ;;
  logs)       logs      ;;
  health)     health    ;;
  shell)      shell     ;;
  test)       test      ;;
  *)
    echo "Usage: $0 {build-gpu|build-cpu|up-gpu|up-cpu|down|logs|health|shell|test}"
    echo ""
    echo "  GPU production:  ./docker-commands.sh build-gpu && ./docker-commands.sh up-gpu"
    echo "  CPU local dev:   ./docker-commands.sh build-cpu && ./docker-commands.sh up-cpu"
    echo "  Test running:    ./docker-commands.sh test"
    ;;
esac
