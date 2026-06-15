# Complete Deployment Guide - Frontend + Backend

**Project:** 3D Voice Avatar with Ultravox  
**Target:** GCP Ubuntu Server with 80GB GPU  
**Stack:** Frontend (Vite + Three.js) + Backend (FastAPI + Ultravox)

---

## 📋 Quick Start Commands

### **Option 1: Using Docker Compose (Recommended)**

```bash
# 1. Set your Hugging Face token
export HF_TOKEN="your_huggingface_token_here"

# 2. Build and run both containers
docker compose up -d --build

# 3. Check status
docker compose ps
docker compose logs -f

# 4. Stop everything
docker compose down
```

### **Option 2: Manual Docker Commands**

```bash
# 1. Build images
docker build -f Dockerfile.frontend -t voice-avatar-frontend:latest .
docker build -f Dockerfile.server --build-arg HF_TOKEN=$HF_TOKEN -t voice-avatar-server:latest .

# 2. Create network
docker network create voice-avatar-network

# 3. Run server
docker run -d \
  --name voice-avatar-server \
  --gpus all \
  --network voice-avatar-network \
  -p 8765:8765 \
  -e HF_TOKEN=$HF_TOKEN \
  -e OMNIVOICE_DEVICE=cuda:0 \
  -e WHISPER_DEVICE=cuda \
  voice-avatar-server:latest

# 4. Run frontend
docker run -d \
  --name voice-avatar-frontend \
  --network voice-avatar-network \
  -p 3005:3005 \
  voice-avatar-frontend:latest

# 5. Check logs
docker logs -f voice-avatar-server
docker logs -f voice-avatar-frontend
```

---

## 🚀 Complete GCP Deployment Steps

### **Step 1: Prepare GCP Instance**

#### **1.1 Create VM Instance**

```bash
# From your local machine or GCP Console
gcloud compute instances create voice-avatar-server \
  --zone=us-central1-a \
  --machine-type=a2-highgpu-1g \
  --accelerator=type=nvidia-tesla-a100,count=1 \
  --image-family=ubuntu-2204-lts \
  --image-project=ubuntu-os-cloud \
  --boot-disk-size=200GB \
  --boot-disk-type=pd-ssd \
  --maintenance-policy=TERMINATE \
  --metadata=install-nvidia-driver=True
```

#### **1.2 SSH to Instance**

```bash
gcloud compute ssh voice-avatar-server --zone=us-central1-a
```

#### **1.3 Verify GPU**

```bash
# Check NVIDIA driver
nvidia-smi

# Should show:
# - GPU: Tesla A100 80GB
# - Driver version
# - CUDA version 12.4
```

### **Step 2: Install Docker & NVIDIA Container Toolkit**

```bash
# Update system
sudo apt-get update
sudo apt-get upgrade -y

# Install Docker
curl -fsSL https://get.docker.com -o get-docker.sh
sudo sh get-docker.sh
sudo usermod -aG docker $USER

# Install NVIDIA Container Toolkit
distribution=$(. /etc/os-release;echo $ID$VERSION_ID)
curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey | sudo gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
curl -s -L https://nvidia.github.io/libnvidia-container/$distribution/libnvidia-container.list | \
  sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' | \
  sudo tee /etc/apt/sources.list.d/nvidia-container-toolkit.list

sudo apt-get update
sudo apt-get install -y nvidia-container-toolkit
sudo nvidia-ctk runtime configure --runtime=docker
sudo systemctl restart docker

# Install Docker Compose
sudo curl -L "https://github.com/docker/compose/releases/latest/download/docker-compose-$(uname -s)-$(uname -m)" -o /usr/local/bin/docker-compose
sudo chmod +x /usr/local/bin/docker-compose

# Logout and login again for docker group to take effect
exit
```

```bash
# SSH back in
gcloud compute ssh voice-avatar-server --zone=us-central1-a

# Test GPU access in Docker
docker run --rm --gpus all nvidia/cuda:12.4.0-base-ubuntu22.04 nvidia-smi
```

### **Step 3: Upload Project Files**

#### **Option A: From Your Mac via SCP**

