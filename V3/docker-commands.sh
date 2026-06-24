#!/usr/bin/env bash
# docker-commands.sh — Helper commands for the S2S Voice Avatar full stack
#
# Single-command startup:
#   ./docker-commands.sh up        (1x GPU — default)
#   ./docker-commands.sh up-gpu    (all GPUs)

set -euo pipefail

SERVER_URL="${SERVER_URL:-http://localhost:8765}"

case "${1:-help}" in

  up)
    echo "=== Starting full S2S stack ==="
    echo "Services: vexyl-stt | orchestrator | frontend"
    if command -v nvidia-smi &> /dev/null; then
      echo "GPU detected! Starting with GPU overrides..."
      docker compose -f docker-compose.yml -f docker-compose.gpu.yml up -d
    else
      echo "No GPU detected. Starting in CPU-only fallback mode..."
      docker compose up -d
    fi
    echo ""
    echo "Frontend:     http://localhost:3005"
    echo "Orchestrator: http://localhost:8765/health"
    echo "STT:          ws://localhost:8080"
    echo ""
    echo "Tail logs:    ./docker-commands.sh logs"
    ;;

  up-gpu)
    echo "=== Starting full S2S stack (all GPUs) ==="
    docker compose -f docker-compose.yml -f docker-compose.gpu.yml up -d
    ;;

  build)
    echo "=== Building local images ==="
    echo "(vexyl-stt is built from GitHub source — needs internet on first run)"
    if command -v nvidia-smi &> /dev/null; then
      echo "GPU detected! Building with GPU overrides (including CUDA PyTorch)..."
      docker compose -f docker-compose.yml -f docker-compose.gpu.yml build
    else
      echo "No GPU detected. Building CPU-only images..."
      docker compose build
    fi
    ;;

  down)
    echo "=== Stopping all services ==="
    docker compose down
    ;;

  restart)
    SERVICE="${2:-}"
    if [[ -n "$SERVICE" ]]; then
      echo "=== Restarting $SERVICE ==="
      docker compose restart "$SERVICE"
    else
      echo "=== Restarting all services ==="
      docker compose restart
    fi
    ;;

  logs)
    SERVICE="${2:-}"
    if [[ -n "$SERVICE" ]]; then
      docker compose logs -f --tail=100 "$SERVICE"
    else
      docker compose logs -f --tail=50
    fi
    ;;

  status)
    echo "=== Service status ==="
    docker compose ps
    ;;

  health)
    echo "--- Orchestrator /health ---"
    curl -sf "${SERVER_URL}/health" | python3 -m json.tool
    ;;

  chat)
    TEXT="${2:-नमस्ते! आप कैसे हैं?}"
    echo "--- POST /chat (translation debug) ---"
    curl -sf -X POST "${SERVER_URL}/chat" \
      -H "Content-Type: application/json" \
      -d "{\"text\": \"${TEXT}\", \"lang\": \"hi\"}" | python3 -m json.tool
    ;;

  help|*)
    cat <<EOF
Usage: $0 <command> [args]

Commands:
  up              Start all services (1x GPU)
  up-gpu          Start all services (all GPUs)
  build           Build local Docker images
  down            Stop all services
  restart [svc]   Restart all or a specific service
  logs [svc]      Tail logs (all or specific service)
  status          Show running containers
  health          GET /health from orchestrator
  chat [text]     POST /chat translation debug

Services: vexyl-stt, orchestrator, frontend

First-time setup:
  1. cp .env.example .env
  2. Add HF_TOKEN to .env (needed for model downloads)
  3. ./docker-commands.sh build
  4. ./docker-commands.sh up
EOF
    ;;
esac
