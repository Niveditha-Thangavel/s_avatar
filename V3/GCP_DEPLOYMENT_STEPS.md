# GCP Deployment Steps - Voice Avatar

**Target:** Ubuntu Server with 80GB GPU (A100/H100)  
**Date:** June 14, 2026  
**Verification Status:** ✅ All components verified and ready

---

## Prerequisites Checklist

Before starting, ensure you have:

- [ ] GCP account with billing enabled
- [ ] GCP project created
- [ ] `gcloud` CLI installed on your Mac
- [ ] Hugging Face account with token
- [ ] Llama 3.1 license accepted at https://huggingface.co/meta-llama/Llama-3.1-8B

---

## Part 1: GCP VM Setup (10-15 minutes)

### Step 1.1: Create VM Instance with GPU

From your **Mac terminal**:

```bash
# Set your project ID (replace with your actual project ID)
export GCP_PROJECT_ID="your-project-id"
gcloud config set project $GCP_PROJECT_ID

# Create A100 GPU instance
gcloud compute instances create voice-avatar-server \
  --zone=us-central1-a \
  --machine-type=a2-highgpu-1g \
  --accelerator=type=nvidia-tesla-a100,count=1 \
  --image-family=ubuntu-2204-lts \
  --image-project=ubuntu-os-cloud \
  --boot-disk-size=200GB \
  --boot-disk-type=pd-ssd \
  --maintenance-policy=TERMINATE \
  --metadata=install-nvidia-driver=True \
  --tags=http-server,https-server

# Wait for instance to be created (2-3 minutes)
```

**Expected Output:**
```
Created [https://www.googleapis.com/compute/v1/projects/.../instances/voice-avatar-server].
NAME: voice-avatar-server
ZONE: us-central1-a
MACHINE_TYPE: a2-highgpu-1g
...
```

### Step 1.2: Configure Firewall Rules

```bash
# Allow frontend access (port 3005)
gcloud compute firewall-rules create voice-avatar-frontend \
  --allow tcp:3005 \
  --source-ranges 0.0.0.0/0 \
  --target-tags http-server \
  --description "Voice Avatar Frontend"

# Allow backend WebSocket access (port 8765)
gcloud compute firewall-rules create voice-avatar-backend \
  --allow tcp:8765 \
  --source-ranges 0.0.0.0/0 \
  --target-tags http-server \
  --description "Voice Avatar Backend API"
```

### Step 1.3: Get External IP Address

```bash
# Get the external IP (you'll need this later)
export GCP_IP=$(gcloud compute instances describe voice-avatar-server \
  --zone=us-central1-a \
  --format='get(networkInterfaces[0].accessConfigs[0].natIP)')

echo "Your server IP: $GCP_IP"

# Save it for later
echo "export GCP_IP=$GCP_IP" >> ~/.voice_avatar_env
```

**Expected Output:**
```
Your server IP: 34.123.45.67
```

---

## Part 2: SSH and Install Dependencies (15-20 minutes)

### Step 2.1: SSH into the Instance

```bash
gcloud compute ssh voice-avatar-server --zone=us-central1-a
```

**You are now on the GCP Ubuntu server.**

### Step 2.2: Verify GPU Drivers

```bash
# Check NVIDIA driver installation
nvidia-smi

# Expected output: Tesla A100 80GB with CUDA 12.4
```

If `nvidia-smi` doesn't work, install drivers:

```bash
sudo apt-get update
sudo apt-get install -y nvidia-driver-535
sudo reboot

# After reboot, SSH back in and verify
gcloud compute ssh voice-avatar-server --zone=us-central1-a
nvidia-smi
```

### Step 2.3: Install Docker

```bash
# Update system
sudo apt-get update
sudo apt-get upgrade -y

# Install Docker
curl -fsSL https://get.docker.com -o get-docker.sh
sudo sh get-docker.sh

# Add your user to docker group
sudo usermod -aG docker $USER

# Verify Docker installed
docker --version
```

### Step 2.4: Install NVIDIA Container Toolkit

