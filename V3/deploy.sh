#!/bin/bash
# Voice Avatar Deployment Script for GCP
# Run this on your GCP Ubuntu instance with GPU

set -e  # Exit on error

echo "🚀 Voice Avatar Deployment Script"
echo "=================================="
echo ""

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Check if running as root
if [ "$EUID" -eq 0 ]; then 
    echo -e "${RED}❌ Please do not run as root${NC}"
    exit 1
fi

# Check if .env file exists
if [ ! -f .env ]; then
    echo -e "${YELLOW}⚠️  .env file not found. Creating from .env.example...${NC}"
    cp .env.example .env
    echo -e "${YELLOW}📝 Please edit .env file and add your HF_TOKEN${NC}"
    echo -e "${YELLOW}   Then run this script again${NC}"
    exit 1
fi

# Load .env file
export $(cat .env | grep -v '^#' | xargs)

# Check if HF_TOKEN is set
if [ -z "$HF_TOKEN" ] || [ "$HF_TOKEN" = "your_huggingface_token_here" ]; then
    echo -e "${RED}❌ HF_TOKEN not set in .env file${NC}"
    echo -e "${YELLOW}   Get your token from: https://huggingface.co/settings/tokens${NC}"
    echo -e "${YELLOW}   Accept Llama 3.1 license: https://huggingface.co/meta-llama/Llama-3.1-8B${NC}"
    exit 1
fi

echo -e "${GREEN}✅ HF_TOKEN found${NC}"
echo ""

# Check if Docker is installed
if ! command -v docker &> /dev/null; then
    echo -e "${RED}❌ Docker is not installed${NC}"
    echo -e "${YELLOW}   Run: curl -fsSL https://get.docker.com | sh${NC}"
    exit 1
fi

echo -e "${GREEN}✅ Docker is installed${NC}"

# Check if Docker Compose is installed
if ! command -v docker compose &> /dev/null; then
    echo -e "${RED}❌ Docker Compose is not installed${NC}"
    echo -e "${YELLOW}   Run: sudo apt-get install docker-compose-plugin${NC}"
    exit 1
fi

echo -e "${GREEN}✅ Docker Compose is installed${NC}"

# Check if NVIDIA drivers are available
if ! command -v nvidia-smi &> /dev/null; then
    echo -e "${YELLOW}⚠️  nvidia-smi not found - GPU acceleration will not work${NC}"
    echo -e "${YELLOW}   For GPU support, install NVIDIA drivers and restart${NC}"
    GPU_AVAILABLE=false
else
    echo -e "${GREEN}✅ NVIDIA drivers found${NC}"
    GPU_AVAILABLE=true
    nvidia-smi --query-gpu=name,driver_version,memory.total --format=csv,noheader
fi

echo ""
echo "📦 Deployment Mode Selection"
echo "=============================="
echo ""
echo "1) GCP GPU Server (recommended for production)"
echo "2) Local CPU (for testing without GPU)"
echo ""
read -p "Select mode [1/2]: " MODE

if [ "$MODE" = "2" ]; then
    COMPOSE_FILE="docker-compose.local.yml"
    echo -e "${YELLOW}🔧 Using LOCAL CPU mode${NC}"
else
    COMPOSE_FILE="docker-compose.yml"
    if [ "$GPU_AVAILABLE" = true ]; then
        echo -e "${GREEN}🚀 Using GCP GPU mode${NC}"
    else
        echo -e "${RED}❌ GPU mode selected but GPU not available${NC}"
        exit 1
    fi
fi

echo ""
echo "🏗️  Building and Deploying Containers"
echo "======================================"
echo ""
echo -e "${YELLOW}This will take 30-60 minutes on first run (downloading models)${NC}"
echo -e "${YELLOW}Press Ctrl+C within 5 seconds to cancel${NC}"
sleep 5

# Build and start containers
echo ""
echo "🔨 Building containers..."
docker compose -f $COMPOSE_FILE up -d --build

echo ""
echo "⏳ Waiting for services to start..."
sleep 10

# Check if containers are running
if docker ps | grep -q voice-avatar-server; then
    echo -e "${GREEN}✅ Server container is running${NC}"
else
    echo -e "${RED}❌ Server container failed to start${NC}"
    echo "Logs:"
    docker logs voice-avatar-server --tail 50
    exit 1
fi

if docker ps | grep -q voice-avatar-frontend; then
    echo -e "${GREEN}✅ Frontend container is running${NC}"
else
    echo -e "${RED}❌ Frontend container failed to start${NC}"
    echo "Logs:"
    docker logs voice-avatar-frontend --tail 50
    exit 1
fi

# Wait for server health check
echo ""
echo "⏳ Waiting for server to be healthy (this may take 1-2 minutes)..."
MAX_ATTEMPTS=60
ATTEMPT=0

while [ $ATTEMPT -lt $MAX_ATTEMPTS ]; do
    if curl -s http://localhost:8765/health > /dev/null 2>&1; then
        echo -e "${GREEN}✅ Server is healthy!${NC}"
        break
    fi
    ATTEMPT=$((ATTEMPT + 1))
    echo -n "."
    sleep 2
done

if [ $ATTEMPT -eq $MAX_ATTEMPTS ]; then
    echo -e "${RED}❌ Server health check timeout${NC}"
    echo "Check logs: docker logs voice-avatar-server"
    exit 1
fi

# Get server IP
if command -v curl &> /dev/null; then
    EXTERNAL_IP=$(curl -s ifconfig.me || echo "localhost")
else
    EXTERNAL_IP="localhost"
fi

echo ""
echo "✅ Deployment Complete!"
echo "======================"
echo ""
echo -e "${GREEN}🎉 Your Voice Avatar is now running!${NC}"
echo ""
echo "Access URLs:"
echo "  Frontend (UI):  http://$EXTERNAL_IP:3005"
echo "  Backend API:    http://$EXTERNAL_IP:8765"
echo "  API Health:     http://$EXTERNAL_IP:8765/health"
echo "  API Docs:       http://$EXTERNAL_IP:8765/docs"
echo ""
echo "Useful Commands:"
echo "  View logs:      docker compose logs -f"
echo "  Stop services:  docker compose down"
echo "  Restart:        docker compose restart"
echo "  Rebuild:        docker compose up -d --build"
echo ""
echo "📊 Container Status:"
docker compose ps
echo ""

if [ "$GPU_AVAILABLE" = true ]; then
    echo "🎮 GPU Status:"
    docker exec voice-avatar-server nvidia-smi --query-gpu=utilization.gpu,utilization.memory,memory.used,memory.total --format=csv,noheader || echo "GPU info not available yet"
    echo ""
fi

echo -e "${GREEN}🚀 Ready to use!${NC}"
echo "Open http://$EXTERNAL_IP:3005 in your browser"
