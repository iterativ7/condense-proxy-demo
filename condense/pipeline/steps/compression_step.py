"""Prompt compression step.

Compresses messages before forwarding to the LLM, reducing token count
and therefore cost.  Supports multiple backends via the compression
registry (FusionEngine, LLMLingua, etc.).
"""

import logging
from typing import Optional

from condense.compression.compressor import Compressor
from condense.pipeline.context import PipelineContext
from condense.pipeline.result import StepResult
from condense.pipeline.steps.base import BaseStep

logger = logging.getLogger(__name__)

# Lazy-initialized compressor instance, cached per config signature.
_compressor_cache: dict[str, Compressor] = {}


def _get_or_create_compressor(config: dict) -> Compressor:
    """Return a cached Compressor for the given config."""
    compressor_type = config.get("compressor_type", "fusion")

    # Build a cache key from the config
    cache_parts = [compressor_type]
    for sub_key in ("fusion", "llmlingua"):
        sub = config.get(sub_key, {})
        if sub:
            cache_parts.append(f"{sub_key}:{sorted(sub.items())}")
    cache_key = "|".join(cache_parts)

    if cache_key not in _compressor_cache:
        # Extract backend-specific kwargs
        backend_kwargs = config.get(compressor_type, {})
        _compressor_cache[cache_key] = Compressor(
            compressor_type=compressor_type,
            **backend_kwargs,
        )
    return _compressor_cache[cache_key]


class CompressionStep(BaseStep):
    """Compress request messages to reduce token count.

    Runs before the forward step.  On compression, updates the request
    messages in-place and tracks token savings.
    """

    name = "compression"
    reads = frozenset({"request:messages"})
    writes = frozenset({"request:messages", "compression_state"})

    async def execute(self, ctx: PipelineContext) -> StepResult:
        compressor = _get_or_create_compressor(self.config)

        if not compressor.available:
            return StepResult(action="next")

        messages = ctx.request.get("messages", [])
        if not messages:
            return StepResult(action="next")

        result = compressor.compress_messages(messages)

        if result.reduction_pct > 0:
            ctx.request["messages"] = result.messages
            ctx.original_tokens = result.original_tokens
            ctx.optimized_tokens = result.compressed_tokens

            # Store compression stats in metadata for response headers
            ctx.metadata["compression_stats"] = {
                "original_tokens": result.original_tokens,
                "compressed_tokens": result.compressed_tokens,
                "reduction_pct": result.reduction_pct,
            }

            logger.info(
                "Compression: %d → %d tokens (%.1f%% saved)",
                result.original_tokens,
                result.compressed_tokens,
                result.reduction_pct,
            )
            return StepResult(action="next", technique="compression")

        return StepResult(action="next")
