"""Token counting utilities."""

import logging
from typing import Optional

logger = logging.getLogger(__name__)

_encoder = None


def _get_encoder():
    """Lazily load tiktoken encoder."""
    global _encoder
    if _encoder is None:
        try:
            import tiktoken
            _encoder = tiktoken.get_encoding("cl100k_base")
        except ImportError:
            logger.warning("tiktoken not available, using character-based estimation")
            _encoder = False  # Sentinel: tried and failed
    return _encoder


def count_tokens(text: str, model: Optional[str] = None) -> int:
    """Count tokens in a text string.

    Uses tiktoken if available, falls back to character-based estimation (1 token ≈ 4 chars).
    """
    encoder = _get_encoder()
    if encoder and encoder is not False:
        try:
            return len(encoder.encode(text))
        except Exception:
            pass
    # Fallback: ~4 chars per token
    return max(1, len(text) // 4)


def count_message_tokens(messages: list, model: Optional[str] = None) -> int:
    """Count total tokens across a list of chat messages."""
    total = 0
    for msg in messages:
        # Each message has overhead (~4 tokens for role, separators)
        total += 4
        content = msg.get("content", "")
        if isinstance(content, str):
            total += count_tokens(content, model)
        elif isinstance(content, list):
            # Multi-modal content blocks
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    total += count_tokens(block.get("text", ""), model)
    return total
