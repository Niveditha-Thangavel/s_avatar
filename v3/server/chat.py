"""
chat.py
Placeholder response layer — returns a canned reply for any user input.

When you're ready to integrate an LLM, replace `get_response()` with your
actual inference call (OpenAI, Ollama, LangChain, etc.). The rest of the
server wiring stays exactly the same.
"""

import random

# ---------------------------------------------------------------------------
# Placeholder responses
# Swap this entire block out when you add a real LLM.
# ---------------------------------------------------------------------------
_RESPONSES = [
    "That's an interesting point. Tell me more.",
    "I understand. How can I help you with that?",
    "Great question! I'm just a placeholder for now, but a real AI will be here soon.",
    "I heard you loud and clear. Let me think about that.",
    "Interesting! I don't have a full answer yet — my language model is still being integrated.",
    "Thanks for saying that. I'm looking forward to giving you a proper response soon.",
    "I'm listening. A smarter version of me is on the way!",
    "Noted. Once the LLM is connected I'll be able to give you a real answer.",
]


async def get_response(user_text: str) -> str:
    """
    Returns a response string for the given user input.

    TODO: Replace the body of this function with a self-hosted LLM call.
    All inference must stay on-device — no external APIs allowed. Examples:

        # Ollama (local):
        import httpx
        r = await httpx.AsyncClient().post(
            "http://localhost:11434/api/generate",
            json={"model": "llama3", "prompt": user_text, "stream": False}
        )
        return r.json()["response"]

        # llama-cpp-python (in-process):
        from llama_cpp import Llama
        llm = Llama(model_path="models/llama3.gguf")
        return llm(user_text)["choices"][0]["text"]
    """
    # --- placeholder logic -------------------------------------------------
    # Pick a random canned reply so the avatar says something different each time.
    return random.choice(_RESPONSES)
    # -----------------------------------------------------------------------
