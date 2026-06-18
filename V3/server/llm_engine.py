"""
llm_engine.py — Granite 4.0 Nano  (GPU-optimised)

GPU path (CUDA):
  • bfloat16, torch.compile(reduce-overhead), autocast, KV-cache
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
        dtype  = torch.bfloat16 if device == "cuda" else torch.float32

        logger.info("[LLM] Loading Granite '%s' on %s (%s)…", MODEL_ID, device, dtype)
        _tokenizer = AutoTokenizer.from_pretrained(MODEL_ID)
        _model = AutoModelForCausalLM.from_pretrained(
            MODEL_ID, device_map=device, torch_dtype=dtype,
        )
        _model.eval()

        if device == "cuda":
            torch.backends.cudnn.benchmark = True
            try:
                _model = torch.compile(_model, mode="reduce-overhead", fullgraph=False)
                logger.info("[LLM] torch.compile done")
            except Exception as e:
                logger.warning("[LLM] torch.compile skipped: %s", e)

        logger.info("[LLM] ✅ Granite ready on %s", device)
        return _model, _tokenizer


# ── Valid emotion values ──────────────────────────────────────────────────────
VALID_EMOTIONS = {"neutral", "happy", "sad", "angry", "surprised", "fearful"}


def _build_system(user_emotion: str = "") -> str:
    """
    System prompt that forces the LLM to:
    1. Always output valid JSON with the exact schema required.
    2. Always fill the `emotion` field with one of the valid emotion labels.
    3. Include inline emotion tokens in the response text where natural.
    """
    emotion_context = ""
    if user_emotion and user_emotion != "neutral":
        emotion_context = (
            f'The user sounds {user_emotion}. '
            "Acknowledge this naturally in your reply. "
        )

    return (
        "You are a friendly talking avatar assistant. "
        "Reply in English. Keep replies to 1-3 short sentences — natural for spoken audio. "
        "No markdown, no bullet points. "
        + emotion_context +
        "\n\n"
        "EMOTION RULES:\n"
        "1. Set `emotion` to exactly one of: neutral, happy, sad, angry, surprised, fearful\n"
        "   - Use happy for positive, upbeat, or congratulatory replies\n"
        "   - Use sad for condolences, disappointment, or sympathy\n"
        "   - Use angry for frustration or strong disagreement\n"
        "   - Use surprised for unexpected or astonishing information\n"
        "   - Use fearful for warnings, danger, or alarming news\n"
        "   - Use neutral for normal informational replies\n"
        "2. Optionally embed ONE inline sound token in the response text:\n"
        "   [laughter] — for funny or amusing moments\n"
        "   [sigh]     — for relief, tiredness, or resigned acceptance\n"
        "   [surprise-oh] — for sudden surprises\n"
        "   [dissatisfaction-hnn] — for mild disappointment\n"
        "   Example: 'That is wonderful news! [laughter] I am so happy for you.'\n"
        "   Example: 'Oh no, that sounds difficult. [sigh] Let me help you.'\n"
        "\n"
        "OUTPUT FORMAT — respond with ONLY this JSON, nothing else:\n"
        '{"intent":"<label>","emotion":"<neutral|happy|sad|angry|surprised|fearful>","response":"<1-3 sentence reply with optional inline token>"}'
    )


def _parse(raw: str) -> Dict[str, str]:
    text = raw.strip()
    # Strip markdown code fences
    if text.startswith("```"):
        lines = text.splitlines()[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    # Try full text then substring extraction
    for candidate in [text, text[text.find("{"):text.rfind("}")+1]]:
        try:
            p = json.loads(candidate)
            if "response" in p:
                # Normalise emotion value — default to happy if missing/invalid
                em = str(p.get("emotion", "happy")).lower().strip()
                p["emotion"] = em if em in VALID_EMOTIONS else "happy"
                return p
        except Exception:
            pass
    # Fallback — return raw text as response with happy emotion
    return {
        "intent":   "unknown",
        "emotion":  "happy",
        "response": text.split("\n")[0][:300].strip() or "I'm here to help.",
    }


async def generate_response(
    user_text: str,
    session_id: str = "default",
    user_emotion: str = "",
) -> Dict[str, str]:
    if not user_text.strip():
        return {"intent": "empty", "emotion": "happy",
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
            if device == "cuda" else _null_ctx()
        )
        with torch.no_grad(), ctx:
            out = model.generate(
                **ids,
                max_new_tokens=200,
                do_sample=True,
                temperature=0.7,
                top_p=0.9,
                pad_token_id=tokenizer.eos_token_id,
                use_cache=True,
            )
        new_ids = out[0][ids["input_ids"].shape[1]:]
        return tokenizer.decode(new_ids, skip_special_tokens=True).strip()

    try:
        raw = await loop.run_in_executor(_executor, _run)
    except Exception as exc:
        logger.exception("[LLM] Inference failed: %s", exc)
        return {"intent": "error", "emotion": "happy",
                "response": "Sorry, I ran into a problem."}

    logger.info("[LLM] raw: %s", raw[:200])
    payload = _parse(raw)
    logger.info("[LLM] emotion=%s response=%s", payload["emotion"], payload.get("response","")[:80])

    history.append({"role": "user",      "content": user_text})
    history.append({"role": "assistant", "content": payload.get("response", "")})
    return payload


class _null_ctx:
    def __enter__(self): return self
    def __exit__(self, *_): pass
