"""
download_models.py — Pre-download IndicTrans2 CT2 models at build time.

Downloads:
  1. ai4bharat/indictrans2-indic-en-1B-ct2   (~1.2 GB)
  2. ai4bharat/indictrans2-en-indic-1B-ct2   (~1.2 GB)

Cache path: ~/.cache/ctranslate2/{en-indic-1B, indic-en-1B}
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

_EN_INDIC_MODEL = "adalat-ai/ct2-rotary-indictrans2-en-indic-1B"
_INDIC_EN_MODEL = "adalat-ai/ct2-rotary-indictrans2-indic-en-1B"
_EN_INDIC_DST = os.path.join(CT2_CACHE, "en-indic-1B")
_INDIC_EN_DST = os.path.join(CT2_CACHE, "indic-en-1B")


def _download_ct2(hf_repo: str, dst_dir: str):
    """Download all files from a HuggingFace CT2 model repo."""
    from huggingface_hub import hf_hub_download, list_repo_files

    os.makedirs(dst_dir, exist_ok=True)
    files = list(list_repo_files(hf_repo))
    visible_files = [f for f in files if not f.startswith(".")]
    for fname in visible_files:
        log.info("  Downloading %s …", fname)
        # NOTE: local_dir_use_symlinks removed — deprecated in huggingface_hub>=0.23
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
            
        # Clean up empty subdirectories (excluding vocab)
        for item in os.listdir(dst_dir):
            p = os.path.join(dst_dir, item)
            if os.path.isdir(p) and item != "vocab":
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

        sp_src_path = os.path.join(path, "vocab", "model.SRC")
        sp_tgt_path = os.path.join(path, "vocab", "model.TGT")
        if os.path.exists(sp_src_path) and os.path.exists(sp_tgt_path):
            sp_src = spm.SentencePieceProcessor()
            sp_src.load(sp_src_path)
            sp_tgt = spm.SentencePieceProcessor()
            sp_tgt.load(sp_tgt_path)
            log.info("  %s tokenizers OK (SRC: %d, TGT: %d vocab)", name, sp_src.vocab_size(), sp_tgt.vocab_size())
        else:
            sp_path = os.path.join(path, "sentencepiece.model")
            if os.path.exists(sp_path):
                sp = spm.SentencePieceProcessor()
                sp.load(sp_path)
                log.info("  %s tokenizer OK (%d vocab)", name, sp.vocab_size())
            else:
                log.warning("  %s: Tokenizer files (model.SRC/model.TGT or sentencepiece.model) not found in %s", name, path)

        # Always verify on CPU at build time — GPU not available during build
        t = ctranslate2.Translator(path, device="cpu")
        log.info("  %s model OK (device=%s)", name, t.device)
        return True
    except Exception as exc:
        log.error("  %s verification FAILED: %s", name, exc)
        return False


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

    # ── Smoke test: fail the build loudly if models are broken ──
    log.info("Verifying translation models …")
    ok_en_indic = _verify_model("en→indic", _EN_INDIC_DST)
    ok_indic_en = _verify_model("indic→en", _INDIC_EN_DST)

    if not (ok_en_indic and ok_indic_en):
        log.error("❌ Model verification failed — aborting build.")
        sys.exit(1)

    # ── 3. OmniVoice TTS Model ──
    log.info("[3/3] Pre-downloading k2-fsa/OmniVoice TTS model …")
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

