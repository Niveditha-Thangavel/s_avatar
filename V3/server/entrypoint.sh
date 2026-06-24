#!/bin/bash
set -e

# ── Startup model downloader for server service ──────────────────────────────
# Downloads IndicTrans2 CT2 + OmniVoice TTS models on first run, then caches
# them in Docker volumes for subsequent insta-starts.
#
# Models from adalat-ai and k2-fsa are NOT gated — no HF_TOKEN needed.

python << 'PYEOF'
import os
import sys
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("entrypoint")

CT2_CACHE = os.environ.get("CTRANSLATE2_CACHE", "/root/.cache/ctranslate2")
os.makedirs(CT2_CACHE, exist_ok=True)

# ── 1. IndicTrans2 CT2 models (not gated, no token needed) ───────────────────
CT2_MODELS = [
    ("adalat-ai/ct2-rotary-indictrans2-en-indic-dist-200M", "ct2-rotary-indictrans2-en-indic-dist-200M"),
    ("adalat-ai/ct2-rotary-indictrans2-indic-en-dist-200M", "ct2-rotary-indictrans2-indic-en-dist-200M"),
]

for repo, name in CT2_MODELS:
    dst = os.path.join(CT2_CACHE, name)
    model_file = os.path.join(dst, "model.bin")
    if os.path.exists(model_file):
        log.info("  %s already cached at %s", repo, dst)
        continue

    log.info("Downloading %s → %s …", repo, dst)
    from huggingface_hub import hf_hub_download, list_repo_files
    os.makedirs(dst, exist_ok=True)
    files = list_repo_files(repo)
    for fname in files:
        if fname.startswith("."):
            continue
        hf_hub_download(repo, filename=fname, local_dir=dst)
    log.info("  ✅ %s downloaded", repo)

# ── 2. OmniVoice TTS (not gated, no token needed) ────────────────────────────
from huggingface_hub import snapshot_download
OV_CACHE = "/root/.cache/huggingface/hub/models--k2-fsa--OmniVoice"
if os.path.exists(OV_CACHE):
    log.info("  k2-fsa/OmniVoice already cached")
else:
    log.info("Downloading k2-fsa/OmniVoice …")
    snapshot_download("k2-fsa/OmniVoice")
    log.info("  ✅ OmniVoice downloaded")

log.info("All models ready.")
PYEOF

# ── Start the server ─────────────────────────────────────────────────────────
exec uvicorn server.main:app \
    --host 0.0.0.0 \
    --port 8765 \
    --loop asyncio \
    --log-level info
