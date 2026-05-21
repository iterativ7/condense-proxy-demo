"""FusionEngine backend (claw-compactor).

14-stage compression pipeline optimised for code (15–42%), JSON, logs,
and diffs.  Works entirely offline — no API keys or models to download.

Install: ``pip install claw-compactor``
"""

from __future__ import annotations

import logging
from typing import Any

from condense.compression.base import CompressionBackend, CompressResult, compression_registry

logger = logging.getLogger(__name__)


@compression_registry.register("fusion")
class FusionCompressionBackend(CompressionBackend):
    """Compress messages via claw-compactor's FusionEngine."""

    def __init__(self, *, aggressive: bool = True, enable_rewind: bool = False, **kwargs):
        self.aggressive = aggressive
        self.enable_rewind = enable_rewind
        self._engine = self._load()

    @property
    def available(self) -> bool:
        return self._engine is not None

    def _load(self) -> Any:
        try:
            from claw_compactor.fusion.engine import FusionEngine  # type: ignore[import-untyped]

            engine = FusionEngine(
                enable_rewind=self.enable_rewind,
                aggressive=self.aggressive,
            )
            logger.info(
                "FusionEngine loaded (aggressive=%s, rewind=%s)",
                self.aggressive,
                self.enable_rewind,
            )
            return engine
        except ImportError:
            logger.debug("claw-compactor not installed")
            return None
        except Exception as exc:
            logger.warning("FusionEngine load failed: %s", exc)
            return None

    def compress_messages(self, messages: list[dict[str, Any]]) -> CompressResult:
        if self._engine is None:
            return CompressResult(messages=messages)

        try:
            result = self._engine.compress_messages(messages)
            compressed_msgs = result.get("messages", messages)
            stats = result.get("stats", {})
            original_tokens = stats.get("original_tokens", 0)
            compressed_tokens = stats.get("compressed_tokens", 0)
            reduction_pct = stats.get("reduction_pct", 0.0)

            logger.debug(
                "[fusion] %d → %d tokens (%.1f%% reduction)",
                original_tokens,
                compressed_tokens,
                reduction_pct,
            )
            return CompressResult(
                messages=compressed_msgs,
                original_tokens=original_tokens,
                compressed_tokens=compressed_tokens,
                reduction_pct=reduction_pct,
                stats=stats,
            )
        except Exception as exc:
            logger.warning("[fusion] compression failed, using original: %s", exc)
            return CompressResult(messages=messages)