```bash
# Add NVIDIA package repository
distribution=$(. /etc/os-release;echo $ID$VERSION_ID)
curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey | sudo gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg

curl -s -L https://nvidia.github.io/libnvidia-container/$distribution/libnvidia-container.list | \
  sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' | \
  sudo tee /etc/apt/sources.list.d/nvidia-container-toolkit.list

# Install toolkit
sudo apt-get update
sudo apt-get install -y nvidia-container-toolkit

# Configure Docker to use NVIDIA runtime
sudo nvidia-ctk runtime configure --runtime=docker
sudo systemctl restart docker

# Logout and login again for group changes
exit
```

### Step 2.5: SSH Back In and Test GPU in Docker

```bash
# SSH back in
gcloud compute ssh voice-avatar-server --zone=us-central1-a

# Test GPU access from Docker
docker run --rm --gpus all nvidia/cuda:12.4.0-base-ubuntu22.04 nvidia-smi
```

**Expected Output:** Should show Tesla A100 GPU info

---

## Part 3: Upload Project Files (5-10 minutes)

### Option A: Upload from Mac (Recommended)

From your **Mac terminal** (new window):

```bash
# Navigate to project directory
cd /Users/kabil/Desktop/Projects/Simplyfi/JUN-JUL/voice_avatar/s_avatar

# Upload entire v3 folder to GCP
gcloud compute scp --recurse v3 voice-avatar-server:~/ --zone=us-central1-a
```

**Expected Duration:** 2-5 minutes depending on internet speed

### Option B: Clone from Git

From **GCP server**:

```bash
cd ~
git clone <your-repo-url>
cd voice_avatar/s_avatar/v3
```

---

## Part 4: Configure Environment Variables (2 minutes)

On **GCP server**:

```bash
# Navigate to project
cd ~/v3

# Create .env file from example
cp .env.example .env

# Edit .env file
nano .env
```

**Update the following values:**

```bash
# Replace with your GCP external IP
VITE_SERVER_HOST=YOUR_GCP_EXTERNAL_IP  # e.g., 34.123.45.67
VITE_SERVER_PORT=8765

# Replace with your actual Hugging Face token
HF_TOKEN=hf_your_actual_token_here
```

**To get your Hugging Face token:**
1. Go to https://huggingface.co/settings/tokens
2. Create new token or copy existing one
3. Accept Llama 3.1 license: https://huggingface.co/meta-llama/Llama-3.1-8B

**Save and exit:** `Ctrl+X`, then `Y`, then `Enter`

### Verify .env File

```bash
# Check the file
cat .env

# Load environment variables
export $(cat .env | grep -v '^#' | xargs)

# Verify HF_TOKEN is set
echo $HF_TOKEN  # Should show your token
```

---

## Part 5: Build and Deploy (30-60 minutes)

### Step 5.1: Start Docker Build

On **GCP server**:

```bash
# Make sure you're in the project directory
cd ~/v3

# Start building both containers
docker compose up -d --build
```

**What happens now:**

1. **Frontend build** (5-10 minutes):
   - Downloads Node.js base image
   - Installs npm dependencies (vite, three)
   - Builds production bundle
   - Creates nginx container
   
2. **Backend build** (30-50 minutes):
   - Downloads PyTorch CUDA base image (~10 min)
   - Installs Python dependencies (~5 min)
   - Downloads OmniVoice model ~2.4GB (~5 min)
   - Downloads Ultravox model ~16GB (~20-40 min)
   - Caches models into Docker image

**Monitor the build:**

```bash
# Watch build logs
docker compose logs -f

# Or watch individual services
docker compose logs -f server     # Backend
docker compose logs -f frontend   # Frontend
```

**Important:** Don't interrupt! The model downloads are baked into the image.

### Step 5.2: Monitor Build Progress

Open another SSH session to monitor:

```bash
# In a new terminal on Mac
gcloud compute ssh voice-avatar-server --zone=us-central1-a

# Check disk space
df -h

# Watch Docker image sizes grow
watch -n 5 'docker images'

# Monitor GPU (should be idle during build)
watch -n 2 nvidia-smi
```

### Step 5.3: Wait for "ready" Messages

Look for these in logs:

```
voice-avatar-server    | [TTS] OmniVoice ready in X.XXs
voice-avatar-server    | [Chat] Ultravox pipeline loaded successfully on device: cuda with dtype: torch.float16
voice-avatar-server    | [STT] Whisper ready in X.XXs
voice-avatar-server    | [Server] All models ready – accepting connections
voice-avatar-server    | INFO:     Application startup complete.
voice-avatar-frontend  | ... nginx ready ...
```

