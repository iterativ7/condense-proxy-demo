"""Prompt compressor with pluggable backends and chain support.

Backends auto-register via the ``compression_registry`` — see
``condense/compression/backends/`` for built-in implementations.

Adding a new compression backend requires **zero changes** to this file.

Supports two modes:

1. **Single backend** (backward compatible)::

       compressor_type: "fusion"

2. **Chain** (P0-13 fix — multiple backends running back-to-back)::

       chain:
         - backend: "rtk"
           apply_to: ["tool"]
         - backend: "fusion"
           apply_to: ["user", "system"]
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from condense.compression.base import CompressResult, compression_registry

# Trigger auto-registration of built-in backends.
import condense.compression.backends  # noqa: F401

logger = logging.getLogger(__name__)


class Compressor:
    """Prompt compressor with pluggable backends.

    Automatically selects the appropriate backend from the registry
    based on ``compressor_type``, or builds a chain from ``chain`` config.

    Parameters
    ----------
    compressor_type : str
        Strategy name.  Must match a registered backend.  Ignored if
        ``chain`` is provided.
    chain : list[dict] | None
        Ordered list of backend configs for chained compression.
        Each entry has ``backend`` (str), optional ``apply_to`` (list[str]),
        and any backend-specific kwargs.
    kwargs
        Passed through to the single backend constructor.
    """

    def __init__(
        self,
        compressor_type: str = "fusion",
        *,
        chain: list[dict[str, Any]] | None = None,
        **kwargs,
    ):
        self.compressor_type = compressor_type
        self._backend = None
        self._chain = None

        if chain:
            # Chain mode — multiple backends running back-to-back
            from condense.compression.chain import CompressionChain

            self._chain = CompressionChain(chain)
            self.compressor_type = "chain"
            logger.info("[compressor] using compression chain (%d backends)", len(chain))
        else:
            # Single backend mode (backward compatible)
            ct_lower = compressor_type.replace("-", "_").lower()
            backend_cls = compression_registry.get(ct_lower)

            if backend_cls is None:
                available = ", ".join(compression_registry.available_names()) or "(none)"
                logger.warning(
                    "Unknown compression backend %r. Available: %s. "
                    "Compression disabled.",
                    compressor_type,
                    available,
                )
                self._backend = None
            else:
                self._backend = backend_cls(**kwargs)

            if self._backend is not None and not self._backend.available:
                logger.warning(
                    "Compression backend %r loaded but unavailable. "
                    "Check that the required library is installed.",
                    compressor_type,
                )

    @property
    def available(self) -> bool:
        """Whether the underlying compression backend loaded successfully."""
        if self._chain is not None:
            return self._chain.available
        return self._backend is not None and self._backend.available

    def compress_messages(self, messages: list[dict[str, Any]]) -> CompressResult:
        """Compress a list of OpenAI-format chat messages.

        Returns a ``CompressResult`` with compressed messages and stats.
        Falls back to returning original messages on any failure.
        """
        if not self.available:
            return CompressResult(messages=messages)

        try:
            if self._chain is not None:
                result = self._chain.compress_messages(messages)
            else:
                result = self._backend.compress_messages(messages)

            logger.debug(
                "[compressor] %d → %d tokens (%.1f%% reduction, backend=%s)",
                result.original_tokens,
                result.compressed_tokens,
                result.reduction_pct,
                self.compressor_type,
            )
            return result
        except Exception as exc:
            logger.warning(
                "[compressor] compression failed, keeping original: %s", exc
            )
            return CompressResult(messages=messages)
