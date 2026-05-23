"""Prompt compressor with pluggable backends.

Backends auto-register via the ``compression_registry`` — see
``condense/compression/backends/`` for built-in implementations.

Adding a new compression backend requires **zero changes** to this file.
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
    based on ``compressor_type``.

    Parameters
    ----------
    compressor_type : str
        Strategy name.  Must match a registered backend.
    kwargs
        Passed through to the backend constructor.
    """

    def __init__(self, compressor_type: str = "fusion", **kwargs):
        self.compressor_type = compressor_type

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
        return self._backend is not None and self._backend.available

    def compress_messages(self, messages: list[dict[str, Any]]) -> CompressResult:
        """Compress a list of OpenAI-format chat messages.

        Returns a ``CompressResult`` with compressed messages and stats.
        Falls back to returning original messages on any failure.
        """
        if not self.available:
            return CompressResult(messages=messages)

        try:
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
