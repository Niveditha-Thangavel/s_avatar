FROM pytorch/pytorch:2.4.0-cuda12.1-cudnn9-runtime

# System deps: libsndfile1 for soundfile, ffmpeg for MP3/M4A
RUN apt-get update && \
    apt-get install -y --no-install-recommends libsndfile1 ffmpeg && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install remaining Python dependencies (PyTorch is pre-installed in this base image)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code + startup model downloader
COPY vexyl_stt_server.py .
COPY entrypoint.sh ./
RUN chmod +x entrypoint.sh

# Cloud Run injects PORT; set defaults for local testing
ENV PORT=8080 \
    VEXYL_STT_HOST=0.0.0.0 \
    VEXYL_STT_DEVICE=cuda

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=10s --start-period=600s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:${PORT}/health')"

ENTRYPOINT ["./entrypoint.sh"]
