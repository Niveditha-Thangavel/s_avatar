"""
llm_engine.py — Hardcoded emotion responses (no LLM model)
"""

import logging

logger = logging.getLogger(__name__)

_EMOTION_RESPONSES = {
    "neutral":    "I understand. How can I help you today?",
    "happy":      "That is great to hear! I am glad you feel that way.",
    "sad":        "I am sorry to hear that. I hope things get better soon.",
    "angry":      "I understand your frustration. Let me see what I can do to help.",
    "surprised":  "Wow, that is quite something! I can see why you are surprised.",
    "fearful":    "That sounds concerning. Do not worry, I am here to help.",
}

_INTENT_KEYWORDS = {
    "greeting": ["hello", "hi", "hey", "good morning", "good evening", "good afternoon"],
    "farewell": ["bye", "goodbye", "see you", "talk later", "good night"],
    "thanks":   ["thank", "thanks", "appreciate"],
    "help":     ["help", "can you", "how do", "what is"],
    "consent":  ["yes", "yeah", "sure", "okay", "ok", "alright"],
    "denial":   ["no", "nah", "nope", "not really", "never mind"],
}

_VALID_EMOTIONS = set(_EMOTION_RESPONSES.keys())


def _detect_intent(text: str) -> str:
    lower = text.lower()
    for intent, keywords in _INTENT_KEYWORDS.items():
        if any(kw in lower for kw in keywords):
            return intent
    return "unknown"


def _detect_emotion(text: str) -> str:
    lower = text.lower()
    if any(w in lower for w in ["angry", "mad", "frustrated", "annoyed"]):
        return "angry"
    if any(w in lower for w in ["sad", "unhappy", "depressed", "sorry", "miss", "bad", "terrible"]):
        return "sad"
    if any(w in lower for w in ["happy", "glad", "great", "wonderful", "amazing", "love"]):
        return "happy"
    if any(w in lower for w in ["scared", "afraid", "worried", "fearful", "nervous"]):
        return "fearful"
    if any(w in lower for w in ["wow", "surprised", "shocked", "unexpected", "oh"]):
        return "surprised"
    return "neutral"


async def load_model():
    logger.info("[LLM] Using hardcoded responses (no model loaded)")
    return None, None


async def generate_response(
    user_text: str,
    session_id: str = "default",
    user_emotion: str = "",
) -> dict:
    if not user_text.strip():
        return {"intent": "empty", "emotion": "happy",
                "response": "I didn't catch that. Could you say that again?"}

    text = user_text.strip()
    intent = _detect_intent(text)

    if user_emotion in _VALID_EMOTIONS:
        emotion = user_emotion
    else:
        emotion = _detect_emotion(text)

    response = _EMOTION_RESPONSES.get(emotion, _EMOTION_RESPONSES["neutral"])

    logger.info("[LLM] intent=%s emotion=%s text=%s", intent, emotion, text[:60])

    return {
        "intent":   intent,
        "emotion":  emotion,
        "response": response,
    }
