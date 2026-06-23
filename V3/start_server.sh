#!/usr/bin/env zsh
# start_server.sh — S2S Orchestrator launcher
#
# The orchestrator has no PyTorch dependency (IndicTrans2 uses CTranslate2).
# No MPS/CUDA fork-safety hacks needed.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

# ── Activate venv ─────────────────────────────────────────────────────────────
if [[ -f "$SCRIPT_DIR/venv/bin/activate" ]]; then
    source "$SCRIPT_DIR/venv/bin/activate"
else
    echo "ERROR: venv not found at $SCRIPT_DIR/venv" >&2
    exit 1
fi

# ── Kill any stale process on port 8765 ──────────────────────────────────────
echo "[start_server] Clearing port 8765..."
lsof -ti:8765 | xargs kill -9 2>/dev/null || true
sleep 1

echo "[start_server] Python: $(python --version)"
echo "[start_server]"

# ── Launch uvicorn ────────────────────────────────────────────────────────────
exec python -m uvicorn server.main:app \
    --host 0.0.0.0 \
    --port 8765 \
    --loop asyncio \
    --log-level info
