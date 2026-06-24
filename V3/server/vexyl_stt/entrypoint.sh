#!/bin/bash
set -e

# ── Startup model downloader for vexyl-stt service ───────────────────────────
# Downloads ai4bharat/indic-conformer-600m-multilingual on first run.
# This model is GATED on HuggingFace — requires HF_TOKEN for initial download.
# Once cached in the Docker volume, subsequent runs skip the download entirely.

python << 'PYEOF'
import os
import sys
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("entrypoint")

MODEL_ID = "ai4bharat/indic-conformer-600m-multilingual"
CACHE_MARKER = "/root/.cache/huggingface/hub/models--ai4bharat--indic-conformer-600m-multilingual"

if os.path.exists(CACHE_MARKER):
    log.info("  %s already cached — HF_TOKEN not needed", MODEL_ID)
else:
    token = os.environ.get("HF_TOKEN", "")
    if not token:
        log.error(
            "  %s is gated but not cached and HF_TOKEN is not set.\n"
            "  Set HF_TOKEN in .env, then run: docker compose up -d",
            MODEL_ID,
        )
        sys.exit(1)
    log.info("Downloading %s …", MODEL_ID)
    from huggingface_hub import snapshot_download
    snapshot_download(MODEL_ID, token=token)
    log.info("  ✅ %s downloaded", MODEL_ID)

log.info("Model ready.")
PYEOF

# ── Start the STT server ────────────────────────────────────────────────────
exec python -u vexyl_stt_server.py
