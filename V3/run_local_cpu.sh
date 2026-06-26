#!/bin/bash
# run_local_cpu.sh - Setup and start Vexyl STT and the Orchestrator Server concurrently on CPU/macOS

# Exit on error
set -e

# Make sure HF_TOKEN is provided if running for the first time
if [ -z "$HF_TOKEN" ] && [ ! -d "server/vexyl_stt/venv" ]; then
    echo "⚠️  WARNING: HF_TOKEN environment variable is not set."
    echo "If this is the first run, model downloading will fail."
    echo "Please launch using: HF_TOKEN=hf_your_token ./run_local_cpu.sh"
    echo "----------------------------------------"
fi

# Determine script directory
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$SCRIPT_DIR"

echo "⚙️  [1/3] Setting up Vexyl STT environment..."
cd server/vexyl_stt
if [ ! -d "venv" ]; then
    python3 -m venv venv
fi
source venv/bin/activate
echo "Installing PyTorch & Torchaudio for CPU..."
pip install --quiet torch torchaudio --index-url https://download.pytorch.org/whl/cpu
echo "Installing remaining STT dependencies..."
pip install --quiet -r requirements.txt
deactivate
cd ../..

echo "⚙️  [2/3] Setting up Orchestrator Server environment..."
cd server
if [ ! -d "venv" ]; then
    python3 -m venv venv
fi
source venv/bin/activate
echo "Installing Orchestrator dependencies..."
pip install --quiet -r requirements.txt
echo "Downloading translation model weights..."
python download_models.py
deactivate
cd ..

echo "⚙️  [3/3] Starting services concurrently..."

# Trap exit signals to clean up background processes on Ctrl+C
cleanup() {
    echo -e "\nStopping background services..."
    if [ ! -z "$STT_PID" ]; then
        kill "$STT_PID" 2>/dev/null || true
    fi
    if [ ! -z "$SERVER_PID" ]; then
        kill "$SERVER_PID" 2>/dev/null || true
    fi
    exit
}
trap cleanup SIGINT SIGTERM EXIT

# Start Vexyl STT in background
echo "Starting Vexyl STT (port 8080) in background..."
cd server/vexyl_stt
source venv/bin/activate
python vexyl_stt_server.py > vexyl_stt.log 2>&1 &
STT_PID=$!
deactivate
cd ../..

# Wait briefly for STT to bind
sleep 3

# Start Orchestrator Server in background/foreground
echo "Starting Orchestrator Server (port 8765)..."
cd server
source venv/bin/activate
uvicorn main:app --host 0.0.0.0 --port 8765 --loop asyncio &
SERVER_PID=$!
deactivate
cd ..

echo "🚀 S2S Stack started successfully!"
echo "- Vexyl STT logs are being written to: V3/server/vexyl_stt/vexyl_stt.log"
echo "Press Ctrl+C to terminate both servers."

# Wait for both processes
wait "$STT_PID" "$SERVER_PID"
