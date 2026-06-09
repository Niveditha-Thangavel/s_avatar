# ================================================================
# backend/Dockerfile
# ================================================================
FROM python:3.11-slim

# System dependencies
RUN apt-get update && apt-get install -y \
    ffmpeg \
    wget \
    unzip \
    curl \
    libsndfile1 \
    libgomp1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Download Rhubarb lip sync binary
RUN wget -q https://github.com/DanielSWolf/rhubarb-lip-sync/releases/download/v1.13.0/rhubarb-lip-sync-1.13.0-linux.zip \
    && unzip rhubarb-lip-sync-1.13.0-linux.zip \
    && mv rhubarb-lip-sync-1.13.0-linux/rhubarb ./rhubarb \
    && chmod +x ./rhubarb \
    && rm -rf rhubarb-lip-sync-1.13.0-linux*

# Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Download Kokoro model weights at build time (not runtime)
RUN python -c "\
from huggingface_hub import hf_hub_download; \
hf_hub_download('hexgrad/Kokoro-82M', 'kokoro-v0_19.onnx', local_dir='/app/models'); \
hf_hub_download('hexgrad/Kokoro-82M', 'voices.bin', local_dir='/app/models')"

# Download Whisper base model at build time
RUN python -c "import whisper; whisper.load_model('base')"

COPY . .

EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]