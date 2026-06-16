"""
llm_engine.py — Granite 4.0 Nano  (GPU-optimised)

GPU path (CUDA):
  • bfloat16 (native on Ampere+; faster than float16, no loss in quality)
  • torch.compile (reduce-overhead) — ~40% speedup on repeated prompts
  • autocast for generation
  • KV-cache enabled (default in transformers, but explicitly guarded)
  • Dedicated single-thread executor

MPS / CPU: float32, no compile
"""

import asyncio
import concurrent.futures
import json
import logging
from collections import deque
from typing import Dict, List

import torch

logger     = logging.getLogger(__name__)
MODEL_ID   = "ibm-granite/granite-4.0-1b"
_model     = None
_tokenizer = None
_lock      = asyncio.Lock()

_sessions: Dict[str, deque] = {}
MAX_TURNS = 10

# Dedicated executor — LLM inference is long; keep it off the shared pool
_executor = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="llm")


def _history(session_id: str) -> deque:
    if session_id not in _sessions:
        _sessions[session_id] = deque(maxlen=MAX_TURNS)
    return _sessions[session_id]


def _get_device() -> str:
    if torch.cuda.is_available():
        return "cuda"
    if torch.backends.mps.is_available():
        return "mps"
    return "cpu"


async def load_model():
    global _model, _tokenizer
    if _model is not None:
        return _model, _tokenizer
    async with _lock:
        if _model is not None:
            return _model, _tokenizer

        from transformers import AutoModelForCausalLM, AutoTokenizer
        device = _get_device()
        # bfloat16 on CUDA (supported on Ampere+, A100, H100, RTX 30xx+)
        # float32 on MPS/CPU (bfloat16 not always reliable on MPS)
        dtype  = torch.bfloat16 if device == "cuda" else torch.float32

        logger.info("[LLM] Loading Granite '%s' on %s (%s)…", MODEL_ID, device, dtype)
        _tokenizer = AutoTokenizer.from_pretrained(MODEL_ID)
        _model = AutoModelForCausalLM.from_pretrained(
            MODEL_ID,
            device_map=device,
            torch_dtype=dtype,
        )
        _model.eval()

        if device == "cuda":
            torch.backends.cudnn.benchmark = True
            logger.info("[LLM] torch.compile (reduce_overhead) …")
            try:
                _model = torch.compile(_model, mode="reduce-overhead", fullgraph=False)
                logger.info("[LLM] torch.compile done")
            except Exception as e:
                logger.warning("[LLM] torch.compile skipped: %s", e)

        logger.info("[LLM] ✅ Granite ready on %s", device)
        return _model, _tokenizer


def _build_system(user_emotion: str = "") -> str:
    parts = [
        "You are a friendly, helpful talking avatar assistant. "
        "The user's speech has been translated to English for you. "
    ]
    if user_emotion:
        parts.append(
            f"The user's detected emotion is: {user_emotion}. "
            "Acknowledge their emotional state naturally in your response. "
        )
    parts.append(
        "Reply in English. Keep replies short — 1 to 3 sentences, natural for speech. "
        "No markdown, no bullet points. "
        "Crucial: In your response text, include emotion tokens in square brackets "
        "where appropriate. Choose from: [laughter], [sigh], [surprise-oh], "
        "[dissatisfaction-hnn]. "
        "Output ONLY valid JSON with exactly these keys: "
        '{"intent":"<label>","emotion":"<neutral|happy|sad|angry|surprised>","response":"<reply>"}'
    )
    return "".join(parts)


def _parse(raw: str) -> Dict[str, str]:
    text = raw.strip()
    if text.startswith("```"):
        lines = text.splitlines()[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    for candidate in [text, text[text.find("{"):text.rfind("}")+1]]:
        try:
            p = json.loads(candidate)
            if "response" in p:
                return p
        except Exception:
            pass
    return {
        "intent": "unknown", "emotion": "neutral",
        "response": text.split("\n")[0][:300].strip() or "I'm here to help.",
    }


async def generate_response(
    user_text: str,
    session_id: str = "default",
    user_emotion: str = "",
) -> Dict[str, str]:
    if not user_text.strip():
        return {"intent": "empty", "emotion": "neutral",
                "response": "I didn't catch that. Could you say that again?"}

    model, tokenizer = await load_model()
    history = _history(session_id)
    device  = _get_device()

    messages: List[Dict] = [{"role": "system", "content": _build_system(user_emotion)}]
    messages.extend(history)
    messages.append({"role": "user", "content": user_text})

    loop = asyncio.get_event_loop()

    def _run():
        prompt = tokenizer.apply_chat_template(
            messages, tokenize=False, add_generation_prompt=True
        )
        ids = tokenizer(prompt, return_tensors="pt").to(model.device)

        ctx = (
            torch.cuda.amp.autocast(dtype=torch.bfloat16)
            if device == "cuda"
            else _null_ctx()
        )
        with torch.no_grad(), ctx:
            out = model.generate(
                **ids,
                max_new_tokens=200,     # 256→200: shorter = faster, still plenty
                do_sample=True,
                temperature=0.7,
                top_p=0.9,
                pad_token_id=tokenizer.eos_token_id,
                use_cache=True,         # KV-cache — critical for GPU speed
            )
        new_ids = out[0][ids["input_ids"].shape[1]:]
        return tokenizer.decode(new_ids, skip_special_tokens=True).strip()

    try:
        raw = await loop.run_in_executor(_executor, _run)
    except Exception as exc:
        logger.exception("[LLM] Inference failed: %s", exc)
        return {"intent": "error", "emotion": "neutral",
                "response": "Sorry, I ran into a problem."}

    logger.info("[LLM] raw: %s", raw[:200])
    payload = _parse(raw)
    history.append({"role": "user",      "content": user_text})
    history.append({"role": "assistant", "content": payload.get("response", "")})
    return payload


class _null_ctx:
    def __enter__(self): return self
    def __exit__(self, *_): pass
