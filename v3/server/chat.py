"""
chat.py
Integrates mashriram/Sarvam-1-VL-4B-Instruct-VLLM to process user intent
and generate dual-track responses (native_text and romanized_text) aligned 1:1.
Includes a robust multilingual mock responder for local/offline testing.
"""

import os
import json
import logging
import asyncio
import torch
from typing import Dict, Tuple, Optional

# Set up logging
logger = logging.getLogger(__name__)

MODEL_ID = "mashriram/Sarvam-1-VL-4B-Instruct-VLLM"
_model = None
_processor = None
_model_failed = False
_lock = asyncio.Lock()


# ---------------------------------------------------------------------------
# Lazy model loading
# ---------------------------------------------------------------------------

async def load_chat_model() -> Tuple[Optional[object], Optional[object]]:
    """Loads the Sarvam-1-VL model and processor lazily with error fallback."""
    global _model, _processor, _model_failed
    if _model_failed:
        return None, None
    if _model is not None:
        return _model, _processor

    async with _lock:
        if _model is not None:
            return _model, _processor

        try:
            logger.info("[Chat] Loading model '%s' ...", MODEL_ID)
            # Deferred heavy imports
            from transformers import Qwen3VLForConditionalGeneration, Qwen3VLProcessor

            # Determine best device (MPS for Apple Silicon, CUDA for GPU, otherwise CPU)
            device = "cpu"
            if torch.backends.mps.is_available():
                device = "mps"
            elif torch.cuda.is_available():
                device = "cuda"

            dtype = torch.float32 if device in ("cpu", "mps") else torch.float16

            processor = Qwen3VLProcessor.from_pretrained(MODEL_ID)
            model = Qwen3VLForConditionalGeneration.from_pretrained(
                MODEL_ID,
                torch_dtype=dtype,
                device_map=device
            )

            _model = model
            _processor = processor
            logger.info("[Chat] Model loaded successfully on device: %s", device)
            return _model, _processor
        except Exception as exc:
            logger.warning(
                "[Chat] Could not load Sarvam-1-VL model: %s. Falling back to Mock Responder.",
                exc
            )
            _model_failed = True
            return None, None


# ---------------------------------------------------------------------------
# Multilingual Mock Responder for Offline / Test Use
# ---------------------------------------------------------------------------

_MOCK_RESPONSES = {
    "ta": [
        {
            "native_text": "வணக்கம், நான் உங்கள் ஏஐ உதவியாளர். இன்று உங்களுக்கு நான் எப்படி உதவ வேண்டும்?",
            "romanized_text": "Vanakkam, naan ungal AI udhaviyaalar. Indru ungalukku naan eppadi udhava veendum?"
        },
        {
            "native_text": "நன்றாக இருக்கிறது! உங்கள் கேள்விக்கு பதில் அளிக்க நான் தயாராக இருக்கிறேன்.",
            "romanized_text": "Nandraaga irukkiradhu! Ungal keelvikku badhil alikka naan thayaaraaga irukkireen."
        },
        {
            "native_text": "மன்னிக்கவும், எனக்கு அது சரியாக புரியவில்லை. மீண்டும் ஒருமுறை சொல்ல முடியுமா?",
            "romanized_text": "Mannikkavum, enakku adhu sariyaaga puriyavillai. Meendum orumurai solla mudiyuma?"
        }
    ],
    "hi": [
        {
            "native_text": "नमस्ते, मैं आपका एआई सहायक हूँ। आज मैं आपकी कैसे मदद कर सकता हूँ?",
            "romanized_text": "Namaste, main aapka AI sahayak hoon. Aaj main aapki kaise madad kar sakta hoon?"
        },
        {
            "native_text": "यह एक बहुत अच्छा सवाल है। मुझे इसके बारे में और बताएं।",
            "romanized_text": "Yeh ek bahut achha sawal hai. Mujhe iske baare mein aur batayein."
        },
        {
            "native_text": "क्षमा करें, मुझे समझ नहीं आया। क्या आप फिर से कह सकते हैं?",
            "romanized_text": "Kshama karein, mujhe samajh nahi aaya. Kya aap phir se keh sakte hain?"
        }
    ],
    "te": [
        {
            "native_text": "నమస్కారం, నేను మీ ఏఐ సహాయకుడిని. ఈరోజు నేను మీకు ఎలా సహాయపడగలను?",
            "romanized_text": "Namaskaram, nenu mee AI sahayakudini. Eeroju nenu meeku ela sahayapadagalanu?"
        },
        {
            "native_text": "చాలా సంతోషం! మీతో మాట్లాడటం నాకు చాలా ఆనందంగా ఉంది.",
            "romanized_text": "Chaala santhosham! Meethoo maatlaadatam naaku chaala aanandangaa undhi."
        },
        {
            "native_text": "నన్ను క్షమించండి, నాకు అర్థం కాలేదు. దయచేసి మళ్ళీ చెప్పండి?",
            "romanized_text": "Nannu kshaminchandi, naaku artham kaaledhu. Dayachesi mallee cheppandi?"
        }
    ],
    "en": [
        {
            "native_text": "Hello, I am your AI assistant. How can I help you today?",
            "romanized_text": "Hello, I am your AI assistant. How can I help you today?"
        },
        {
            "native_text": "That is a very interesting question. Please tell me more about it.",
            "romanized_text": "That is a very interesting question. Please tell me more about it."
        },
        {
            "native_text": "I am sorry, I did not catch that. Could you repeat it?",
            "romanized_text": "I am sorry, I did not catch that. Could you repeat it?"
        }
    ]
}