```bash
# Run this on your Mac
cd /Users/kabil/Desktop/Projects/Simplyfi/JUN-JUL/voice_avatar/s_avatar

# Upload entire project
gcloud compute scp --recurse v3 voice-avatar-server:~/ --zone=us-central1-a

# This will take a few minutes depending on your internet speed
```

#### **Option B: Clone from Git (if you have a repo)**

```bash
# Run this on GCP instance
cd ~
git clone <your-repo-url>
cd voice_avatar/s_avatar/v3
```

#### **Option C: Manual tar.gz transfer**

```bash
# On your Mac
cd /Users/kabil/Desktop/Projects/Simplyfi/JUN-JUL/voice_avatar/s_avatar
tar -czf v3.tar.gz v3/
gcloud compute scp v3.tar.gz voice-avatar-server:~/ --zone=us-central1-a

# On GCP instance
tar -xzf v3.tar.gz
cd v3
```

### **Step 4: Configure Environment**

```bash
# On GCP instance
cd ~/v3  # or wherever you uploaded the project

# Create .env file
cp .env.example .env

# Edit .env file
nano .env
```

**Update .env with your settings:**

```bash
# For GCP deployment, use the instance's EXTERNAL IP
VITE_SERVER_HOST=YOUR_GCP_EXTERNAL_IP
VITE_SERVER_PORT=8765

# Your Hugging Face token
HF_TOKEN=hf_your_actual_token_here
```

**Get your external IP:**

```bash
# On GCP instance
curl ifconfig.me

# Or from your Mac
gcloud compute instances describe voice-avatar-server \
  --zone=us-central1-a \
  --format='get(networkInterfaces[0].accessConfigs[0].natIP)'
```

### **Step 5: Build and Deploy**

```bash
# Make sure you're in the project directory
cd ~/v3

# Load environment variables
export $(cat .env | grep -v '^#' | xargs)

# Build and start containers (this will take 30-60 minutes first time)
docker compose up -d --build

# The build process will:
# - Download Node.js dependencies (~5 min)
# - Build Vite frontend (~2 min)
# - Download PyTorch base image (~10 min)
# - Install Python dependencies (~5 min)
# - Download OmniVoice model ~2.4GB (~5 min)
# - Download Ultravox model ~16GB (~20-40 min depending on connection)
```

### **Step 6: Monitor Deployment**

```bash
# Watch build progress
docker compose logs -f

# Or watch individual services
docker compose logs -f server     # Backend logs
docker compose logs -f frontend   # Frontend logs

# Check container status
docker compose ps

# Expected output:
# NAME                    STATUS              PORTS
# voice-avatar-frontend   Up X minutes        0.0.0.0:3005->3005/tcp
# voice-avatar-server     Up X minutes        0.0.0.0:8765->8765/tcp
```

**Wait for these log messages:**

```
voice-avatar-server    | [Chat] Loading Ultravox model 'fixie-ai/ultravox-v0_6-llama-3_1-8b' ...
voice-avatar-server    | [Chat] Ultravox pipeline loaded successfully on device: cuda with dtype: torch.float16
voice-avatar-server    | INFO:     Application startup complete.
```

### **Step 7: Configure Firewall**

```bash
# Allow traffic on ports 3005 (frontend) and 8765 (backend)
gcloud compute firewall-rules create voice-avatar-frontend \
  --allow tcp:3005 \
  --source-ranges 0.0.0.0/0 \
  --description "Allow voice avatar frontend access"

gcloud compute firewall-rules create voice-avatar-backend \
  --allow tcp:8765 \
  --source-ranges 0.0.0.0/0 \
  --description "Allow voice avatar backend WebSocket access"
```

### **Step 8: Test Deployment**

```bash
# From GCP instance
curl http://localhost:8765/health
# Expected: {"status":"ok","timestamp":1718370000.0}

curl http://localhost:3005
# Expected: HTML content

# From your Mac (replace with your GCP external IP)
export GCP_IP="YOUR_GCP_EXTERNAL_IP"

curl http://$GCP_IP:8765/health
# Expected: {"status":"ok","timestamp":1718370000.0}

# Open in browser
open http://$GCP_IP:3005
```

---

## 🔍 Verification Checklist

