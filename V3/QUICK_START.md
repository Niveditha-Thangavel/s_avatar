# 🚀 Quick Start Guide - Both Frontend & Backend

**Ultra-fast deployment guide for GCP Ubuntu with 80GB GPU**

---

## ⚡ Super Quick Start (Automated)

```bash
# 1. Upload project to GCP
gcloud compute scp --recurse v3 your-instance:~/ --zone=your-zone

# 2. SSH to GCP
gcloud compute ssh your-instance --zone=your-zone

# 3. Go to project
cd ~/v3

# 4. Create .env file
cp .env.example .env
nano .env  # Add your HF_TOKEN

# 5. Run automated deployment
./deploy.sh

# Done! ✅
```

**That's it!** The script will handle everything automatically.

---

## 📝 Manual Quick Start (Docker Compose)

If you prefer manual control:

```bash
# 1. Set your Hugging Face token
export HF_TOKEN="hf_your_actual_token_here"

# 2. Build and run everything
docker compose up -d --build

# 3. Wait 30-60 minutes for models to download

# 4. Check status
docker compose logs -f

# 5. Test
curl http://localhost:8765/health
```

**Access:**
- Frontend: `http://YOUR_IP:3005`
- Backend: `http://YOUR_IP:8765`

---

## 🎯 Three Commands to Deploy

```bash
export HF_TOKEN="your_token_here"
docker compose up -d --build
docker compose logs -f
```

---

## 🔍 Verify Deployment

```bash
# Check containers
docker compose ps

# Check backend
curl http://localhost:8765/health

# Check GPU
docker exec voice-avatar-server nvidia-smi

# View logs
docker compose logs -f server
```

---

## 📱 Common Commands

```bash
# Start
docker compose up -d

# Stop
docker compose down

# Restart
docker compose restart

# Rebuild
docker compose up -d --build

# Logs
docker compose logs -f

# Status
docker compose ps
```

---

## 🐛 Troubleshooting

**Containers won't start:**
```bash
docker compose down
docker compose up -d --build
```

**Model won't load:**
```bash
# Check token
docker exec voice-avatar-server printenv | grep HF_TOKEN

# Rebuild with correct token
export HF_TOKEN="correct_token"
docker compose up -d --build server
```

**Out of memory:**
```bash
# Restart server
docker compose restart server

# Clear GPU cache
docker exec voice-avatar-server python3 -c "import torch; torch.cuda.empty_cache()"
```

---

## 📚 Full Documentation

- **Complete Guide:** [DEPLOYMENT_GUIDE.md](DEPLOYMENT_GUIDE.md)
- **Ultravox Details:** [ULTRAVOX_IMPLEMENTATION_GUIDE.md](ULTRAVOX_IMPLEMENTATION_GUIDE.md)
- **Troubleshooting:** [DEPLOYMENT_GUIDE.md#troubleshooting](DEPLOYMENT_GUIDE.md#troubleshooting)

---

## ✅ Success Checklist

- [ ] GCP instance with A100 GPU
- [ ] Docker + Docker Compose installed
- [ ] NVIDIA Container Toolkit installed
- [ ] .env file with HF_TOKEN configured
- [ ] Containers running: `docker compose ps`
- [ ] Health check OK: `curl localhost:8765/health`
- [ ] Frontend loads: `http://YOUR_IP:3005`
- [ ] GPU shows usage: `docker exec voice-avatar-server nvidia-smi`

---

**Status:** ✅ Ready  
**Time to Deploy:** 1-2 hours (mostly downloads)  
**Difficulty:** Easy 😊