---

## Part 6: Verify Deployment (5 minutes)

### Step 6.1: Check Container Status

On **GCP server**:

```bash
# Check containers are running
docker compose ps

# Expected output:
# NAME                    STATE     PORTS
# voice-avatar-frontend   Up        0.0.0.0:3005->3005/tcp
# voice-avatar-server     Up        0.0.0.0:8765->8765/tcp
```

### Step 6.2: Test Backend Health

```bash
# Test from server
curl http://localhost:8765/health

# Expected: {"status":"ok","timestamp":1718370000.0}
```

### Step 6.3: Check GPU Usage

```bash
# Check GPU memory (should show ~25-30GB used)
docker exec voice-avatar-server nvidia-smi

# Expected output shows:
# | Processes:                                                  GPU Memory Usage |
# |  voice-avatar-server process using ~25-30GB                                |
```

### Step 6.4: Test from Your Mac

From your **Mac terminal**:

```bash
# Source your saved IP
source ~/.voice_avatar_env

# Test backend
curl http://$GCP_IP:8765/health

# Expected: {"status":"ok","timestamp":...}

# Test text chat
curl -X POST http://$GCP_IP:8765/chat \
  -H "Content-Type: application/json" \
  -d '{"text": "Hello, how are you?"}'

# Expected: JSON response with reply
```

### Step 6.5: Test in Browser

Open in your browser:

```
http://YOUR_GCP_IP:3005
```

**Replace `YOUR_GCP_IP` with your actual IP from Step 1.3**

**Expected:** Voice Avatar UI loads with 3D model

---

## Part 7: Functional Testing (10 minutes)

### Test 1: Text-to-Speech

1. Open `http://YOUR_GCP_IP:3005`
2. Type a message in the text input
3. Click "Speak"
4. **Expected:** 
   - Avatar's mouth moves in sync
   - Audio plays through speakers
   - Lip sync matches speech

### Test 2: Speech-to-Text

1. Click "Listen" button
2. Allow microphone access
3. Speak into microphone: "Hello, who are you?"
4. Click "Stop"
5. **Expected:**
   - Your transcript appears
   - Avatar generates reply
   - Avatar speaks the reply with lip sync

### Test 3: Multilingual (Tamil)

1. Type: "வணக்கம், நீங்கள் யார்?"
2. Click "Speak"
3. **Expected:**
   - Avatar speaks Tamil
   - Lip sync works with romanized text

### Test 4: WebSocket Performance

1. Speak multiple messages quickly
2. **Expected:**
   - No audio glitches
   - Smooth transitions
   - GPU memory stays stable (~25-30GB)

---

## Part 8: Production Checklist

### Security

- [ ] Change firewall rules to restrict IP access (optional)
- [ ] Set up HTTPS with SSL certificate (recommended)
- [ ] Add authentication layer to API (recommended)
- [ ] Rotate HF_TOKEN regularly

### Monitoring

- [ ] Set up GCP Cloud Monitoring
- [ ] Create GPU utilization alerts
- [ ] Monitor disk space (200GB boot disk)
- [ ] Track API request rates

### Backup

- [ ] Save Docker images to registry
- [ ] Backup .env file securely
- [ ] Document custom configurations

---

## Troubleshooting

### Issue: "Out of Memory" Error

```bash
# Check GPU memory
docker exec voice-avatar-server nvidia-smi

# If >75GB used, restart server
docker compose restart server

# Clear GPU cache
docker exec voice-avatar-server python3 -c "import torch; torch.cuda.empty_cache()"
```

### Issue: Model Download Fails

```bash
# Check HF_TOKEN
docker exec voice-avatar-server printenv | grep HF_TOKEN

# If wrong, update .env and rebuild
nano .env  # Fix HF_TOKEN
export HF_TOKEN="correct_token"
docker compose up -d --build server
```

### Issue: Containers Won't Start

```bash
# Check logs
docker compose logs --tail=100

# Remove and rebuild
docker compose down
docker compose up -d --build
```

### Issue: Frontend Can't Connect to Backend

```bash
# Check .env has correct IP
cat .env

# Rebuild frontend with correct IP
nano .env  # Fix VITE_SERVER_HOST
docker compose up -d --build frontend
```

