"""
chat.py
Integrates fixie-ai/ultravox-v0_6-llama-3_1-8b to handle both audio and text inputs.
Replaces Whisper and Sarvam models, performing direct Speech-to-Text-and-Text reasoning.
Includes a robust mock responder fallback for local testing.
"""

import os
import json
import logging
import asyncio
import torch
import numpy as np
from typing import Dict, Tuple, Optional, List

# Set up logging
logger = logging.getLogger(__name__)

MODEL_ID = "fixie-ai/ultravox-v0_6-llama-3_1-8b"
_pipeline = None
_model_failed = False
_lock = asyncio.Lock()


# ---------------------------------------------------------------------------
# Lazy pipeline loading
# ---------------------------------------------------------------------------

async def load_chat_model() -> Optional[object]:
    """Loads the Ultravox pipeline lazily with error fallback."""
    global _pipeline, _model_failed
    if _model_failed:
        return None
    if _pipeline is not None:
        return _pipeline

    async with _lock:
        if _pipeline is not None:
            return _pipeline

        try:
            logger.info("[Chat] Loading Ultravox model '%s' ...", MODEL_ID)
            import transformers
            # Monkey-patch to fix library version incompatibility with Ultravox custom weight init
            if not hasattr(transformers.modeling_utils, "_init_weights"):
                transformers.modeling_utils._init_weights = True

            # Monkey-patch check_and_set_device_map to bypass meta device context check errors
            try:
                transformers.modeling_utils.check_and_set_device_map = lambda x: x
            except Exception:
                pass
            try:
                import transformers.integrations.accelerate
                transformers.integrations.accelerate.check_and_set_device_map = lambda x: x
            except Exception:
                pass

            # Determine best device (MPS for Apple Silicon, CUDA for GPU, otherwise CPU)
            device = "cpu"
            if torch.backends.mps.is_available():
                device = "mps"
            elif torch.cuda.is_available():
                device = "cuda"

            dtype = torch.float32 if device in ("cpu", "mps") else torch.float16

            # Load the transformers pipeline with trust_remote_code=True
            pipe = transformers.pipeline(
                model=MODEL_ID,
                trust_remote_code=True,
                device=device,
                torch_dtype=dtype
            )

            _pipeline = pipe
            logger.info("[Chat] Ultravox pipeline loaded successfully on device: %s", device)
            return _pipeline
        except Exception as exc:
            logger.warning(
                "[Chat] Could not load Ultravox pipeline: %s. Falling back to Mock Responder.",
                exc
            )
            _model_failed = True
            return None


# ---------------------------------------------------------------------------
# Multilingual Mock Responder
# ---------------------------------------------------------------------------

