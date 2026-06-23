"""
download_models.py — Pre-download IndicTrans2 CT2 models at build time.

Downloads:
  1. ai4bharat/indictrans2-indic-en-1B-ct2   (~1.2 GB)
  2. ai4bharat/indictrans2-en-indic-1B-ct2   (~1.2 GB)

Cache path: ~/.cache/ctranslate2/{en-indic-1B, indic-en-1B}
"""

import logging
import os
import shutil

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("download_models")

HF_CACHE = os.environ.get(
    "TRANSFORMERS_CACHE",
    os.path.join(os.path.expanduser("~"), ".cache", "huggingface"),
)
CT2_CACHE = os.environ.get(
    "CTRANSLATE2_CACHE",
    os.path.join(os.path.expanduser("~"), ".cache", "ctranslate2"),
)

_EN_INDIC_MODEL = "ai4bharat/indictrans2-en-indic-1B-ct2"
_INDIC_EN_MODEL = "ai4bharat/indictrans2-indic-en-1B-ct2"
_EN_INDIC_DST = os.path.join(CT2_CACHE, "en-indic-1B")
_INDIC_EN_DST = os.path.join(CT2_CACHE, "indic-en-1B")


def _download_ct2(hf_repo: str, dst_dir: str):
    """Download all files from a HuggingFace CT2 model repo."""
    from huggingface_hub import hf_hub_download, list_repo_files

    os.makedirs(dst_dir, exist_ok=True)
    files = list_repo_files(hf_repo)
    for fname in files:
        if fname.startswith("."):
            continue
        log.info("  Downloading %s …", fname)
        hf_hub_download(hf_repo, filename=fname, local_dir=dst_dir, local_dir_use_symlinks=False)
    log.info("  ✅ %d files in %s", len([f for f in files if not f.startswith(".")]), dst_dir)


def main():
    os.makedirs(CT2_CACHE, exist_ok=True)

    # ── 1. English → Indic ──
    if not os.path.exists(os.path.join(_EN_INDIC_DST, "model.bin")):
        log.info("[1/2] Downloading %s → %s", _EN_INDIC_MODEL, _EN_INDIC_DST)
        _download_ct2(_EN_INDIC_MODEL, _EN_INDIC_DST)
    else:
        log.info("[1/2] ✅ en→indic already cached at %s", _EN_INDIC_DST)

    # ── 2. Indic → English ──
    if not os.path.exists(os.path.join(_INDIC_EN_DST, "model.bin")):
        log.info("[2/2] Downloading %s → %s", _INDIC_EN_MODEL, _INDIC_EN_DST)
        _download_ct2(_INDIC_EN_MODEL, _INDIC_EN_DST)
    else:
        log.info("[2/2] ✅ indic→en already cached at %s", _INDIC_EN_DST)

    # Quick smoke test
    log.info("Verifying models …")
    try:
        import ctranslate2
        import sentencepiece as spm

        for name, path in [("indic→en", _INDIC_EN_DST), ("en→indic", _EN_INDIC_DST)]:
            sp = spm.SentencePieceProcessor()
            sp_path = os.path.join(path, "sentencepiece.model")
            if os.path.exists(sp_path):
                sp.load(sp_path)
                log.info("  %s tokenizer OK (%d vocab)", name, sp.vocab_size())
            t = ctranslate2.Translator(path, device="cpu")
            log.info("  %s model OK (%s)", name, t.device)
    except Exception as exc:
        log.error("Verification failed: %s", exc)

    log.info("✅ All models ready in %s", CT2_CACHE)


if __name__ == "__main__":
    main()