Run these commands to verify everything is working:

```bash
# 1. Check containers are running
docker compose ps
# ✅ Both should show "Up"

# 2. Check GPU access
docker exec voice-avatar-server nvidia-smi
# ✅ Should show A100 GPU

# 3. Check backend health
curl http://localhost:8765/health
# ✅ Should return JSON with "ok"

# 4. Check frontend is serving
curl -I http://localhost:3005
# ✅ Should return 200 OK

# 5. Check model is loaded
docker logs voice-avatar-server 2>&1 | grep "pipeline loaded successfully"
# ✅ Should show success message

# 6. Test text chat
curl -X POST http://localhost:8765/chat \
  -H "Content-Type: application/json" \
  -d '{"text": "Hello"}'
# ✅ Should return reply JSON

# 7. Check GPU memory usage
docker exec voice-avatar-server nvidia-smi
# ✅ GPU memory should show ~25-30GB used
```

---

## 📊 Resource Usage

**Expected resource consumption:**

| Resource | Usage | Notes |
|----------|-------|-------|
| **Disk Space** | ~25GB | Models + Docker images |
| **GPU RAM** | ~25-30GB | OmniVoice + Ultravox + CUDA |
| **System RAM** | ~8-10GB | Container overhead |
| **Network (download)** | ~20GB | First-time model download |
| **Build Time** | 30-60 min | Depends on internet speed |
| **Startup Time** | 30-60 sec | After containers are built |

---

## 🛠️ Management Commands

### **View Logs**

```bash
# All services
docker compose logs -f

# Server only
docker compose logs -f server

# Frontend only
docker compose logs -f frontend

# Last 100 lines
docker compose logs --tail=100

# Since 1 hour ago
docker compose logs --since 1h
```

### **Restart Services**

```bash
# Restart both
docker compose restart

# Restart server only
docker compose restart server

# Restart frontend only
docker compose restart frontend
```

### **Stop Services**

```bash
# Stop but keep containers
docker compose stop

# Stop and remove containers
docker compose down

# Stop and remove containers + volumes
docker compose down -v
```

### **Update/Rebuild**

```bash
# Rebuild after code changes
docker compose up -d --build

# Rebuild specific service
docker compose up -d --build server
docker compose up -d --build frontend

# Force complete rebuild (no cache)
docker compose build --no-cache
docker compose up -d
```

### **Shell Access**

```bash
# Access server container
docker exec -it voice-avatar-server bash

# Access frontend container
docker exec -it voice-avatar-frontend sh

# Run Python in server
docker exec -it voice-avatar-server python3

# Check GPU inside container
docker exec -it voice-avatar-server nvidia-smi
```

---

## 🐛 Troubleshooting

### **Issue: Containers won't start**

```bash
# Check Docker daemon
sudo systemctl status docker

# Check Docker Compose version
docker compose version

# Check logs for errors
docker compose logs

# Remove and rebuild
docker compose down
docker compose up -d --build
```

### **Issue: GPU not detected**

```bash
# Check host GPU
nvidia-smi

# Check NVIDIA Docker runtime
docker run --rm --gpus all nvidia/cuda:12.4.0-base-ubuntu22.04 nvidia-smi

# Check Docker daemon config
cat /etc/docker/daemon.json

# Should contain:
{
    "runtimes": {
        "nvidia": {
            "path": "nvidia-container-runtime",
            "runtimeArgs": []
        }
    }
}

# Restart Docker
sudo systemctl restart docker
```

### **Issue: Out of Memory**

```bash
# Check GPU memory
docker exec voice-avatar-server nvidia-smi

# Reduce workers in docker-compose.yml:
# In server CMD: --workers 1 (should already be 1)

# Clear GPU cache
docker exec voice-avatar-server python3 -c "
import torch
import gc
torch.cuda.empty_cache()
gc.collect()
"

# Restart server
docker compose restart server
```

### **Issue: Model download fails**

```bash
# Check HF token
docker exec voice-avatar-server printenv | grep HF_TOKEN

# Manually test model download
docker exec -it voice-avatar-server python3 -c "
import transformers
pipe = transformers.pipeline(
    model='fixie-ai/ultravox-v0_6-llama-3_1-8b',
    trust_remote_code=True,
    device='cuda'
)
print('Success!')
"

# If token issue, rebuild with correct token:
export HF_TOKEN="correct_token_here"
docker compose up -d --build server
```