def _detect_language(text: str) -> str:
    """Helper to detect language based on Unicode ranges (Tamil, Hindi, Telugu, English)."""
    for char in text:
        cp = ord(char)
        if 0x0b80 <= cp <= 0x0bff:
            return "ta"  # Tamil
        elif 0x0900 <= cp <= 0x097f:
            return "hi"  # Hindi / Devanagari
        elif 0x0c00 <= cp <= 0x0c7f:
            return "te"  # Telugu
    return "en"      # Default to English


def get_mock_response(user_text: str) -> Dict[str, str]:
    """Generates an aligned mock dual-text response based on input language."""
    import random
    lang = _detect_language(user_text)
    responses = _MOCK_RESPONSES.get(lang, _MOCK_RESPONSES["en"])
    return random.choice(responses)


# ---------------------------------------------------------------------------
# Core Response Generation
# ---------------------------------------------------------------------------

async def get_response(user_text: str) -> Dict[str, str]:
    """
    Evaluates query and converts response into a custom structured JSON
    with matched 1:1 structural layout ('native_text' and 'romanized_text').
    """
    if not user_text.strip():
        return {"native_text": "", "romanized_text": ""}

    model, processor = await load_chat_model()
    if model is None or processor is None:
        logger.info("[Chat] Model not active, using Mock Responder")
        return get_mock_response(user_text)

    try:
        system_prompt = (
            "You are a helpful multilingual talking avatar assistant.\n"
            "Respond in the user's language, and format your output as a JSON object containing exactly two keys:\n"
            "1. 'native_text': The response in the native script (e.g. Tamil, Hindi, Telugu, or English).\n"
            "2. 'romanized_text': The phonetically equivalent Romanized transliteration of the native text.\n\n"
            "CRITICAL RULES:\n"
            "- If the user query is in English, both native_text and romanized_text must be the identical English sentence.\n"
            "- Both sentences must feature the identical amount of words.\n"
            "- Punctuation flags and grammatical cadence must match directly.\n"
            "- Splitting both strings by spaces must yield a perfectly mirrored array mapping index.\n"
            "- Only return valid JSON. Do not write markdown tags (like ```json ... ```) or any extra conversational filler."
        )

        messages = [
            {"role": "system", "content": system_prompt},
            # Few-shot English
            {"role": "user", "content": "Hello! Who are you?"},
            {"role": "assistant", "content": '{"native_text": "Hello, I am your AI assistant.", "romanized_text": "Hello, I am your AI assistant."}'},
            # Few-shot Tamil
            {"role": "user", "content": "வணக்கம், நீங்கள் யார்?"},
            {"role": "assistant", "content": '{"native_text": "வணக்கம், நான் உங்கள் ஏஐ உதவியாளர்.", "romanized_text": "Vanakkam, naan ungal AI udhaviyaalar."}'},
            # Few-shot Hindi
            {"role": "user", "content": "नमस्ते, आप कौन हैं?"},
            {"role": "assistant", "content": '{"native_text": "नमस्ते, मैं आपका एआई सहायक हूँ।", "romanized_text": "Namaste, main aapka AI sahayak hoon."}'},
            # Actual query
            {"role": "user", "content": user_text}
        ]

        text_prompt = processor.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)

        loop = asyncio.get_event_loop()
        raw_response = await loop.run_in_executor(
            None,
            _run_inference,
            model,
            processor,
            text_prompt
        )

        # Parse structured output
        clean_response = raw_response.strip()
        if clean_response.startswith("```"):
            lines = clean_response.split("\n")
            if lines[0].startswith("```json") or lines[0].startswith("```"):
                lines = lines[1:-1]
            clean_response = "\n".join(lines).strip()

        try:
            payload = json.loads(clean_response)
            if isinstance(payload, dict) and "native_text" in payload and "romanized_text" in payload:
                return payload
        except (json.JSONDecodeError, TypeError) as parse_err:
            logger.warning("[Chat] Failed to parse JSON response: %s. Raw response: %r", parse_err, raw_response)

        # Fallback if structure is malformed
        return {
            "native_text": raw_response,
            "romanized_text": raw_response
        }

    except Exception as exc:
        logger.exception("[Chat] Failed to generate response from model: %s", exc)
        return get_mock_response(user_text)


def _run_inference(model, processor, text_prompt: str) -> str:
    """Synchronous model execution run in executor thread."""
    inputs = processor(text=[text_prompt], return_tensors="pt").to(model.device)
    with torch.inference_mode():
        outputs = model.generate(**inputs, max_new_tokens=128, use_cache=True)
    
    # Decodes only the newly generated token IDs
    generated_ids = [
        output_ids[len(input_ids):]
        for input_ids, output_ids in zip(inputs.input_ids, outputs)
    ]
    return processor.batch_decode(generated_ids, skip_special_tokens=True)[0]