### Issue: Slow Inference (>10s per response)

```bash
# Check GPU utilization during inference
watch -n 1 'docker exec voice-avatar-server nvidia-smi'

# Should show >80% GPU util during inference
# If low, check dtype
docker logs voice-avatar-server | grep dtype
# Should show: torch.float16
```

---

## Management Commands

### View Logs

```bash
# All logs
docker compose logs -f

# Server only
docker compose logs -f server

# Last 100 lines
docker compose logs --tail=100 server
```

### Restart Services

```bash
# Restart both
docker compose restart

# Restart server only
docker compose restart server
```

### Stop Services

```bash
# Stop (keeps containers)
docker compose stop

# Stop and remove containers
docker compose down

# Stop, remove, and clean up volumes
docker compose down -v
```

### Update Code

```bash
# Upload new code from Mac
gcloud compute scp --recurse v3 voice-avatar-server:~/ --zone=us-central1-a

# On GCP, rebuild
cd ~/v3
docker compose up -d --build
```

---

## Resource Usage

After successful deployment, expect:

| Resource | Usage |
|----------|-------|
| Disk Space | ~25GB (models + Docker images) |
| GPU RAM | ~25-30GB (all models loaded) |
| System RAM | ~8-10GB (container overhead) |
| CPU | 10-20% idle, 40-60% during inference |
| Network | Minimal (only user traffic) |

---

## Cost Estimation (GCP)

**A100 80GB GPU Instance (a2-highgpu-1g):**

- **On-demand:** ~$3.67/hour = ~$88/day = ~$2,640/month
- **Preemptible:** ~$1.10/hour = ~$26/day = ~$792/month
- **Committed 1-year:** ~$1.84/hour = ~$44/day = ~$1,320/month

**Additional Costs:**
- 200GB SSD: ~$34/month
- Network egress: Variable (depends on usage)

**Recommendation:** Use preemptible instances for development/testing

---

## Access URLs

After successful deployment:

| Service | URL | Purpose |
|---------|-----|---------|
| **Frontend** | `http://YOUR_GCP_IP:3005` | Main UI |
| **Backend API** | `http://YOUR_GCP_IP:8765` | Direct API access |
| **Health Check** | `http://YOUR_GCP_IP:8765/health` | Server status |
| **API Docs** | `http://YOUR_GCP_IP:8765/docs` | Auto-generated API docs |
| **TTS WebSocket** | `ws://YOUR_GCP_IP:8765/ws/tts` | Text-to-Speech stream |
| **STT WebSocket** | `ws://YOUR_GCP_IP:8765/ws/stt` | Speech-to-Text stream |

---

## Success Criteria

✅ **Deployment Successful When:**

- [ ] `docker compose ps` shows both containers running
- [ ] `curl http://localhost:8765/health` returns `{"status":"ok"}`
- [ ] `nvidia-smi` inside container shows GPU usage ~25-30GB
- [ ] Frontend loads at `http://YOUR_GCP_IP:3005`
- [ ] Avatar 3D model renders correctly
- [ ] Text-to-speech works with lip sync
- [ ] Speech-to-text captures audio and replies
- [ ] Multilingual support works (test Tamil/Hindi)
- [ ] No errors in `docker compose logs`
- [ ] Response latency <5 seconds per inference

---

## Quick Reference Commands

```bash
# From Mac: SSH to server
gcloud compute ssh voice-avatar-server --zone=us-central1-a

# On server: Check status
docker compose ps
docker compose logs -f server

# On server: Restart services
docker compose restart

# On server: Check GPU
docker exec voice-avatar-server nvidia-smi

# On server: Test health
curl http://localhost:8765/health

# From Mac: Test remotely
curl http://$GCP_IP:8765/health

# From Mac: Open in browser
open http://$GCP_IP:3005
```

---

## Next Steps After Deployment

1. **Test all features** (TTS, STT, multilingual)
2. **Monitor GPU memory** for leaks
3. **Measure response latency** under load
4. **Set up HTTPS** for production
5. **Add authentication** if needed
6. **Configure backups** for important data
7. **Set up monitoring alerts**
8. **Document any custom changes**

---

**Estimated Total Time:** 60-90 minutes (mostly waiting for model downloads)

**Status:** ✅ Ready to deploy

**Last Updated:** June 14, 2026
