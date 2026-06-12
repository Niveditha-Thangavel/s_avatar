#!/usr/bin/env bash
# Start the Voice Avatar Python server
# Usage: bash server/start.sh

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# ── Disk space check ──────────────────────────────────────────────────────────
# Models need ~3-5 GB free. Warn and abort if less than 2 GB available.
AVAIL_KB=$(df -k . | awk 'NR==2 {print $4}')
AVAIL_GB=$(echo "scale=1; $AVAIL_KB / 1048576" | bc)
MIN_GB=2

if [ "$AVAIL_KB" -lt $((MIN_GB * 1048576)) ]; then
  echo ""
  echo "ERROR: Not enough disk space."
  echo "  Available : ${AVAIL_GB} GB"
  echo "  Required  : at least ${MIN_GB} GB"
  echo ""
  echo "Free up disk space and try again."
  echo "  Quick wins:"
  echo "    rm -rf ~/Library/Caches                  # Xcode / app caches"
  echo "    brew cleanup                              # Homebrew old versions"
  echo "    pip cache purge                           # pip download cache"
  echo "    du -sh ~/.cache/huggingface               # HuggingFace model cache"
  echo ""
  exit 1
fi

echo "Disk space OK: ${AVAIL_GB} GB available"

# ── Device auto-detection ───
# Let the server auto-detect MPS on Mac or CUDA on GPU machines
# export OMNIVOICE_DEVICE=cpu
# export WHISPER_DEVICE=cpu

# Suppress tokenizer parallelism warnings
export TOKENIZERS_PARALLELISM=false

# ── Virtual environment ────────────────────────────────────────────────────────
if [ ! -d "venv" ]; then
  echo "Creating Python virtual environment..."
  python3 -m venv venv
fi

source venv/bin/activate

echo "Installing / upgrading dependencies..."
pip install --upgrade pip -q
pip install -r requirements.txt -q

echo ""
echo "Starting server on ws://0.0.0.0:8765 ..."
echo "(Models will be downloaded on first run — this takes a few minutes)"
echo ""

uvicorn main:app --host 0.0.0.0 --port 8765 --log-level info