_MOCK_RESPONSES = {
    "ta": [
        {
            "user_transcript": "வணக்கம், நீங்கள் யார்?",
            "native_text": "வணக்கம், நான் உங்கள் ஏஐ உதவியாளர். இன்று உங்களுக்கு நான் எப்படி உதவ வேண்டும்?",
            "romanized_text": "Vanakkam, naan ungal AI udhaviyaalar. Indru ungalukku naan eppadi udhava veendum?"
        },
        {
            "user_transcript": "எப்படி இருக்கிறீர்கள்?",
            "native_text": "நான் நன்றாக இருக்கிறேன்! உங்கள் கேள்விக்கு பதில் அளிக்க நான் தயாராக இருக்கிறேன்.",
            "romanized_text": "Naan nandraaga irukkireen! Ungal keelvikku badhil alikka naan thayaaraaga irukkireen."
        }
    ],
    "hi": [
        {
            "user_transcript": "नमस्ते, आप कौन हैं?",
            "native_text": "नमस्ते, मैं आपका एआई सहायक हूँ। आज मैं आपकी कैसे मदद कर सकता हूँ?",
            "romanized_text": "Namaste, main aapka AI sahayak hoon. Aaj main aapki kaise madad kar sakta hoon?"
        },
        {
            "user_transcript": "आप कैसे हैं?",
            "native_text": "मैं बहुत अच्छा हूँ। मुझे इसके बारे में और बताएं।",
            "romanized_text": "Main bahut achha hoon. Mujhe iske baare mein aur batayein."
        }
    ],
    "te": [
        {
            "user_transcript": "నమస్కారం, మీరు ఎవరు?",
            "native_text": "నమస్కారం, నేను మీ ఏఐ సహాయకుడిని. ఈరోజు నేను మీకు ఎలా సహాయపడగలను?",
            "romanized_text": "Namaskaram, nenu mee AI sahayakudini. Eeroju nenu meeku ela sahayapadagalanu?"
        },
        {
            "user_transcript": "ఎలా ఉన్నారు?",
            "native_text": "నేను చాలా బాగున్నాను! మీతో మాట్లాడటం నాకు చాలా సంతోషంగా ఉంది.",
            "romanized_text": "Nenu chaala baagunnaanu! Meethoo maatlaadatam naaku chaala santhoshangaa undhi."
        }
    ],
    "en": [
        {
            "user_transcript": "Hello, who are you?",
            "native_text": "Hello, I am your AI assistant. How can I help you today?",
            "romanized_text": "Hello, I am your AI assistant. How can I help you today?"
        },
        {
            "user_transcript": "How are you doing?",
            "native_text": "I am doing great. Please tell me more about it.",
            "romanized_text": "I am doing great. Please tell me more about it."
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
    """Generates an aligned mock response (transcription + reply) based on language."""
    import random
    lang = _detect_language(user_text)
    responses = _MOCK_RESPONSES.get(lang, _MOCK_RESPONSES["en"])
    return random.choice(responses)


# ---------------------------------------------------------------------------
# Helper output extractors
# ---------------------------------------------------------------------------

def _extract_text_from_output(output) -> str:
    """Robustly extracts output text from transformers pipeline results."""
    if isinstance(output, str):
        return output
    if isinstance(output, dict):
        if "text" in output:
            return output["text"]
        if "generated_text" in output:
            return output["generated_text"]
    if isinstance(output, list) and len(output) > 0:
        return _extract_text_from_output(output[0])
    return str(output)


def _parse_json_response(raw_text: str) -> Optional[Dict[str, str]]:
    """Cleans up markdown ticks and parses the JSON response."""
    clean = raw_text.strip()
    if clean.startswith("```"):
        lines = clean.split("\n")
        if lines[0].startswith("```json") or lines[0].startswith("```"):
            lines = lines[1:-1]
        clean = "\n".join(lines).strip()
    try:
        return json.loads(clean)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Multimodal Audio Response Generation
# ---------------------------------------------------------------------------

async def get_response_from_audio(audio_np: np.ndarray) -> Dict[str, str]:
    """
    Consumes raw 16kHz float32 audio and generates a unified response payload
    containing: 'user_transcript', 'native_text', and 'romanized_text'.
    """
    pipe = await load_chat_model()
    if pipe is None:
        logger.info("[Chat-Audio] Model not active, using English Mock Response")
        return get_mock_response("")

    try:
        system_prompt = (
            "You are a helpful multilingual talking avatar assistant.\n"
            "Listen to the user's speech in this audio, transcribe it, and generate your conversational response in the user's language.\n"
            "Format your output as a JSON object containing exactly three keys:\n"
            "1. 'user_transcript': The transcription of what the user said in the native script.\n"
            "2. 'native_text': Your reply to the user in the native script.\n"
            "3. 'romanized_text': The phonetically equivalent Romanized transliteration of 'native_text'.\n\n"
            "CRITICAL RULES:\n"
            "- If the user spoke in English, user_transcript, native_text, and romanized_text must all be in English (and romanized_text must be identical to native_text).\n"
            "- 'native_text' and 'romanized_text' must feature the identical amount of words.\n"
            "- Punctuation flags and grammatical cadence between 'native_text' and 'romanized_text' must match directly.\n"
            "- Only return valid JSON. Do not write markdown tags (like ```json ... ```) or any extra conversational filler."
        )

        turns = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": "<|audio|>"}
        ]

        loop = asyncio.get_event_loop()
        raw_output = await loop.run_in_executor(
            None,
            _run_ultravox_inference,
            pipe,
            audio_np,
            turns
        )

        output_text = _extract_text_from_output(raw_output)
        payload = _parse_json_response(output_text)

        if payload and "user_transcript" in payload and "native_text" in payload and "romanized_text" in payload:
            return payload

        # Fallback if structure is malformed
        logger.warning("[Chat-Audio] Output was not in valid schema: %r", output_text)
        return {
            "user_transcript": "User speech detected",
            "native_text": output_text,
            "romanized_text": output_text
        }

    except Exception as exc:
        logger.exception("[Chat-Audio] Inference failed: %s", exc)
        return get_mock_response("")


def _run_ultravox_inference(pipe, audio: np.ndarray, turns: List[Dict[str, str]]) -> str:
    """Executes Ultravox pipeline synchronously in the executor thread pool."""
    try:
        return pipe(
            {'audio': audio, 'turns': turns, 'sampling_rate': 16000},
            max_new_tokens=256
        )
    except Exception as e:
        logger.warning("[Chat-Audio] System role execution failed, merging system prompt: %s", e)
        system_content = next((t["content"] for t in turns if t["role"] == "system"), "")
        user_content = next((t["content"] for t in turns if t["role"] == "user"), "")
        merged_turns = [
            {"role": "user", "content": f"{system_content}\n\n{user_content}"}
        ]
        return pipe(
            {'audio': audio, 'turns': merged_turns, 'sampling_rate': 16000},
            max_new_tokens=256
        )


# ---------------------------------------------------------------------------
# Text-Only Chat Generation (for /chat HTTP endpoint)
# ---------------------------------------------------------------------------

async def get_response(user_text: str) -> Dict[str, str]:
    """
    Text-only conversational interface using the same Ultravox model
    (falling back to Llama-3.1 text-only mode).
    """
    if not user_text.strip():
        return {"native_text": "", "romanized_text": ""}

    pipe = await load_chat_model()
    if pipe is None:
        logger.info("[Chat-Text] Model not active, using Mock Responder")
        return get_mock_response(user_text)

    try:
        system_prompt = (
            "You are a helpful multilingual talking avatar assistant.\n"
            "Respond to the user's message in the user's language.\n"
            "Format your output as a JSON object containing exactly two keys:\n"
            "1. 'native_text': Your response in the native script (e.g. Tamil, Hindi, Telugu, or English).\n"
            "2. 'romanized_text': The phonetically equivalent Romanized transliteration of the native text.\n\n"
            "CRITICAL RULES:\n"
            "- If the user query is in English, both native_text and romanized_text must be the identical English sentence.\n"
            "- Both sentences must feature the identical amount of words.\n"
            "- Punctuation flags and grammatical cadence must match directly.\n"
            "- Splitting both strings by spaces must yield a perfectly mirrored array mapping index.\n"
            "- Only return valid JSON. Do not write markdown tags (like ```json ... ```) or any extra conversational filler."
        )

        turns = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_text}
        ]

        loop = asyncio.get_event_loop()
        raw_output = await loop.run_in_executor(
            None,
            _run_ultravox_text_inference,
            pipe,
            turns
        )

        output_text = _extract_text_from_output(raw_output)
        payload = _parse_json_response(output_text)

        if payload and "native_text" in payload and "romanized_text" in payload:
            return payload

        # Fallback if structure is malformed
        return {
            "native_text": output_text,
            "romanized_text": output_text
        }

    except Exception as exc:
        logger.exception("[Chat-Text] Text inference failed: %s", exc)
        return get_mock_response(user_text)


def _run_ultravox_text_inference(pipe, turns: List[Dict[str, str]]) -> str:
    """Executes Ultravox text-only generation synchronously."""
    try:
        return pipe(
            {'turns': turns},
            max_new_tokens=256
        )
    except Exception as e:
        logger.warning("[Chat-Text] System role execution failed, merging system prompt: %s", e)
        system_content = next((t["content"] for t in turns if t["role"] == "system"), "")
        user_content = next((t["content"] for t in turns if t["role"] == "user"), "")
        merged_turns = [
            {"role": "user", "content": f"{system_content}\n\n{user_content}"}
        ]
        return pipe(
            {'turns': merged_turns},
            max_new_tokens=256
        )
