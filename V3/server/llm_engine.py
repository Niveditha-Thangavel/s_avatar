"""
llm_engine.py — Granite 4.0 Nano (ibm-granite/granite-4.0-1b)
Placeholder LLM: intent detection, emotion detection, response generation.
Apache 2.0 — no HF token required.
"""

import asyncio
import json
import logging
from collections import deque
from typing import Dict, List

import torch

logger = logging.getLogger(__name__)

MODEL_ID  = "ibm-granite/granite-4.0-1b"
_model    = None
_tokenizer = None
_lock     = asyncio.Lock()

_sessions: Dict[str, deque] = {}
MAX_TURNS = 10


def _history(session_id: str) -> deque:
    if session_id not in _sessions:
        _sessions[session_id] = deque(maxlen=MAX_TURNS)
    return _sessions[session_id]


async def load_model():
    global _model, _tokenizer
    if _model is not None:
        return _model, _tokenizer
    async with _lock:
        if _model is not None:
            return _model, _tokenizer
        from transformers import AutoModelForCausalLM, AutoTokenizer
        device = "cuda" if torch.cuda.is_available() else "cpu"
        dtype  = torch.bfloat16 if device == "cuda" else torch.float32
        logger.info("[LLM] Loading Granite '%s' on %s …", MODEL_ID, device)
        _tokenizer = AutoTokenizer.from_pretrained(MODEL_ID)
        _model     = AutoModelForCausalLM.from_pretrained(
            MODEL_ID, device_map=device, torch_dtype=dtype
        )
        _model.eval()
        logger.info("[LLM] ✅ Granite ready on %s", device)
        return _model, _tokenizer


_SYSTEM = (
    "You are a friendly, helpful talking avatar assistant. "
    "The user's speech has been translated to English for you. "
    "Reply in English. Keep replies short — 1 to 3 sentences, natural for speech. "
    "No markdown, no bullet points. "
    "Output ONLY valid JSON with exactly these keys: "
    '{"intent":"<label>","emotion":"<neutral|happy|sad|angry|surprised>","response":"<reply>"}'
)


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
    return {"intent": "unknown", "emotion": "neutral",
            "response": text.split("\n")[0][:300].strip() or "I'm here to help."}


async def generate_response(user_text: str, session_id: str = "default") -> Dict[str, str]:
    if not user_text.strip():
        return {"intent": "empty", "emotion": "neutral",
                "response": "I didn't catch that. Could you say that again?"}

    model, tokenizer = await load_model()
    history = _history(session_id)

    messages: List[Dict] = [{"role": "system", "content": _SYSTEM}]
    messages.extend(history)
    messages.append({"role": "user", "content": user_text})

    loop = asyncio.get_event_loop()

    def _run():
        prompt = tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
        ids    = tokenizer(prompt, return_tensors="pt").to(model.device)
        with torch.no_grad():
            out = model.generate(
                **ids, max_new_tokens=256, do_sample=True,
                temperature=0.7, top_p=0.9, pad_token_id=tokenizer.eos_token_id,
            )
        new_ids = out[0][ids["input_ids"].shape[1]:]
        return tokenizer.decode(new_ids, skip_special_tokens=True).strip()

    try:
        raw = await loop.run_in_executor(None, _run)
    except Exception as exc:
        logger.exception("[LLM] Inference failed: %s", exc)
        return {"intent": "error", "emotion": "neutral", "response": "Sorry, I ran into a problem."}

    logger.info("[LLM] raw: %s", raw[:200])
    payload = _parse(raw)
    history.append({"role": "user",      "content": user_text})
    history.append({"role": "assistant", "content": payload.get("response", "")})
    return payload
