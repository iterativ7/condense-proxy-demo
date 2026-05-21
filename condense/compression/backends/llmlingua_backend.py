"""LLMLingua backend (Microsoft).

ML-based prompt compression using perplexity-based token importance
scoring.  Best for natural language context windows.  Runs offline
with a local BERT model (~500 MB downloaded on first use).

Install: ``pip install llmlingua``
"""

from __future__ import annotations

import logging
from typing import Any

from condense.compression.base import CompressionBackend, CompressResult, compression_registry

logger = logging.getLogger(__name__)


@compression_registry.register("llmlingua")
class LLMLinguaCompressionBackend(CompressionBackend):
    """Compress messages via Microsoft LLMLingua-2."""

    def __init__(
        self,
        *,
        model_name: str = "microsoft/llmlingua-2-bert-base-multilingual-cased-meetingbank",
        rate: float = 0.5,
        use_llmlingua2: bool = True,
        device: str = "cpu",
        **kwargs,
    ):
        self.model_name = model_name
        self.rate = rate
        self.use_llmlingua2 = use_llmlingua2
        self.device = device
        self._compressor = self._load()

    @property
    def available(self) -> bool:
        return self._compressor is not None

    def _load(self) -> Any:
        try:
            from llmlingua import PromptCompressor  # type: ignore[import-untyped]

            compressor = PromptCompressor(
                model_name=self.model_name,
                use_llmlingua2=self.use_llmlingua2,
                device_map=self.device,
            )
            logger.info(
                "LLMLingua loaded (model=%s, rate=%.2f, v2=%s)",
                self.model_name,
                self.rate,
                self.use_llmlingua2,
            )
            return compressor
        except ImportError:
            logger.debug("llmlingua not installed")
            return None
        except Exception as exc:
            logger.warning("LLMLingua load failed: %s", exc)
            return None

    def _extract_content(self, msg: dict[str, Any]) -> str:
        """Extract text content from a message, handling structured blocks."""
        content = msg.get("content", "")
        if isinstance(content, list):
            text_parts = [
                b.get("text", "")
                for b in content
                if isinstance(b, dict) and b.get("type") == "text"
            ]
            return " ".join(text_parts)
        return str(content) if content else ""

    def _find_compressible_messages(
        self, messages: list[dict[str, Any]]
    ) -> list[int]:
        """Return indices of user messages that should be compressed.

        System messages are kept verbatim (they're typically short
        instructions).  Assistant messages are kept verbatim (they
        preserve conversation context the model expects).
        Only user messages are compressed — they contain the context
        and prompts where token savings matter.
        """
        indices = []
        for i, msg in enumerate(messages):
            role = msg.get("role", "")
            content = self._extract_content(msg)
            if role == "user" and content.strip():
                indices.append(i)
        return indices

    def compress_messages(self, messages: list[dict[str, Any]]) -> CompressResult:
        if self._compressor is None:
            return CompressResult(messages=messages)

        try:
            compressible = self._find_compressible_messages(messages)
            if not compressible:
                return CompressResult(messages=messages)

            # Collect all user-message text for compression
            user_texts = [
                self._extract_content(messages[i]) for i in compressible
            ]

            result = self._compressor.compress_prompt(
                context=user_texts,
                instruction="",
                question="",
                rate=self.rate,
            )

            compressed_text = result.get("compressed_prompt", "")
            original_tokens = result.get("origin_tokens", 0)
            compressed_tokens = result.get("compressed_tokens", 0)

            # ratio may be a string like "2.4x" — parse it
            raw_ratio = result.get("ratio", 1.0)
            if isinstance(raw_ratio, str):
                raw_ratio = float(raw_ratio.rstrip("x") or "1")
            ratio = float(raw_ratio) if raw_ratio else 1.0

            if original_tokens > 0 and compressed_tokens > 0:
                reduction_pct = (1.0 - compressed_tokens / original_tokens) * 100
            else:
                reduction_pct = 0.0

            # Rebuild messages: preserve structure, only replace user content.
            # If there are multiple user messages we replace the *last* one
            # with the full compressed output and remove earlier user messages
            # (LLMLingua compresses all context into a single output).
            compressed_messages = []
            last_user_idx = compressible[-1]
            skip_indices = set(compressible[:-1])  # earlier user msgs absorbed

            for i, msg in enumerate(messages):
                if i in skip_indices:
                    continue  # absorbed into compressed output
                if i == last_user_idx:
                    compressed_messages.append(
                        {"role": "user", "content": compressed_text}
                    )
                else:
                    compressed_messages.append(msg.copy())

            logger.debug(
                "[llmlingua] %d → %d tokens (%.1f%% reduction, %.1fx ratio)",
                original_tokens,
                compressed_tokens,
                reduction_pct,
                ratio,
            )
            return CompressResult(
                messages=compressed_messages,
                original_tokens=original_tokens,
                compressed_tokens=compressed_tokens,
                reduction_pct=reduction_pct,
                stats={"ratio": ratio, "compressed_prompt": compressed_text},
            )
        except Exception as exc:
            logger.warning("[llmlingua] compression failed, using original: %s", exc)
            return CompressResult(messages=messages)