### **Issue: Frontend can't connect to backend**

```bash
# Check backend is accessible
curl http://localhost:8765/health

# Check frontend environment
# Make sure .env has correct VITE_SERVER_HOST

# Rebuild frontend
docker compose up -d --build frontend

# Check browser console for CORS errors
# Open http://YOUR_IP:3005 and press F12
```

### **Issue: Slow inference**

```bash
# Check GPU utilization
watch -n 1 'docker exec voice-avatar-server nvidia-smi'
# Should show >80% GPU util during inference

# Check dtype
docker logs voice-avatar-server 2>&1 | grep dtype
# Should show: torch.float16

# Check if model is on GPU
docker exec voice-avatar-server python3 -c "
import torch
print('CUDA available:', torch.cuda.is_available())
print('CUDA device:', torch.cuda.get_device_name(0))
"
```

---

## 🔒 Security Recommendations

Before production deployment:

```bash
# 1. Restrict firewall to specific IPs
gcloud compute firewall-rules update voice-avatar-frontend \
  --source-ranges YOUR_OFFICE_IP/32

# 2. Set up HTTPS (not covered here, use nginx reverse proxy or Cloud Load Balancer)

# 3. Add authentication (implement in FastAPI)

# 4. Enable audit logging
gcloud logging read "resource.type=gce_instance AND resource.labels.instance_id=YOUR_INSTANCE_ID" --limit 50

# 5. Set up monitoring/alerting
# Use GCP Cloud Monitoring or Prometheus
```

---

## 📈 Monitoring & Metrics

### **View GPU metrics**

```bash
# Real-time GPU monitoring
watch -n 1 'docker exec voice-avatar-server nvidia-smi'

# Log GPU usage every 5 seconds
while true; do
  docker exec voice-avatar-server nvidia-smi --query-gpu=timestamp,utilization.gpu,utilization.memory,memory.used,memory.total --format=csv >> gpu_metrics.log
  sleep 5
done
```

### **View container metrics**

```bash
# Container stats
docker stats

# Specific container
docker stats voice-avatar-server voice-avatar-frontend
```

---

## 🎯 Access Your Application

**Once deployed, access your app at:**

- **Frontend (UI):** `http://YOUR_GCP_IP:3005`
- **Backend API:** `http://YOUR_GCP_IP:8765`
- **API Health:** `http://YOUR_GCP_IP:8765/health`
- **API Docs:** `http://YOUR_GCP_IP:8765/docs` (FastAPI auto-generated)

**To get your GCP IP:**

```bash
gcloud compute instances describe voice-avatar-server \
  --zone=us-central1-a \
  --format='get(networkInterfaces[0].accessConfigs[0].natIP)'
```

---

## 📝 Quick Reference

```bash
# Start everything
docker compose up -d

# Stop everything
docker compose down

# Rebuild everything
docker compose up -d --build

# View logs
docker compose logs -f

# Check status
docker compose ps

# Update code and redeploy
git pull  # or re-upload files
docker compose up -d --build

# Clean everything (⚠️ destroys all containers and cached models)
docker compose down -v
docker system prune -af
```

---

## ✅ Success Checklist

- [ ] GCP instance created with A100 GPU
- [ ] NVIDIA drivers installed and working
- [ ] Docker and Docker Compose installed
- [ ] NVIDIA Container Toolkit configured
- [ ] Project files uploaded
- [ ] .env file configured with HF token and IP
- [ ] Containers built successfully
- [ ] Backend health check returns OK
- [ ] Frontend loads in browser
- [ ] GPU shows ~25-30GB usage
- [ ] Ultravox model loaded successfully
- [ ] Test chat request works
- [ ] Firewall rules configured
- [ ] Performance is acceptable (<5s per response)

---

**Status:** ✅ Ready for Deployment  
**Estimated Total Time:** 1-2 hours (mostly waiting for downloads)  
**Next Steps:** Run `docker compose up -d --build` on GCP and test!
