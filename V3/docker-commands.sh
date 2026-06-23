#!/usr/bin/env bash
# docker-commands.sh — Helper commands for the S2S Voice Avatar Docker setup

set -euo pipefail

SERVER_URL="${SERVER_URL:-http://localhost:8765}"

case "${1:-help}" in

  build)
    echo "=== Building Docker images ==="
    docker compose build
    ;;

  up)
    echo "=== Starting all services ==="
    docker compose up -d
    echo "Orchestrator:  http://localhost:8765"
    echo "Frontend:      http://localhost:3005"
    ;;

  up-gpu)
    echo "=== Starting all services (GPU) ==="
    docker compose -f docker-compose.yml -f docker-compose.gpu.yml up -d
    ;;

  down)
    echo "=== Stopping all services ==="
    docker compose down
    ;;

  logs)
    docker compose logs -f --tail=100 "${2:-orchestrator}"
    ;;

  health)
    echo "--- GET /health ---"
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
    echo "Usage: $0 <command>"
    echo ""
    echo "Commands:"
    echo "  build       Build all Docker images"
    echo "  up          Start all services (CPU)"
    echo "  up-gpu      Start all services (GPU override)"
    echo "  down        Stop all services"
    echo "  logs [svc]  Tail logs (default: orchestrator)"
    echo "  health      GET /health"
    echo "  chat [text] POST /chat translation debug"
    ;;
esac
