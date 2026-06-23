"""
download_models.py — Pre-download NLLB-200 translation and OmniVoice TTS models at build time.

Downloads:
  1. JustFrederik/nllb-200-distilled-600M-ct2-int8 (~600 MB)
  2. k2-fsa/OmniVoice (~1.2 GB)
"""

import logging
import os
import sys

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("download_models")

CT2_CACHE = os.environ.get(
    "CTRANSLATE2_CACHE",
    os.path.join(os.path.expanduser("~"), ".cache", "ctranslate2"),
)

_NLLB_MODEL = "JustFrederik/nllb-200-distilled-600M-ct2-int8"
_NLLB_DST = os.path.join(CT2_CACHE, "nllb-200-distilled-600M")


def _download_ct2(hf_repo: str, dst_dir: str):
    """Download all files from a HuggingFace CT2 model repo."""
    from huggingface_hub import hf_hub_download, list_repo_files

    os.makedirs(dst_dir, exist_ok=True)
    files = list(list_repo_files(hf_repo))
    visible_files = [f for f in files if not f.startswith(".")]
    for fname in visible_files:
        log.info("  Downloading %s …", fname)
        hf_hub_download(hf_repo, filename=fname, local_dir=dst_dir)
    
    # ── Flatten directory structure if files are nested under subdirectories ──
    import shutil
    config_found_path = None
    for root, dirs, files_in_dir in os.walk(dst_dir):
        if "config.json" in files_in_dir:
            config_found_path = root
            break
            
    if config_found_path and config_found_path != dst_dir:
        log.info("  Flattening nested directory structure from %s to %s …", config_found_path, dst_dir)
        for item in os.listdir(config_found_path):
            s = os.path.join(config_found_path, item)
            d = os.path.join(dst_dir, item)
            if os.path.exists(d):
                if os.path.isdir(d):
                    shutil.rmtree(d)
                else:
                    os.remove(d)
            shutil.move(s, dst_dir)
            
        # Clean up empty subdirectories
        for item in os.listdir(dst_dir):
            p = os.path.join(dst_dir, item)
            if os.path.isdir(p):
                try:
                    shutil.rmtree(p)
                except Exception:
                    pass

    log.info("  ✅ Model files ready in %s", dst_dir)


def _verify_model(name: str, path: str) -> bool:
    """Verify a model directory is loadable. Returns True on success."""
    try:
        import ctranslate2
        import sentencepiece as spm

        # Look for sentencepiece.bpe.model (NLLB) or sentencepiece.model
        sp_path = os.path.join(path, "sentencepiece.bpe.model")
        if not os.path.exists(sp_path):
            sp_path = os.path.join(path, "sentencepiece.model")
            
        if os.path.exists(sp_path):
            sp = spm.SentencePieceProcessor()
            sp.load(sp_path)
            log.info("  %s tokenizer OK (%d vocab)", name, sp.vocab_size())
        else:
            log.warning("  %s: Tokenizer file not found in %s", name, path)

        # Always verify on CPU at build time — GPU not available during build
        t = ctranslate2.Translator(path, device="cpu")
        log.info("  %s model OK (device=%s)", name, t.device)
        return True
    except Exception as exc:
        log.error("  %s verification FAILED: %s", name, exc)
        return False


def main():
    os.makedirs(CT2_CACHE, exist_ok=True)

    # ── 1. NLLB Translation Model ──
    if not os.path.exists(os.path.join(_NLLB_DST, "model.bin")):
        log.info("[1/2] Downloading %s → %s", _NLLB_MODEL, _NLLB_DST)
        _download_ct2(_NLLB_MODEL, _NLLB_DST)
    else:
        log.info("[1/2] ✅ NLLB translation model already cached at %s", _NLLB_DST)

    # ── Smoke test: fail the build loudly if model is broken ──
    log.info("Verifying translation model …")
    ok_nllb = _verify_model("nllb-200", _NLLB_DST)

    if not ok_nllb:
        log.error("❌ Model verification failed — aborting build.")
        sys.exit(1)

    # ── 2. OmniVoice TTS Model ──
    log.info("[2/2] Pre-downloading k2-fsa/OmniVoice TTS model …")
    try:
        from huggingface_hub import snapshot_download
        snapshot_download("k2-fsa/OmniVoice")
        log.info("  ✅ k2-fsa/OmniVoice TTS model cached successfully.")
    except Exception as exc:
        log.error("  ❌ OmniVoice download failed: %s", exc)
        sys.exit(1)

    log.info("✅ All models ready in cache.")


if __name__ == "__main__":
    main()
