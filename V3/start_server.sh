#!/usr/bin/env zsh
# start_server.sh — Safe server launcher for macOS (MPS) and Linux (CUDA)
#
# macOS segfault fix:
#   OBJC_DISABLE_INITIALIZE_FORK_SAFETY prevents MPS allocator crash when
#   uvicorn's resource tracker semaphore races with PyTorch MPS init.
#
# GPU (CUDA) notes:
#   PYTORCH_MPS_HIGH_WATERMARK_RATIO is macOS-only and ignored on CUDA.
#   CUDA_VISIBLE_DEVICES / OMNIVOICE_DEVICE can be overridden before calling.

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

# ── Detect device ─────────────────────────────────────────────────────────────
DEVICE=$(python -c "
import torch
if torch.cuda.is_available():
    print('cuda:' + torch.cuda.get_device_name(0))
elif torch.backends.mps.is_available():
    print('mps')
else:
    print('cpu')
" 2>/dev/null)
echo "[start_server] Device: $DEVICE"

# ── Environment flags ─────────────────────────────────────────────────────────
export TOKENIZERS_PARALLELISM=false

if [[ "$DEVICE" == mps ]]; then
    # macOS MPS — prevent fork-safety crash
    export OBJC_DISABLE_INITIALIZE_FORK_SAFETY=YES
    # Release MPS memory immediately to avoid double-free on multi-model init
    export PYTORCH_MPS_HIGH_WATERMARK_RATIO=0.0

elif [[ "$DEVICE" == cuda* ]]; then
    # CUDA — performance tuning
    export TORCH_ALLOW_TF32=1
    export CUDNN_BENCHMARK=1
    # Cap single-allocation size to prevent fragmentation
    export PYTORCH_CUDA_ALLOC_CONF=max_split_size_mb:512
fi

echo "[start_server] Python: $(python --version)"
echo "[start_server] Torch:  $(python -c 'import torch; print(torch.__version__)')"
echo ""

# ── Launch uvicorn (single-process, asyncio loop) ─────────────────────────────
# --loop asyncio : no subprocess workers → no MPS/CUDA context inheritance bugs
# --workers 1    : explicit (default), but stated clearly for documentation
exec python -m uvicorn server.main:app \
    --host 0.0.0.0 \
    --port 8765 \
    --loop asyncio \
    --log-level info
