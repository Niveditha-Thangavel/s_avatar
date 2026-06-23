"""
sentence_buffer.py — Smart text fragmentation for streaming STT

Detects sentence boundaries in real-time text chunks and emits complete
sentences without waiting for the full utterance. Supports Indian language
sentence terminators (।, |) alongside standard English punctuation.
"""

import asyncio
import re
import logging

logger = logging.getLogger(__name__)

_SENTENCE_BOUNDARY = re.compile(
    r'(?<=[.?!।|])'     # Zero-width lookbehind — split AFTER these chars
    r'(?:\s+|$)'         # Followed by whitespace or end-of-string
)

FLUSH_TIMEOUT = 0.8  # seconds — flush buffer if no new text within this window


class SentenceBuffer:
    """
    Accumulates streaming transcript fragments and emits complete sentences.

    Usage:
      buf = SentenceBuffer()
      buf.push("Hello world.")
      buf.push(" How are you?")
      sentence = await buf.get()  # "Hello world."
      sentence = await buf.get()  # "How are you?"

    Sentence delimiters: `.`, `?`, `!`, `|`, `।` (Devanagari danda)

    When a delimiter is detected the completed text is emitted immediately.
    If no delimiter arrives within `flush_timeout` seconds, the buffer
    auto-flushes so partial utterances don't stall the pipeline.
    """

    def __init__(self, flush_timeout: float = FLUSH_TIMEOUT):
        self._buffer = []
        self._flush_timeout = flush_timeout
        self._flush_timer_handle = None
        self._queue: asyncio.Queue = asyncio.Queue()
        self._finalized = False

    def push(self, chunk: str):
        """Feed a text chunk from STT into the buffer."""
        if self._finalized:
            return
        if not chunk or not chunk.strip():
            return

        self._buffer.append(chunk.strip())
        accumulated = " ".join(self._buffer)

        # Split on sentence boundaries
        parts = _SENTENCE_BOUNDARY.split(accumulated)

        # Everything except the last part is a complete sentence
        for part in parts[:-1]:
            cleaned = part.strip()
            if cleaned:
                logger.debug("[SentenceBuf] emit: %s", cleaned[:60])
                self._queue.put_nowait(cleaned)

        # Keep the remaining (incomplete) fragment in buffer
        remainder = parts[-1].strip() if parts else ""
        self._buffer = [remainder] if remainder else []

        self._reset_flush_timer()

    def finalize(self):
        """Signal end-of-stream. Flushes any remaining buffered text."""
        self._finalized = True
        if self._flush_timer_handle:
            self._flush_timer_handle.cancel()
            self._flush_timer_handle = None
        remainder = " ".join(self._buffer).strip()
        if remainder:
            logger.debug("[SentenceBuf] flush final: %s", remainder[:60])
            self._queue.put_nowait(remainder)
        self._buffer = []

    async def get(self) -> str:
        """Await the next complete sentence (blocks until available)."""
        sentence = await self._queue.get()
        self._queue.task_done()
        return sentence

    def get_nowait(self) -> str:
        """Non-blocking: raise asyncio.QueueEmpty if no sentence ready."""
        sentence = self._queue.get_nowait()
        self._queue.task_done()
        return sentence

    def has_ready(self) -> bool:
        """Check if at least one sentence is available without blocking."""
        return not self._queue.empty()

    def close(self):
        """Release resources. Further push() calls are no-ops."""
        self.finalize()

    def _reset_flush_timer(self):
        if self._flush_timer_handle:
            self._flush_timer_handle.cancel()
        loop = asyncio.get_event_loop()
        self._flush_timer_handle = loop.call_later(
            self._flush_timeout, self._on_flush_timeout
        )

    def _on_flush_timeout(self):
        """Auto-flush: if no data arrived for FLUSH_TIMEOUT, emit whatever we have."""
        remainder = " ".join(self._buffer).strip()
        if remainder:
            logger.debug("[SentenceBuf] timeout flush: %s", remainder[:60])
            self._queue.put_nowait(remainder)
        self._buffer = []
