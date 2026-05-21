"""Abstract base class for compression backends.

Every compression backend must implement this interface.  New backends
self-register via the ``compression_registry`` decorator.

Example::

    from condense.compression.base import CompressionBackend, compression_registry

    @compression_registry.register("my_compressor")
    class MyCompressor(CompressionBackend):
        def __init__(self, **kwargs):
            ...

        @property
        def available(self) -> bool:
            return self._engine is not None

        def compress_messages(self, messages: list[dict]) -> CompressResult:
            ...
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Optional

from condense.backends.registry import BackendRegistry


@dataclass
class CompressResult:
    """Result of a compression operation.

    Attributes
    ----------
    messages : list[dict]
        Compressed messages (same schema as input — role + content).
    original_tokens : int
        Token count before compression (must be >= 0).
    compressed_tokens : int
        Token count after compression (must be >= 0).
    reduction_pct : float
        Percentage of tokens removed (0.0–100.0).
    stats : dict
        Backend-specific statistics (per-stage info, warnings, etc.).
    """

    messages: list[dict[str, Any]]
    original_tokens: int = 0
    compressed_tokens: int = 0
    reduction_pct: float = 0.0
    stats: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        self.original_tokens = max(0, self.original_tokens)
        self.compressed_tokens = max(0, self.compressed_tokens)
        self.reduction_pct = max(0.0, min(100.0, self.reduction_pct))


class CompressionBackend(ABC):
    """Contract that every compression backend must fulfil."""

    @property
    @abstractmethod
    def available(self) -> bool:
        """Whether the backend loaded successfully and can compress."""
        ...

    @abstractmethod
    def compress_messages(self, messages: list[dict[str, Any]]) -> CompressResult:
        """Compress a list of OpenAI-format chat messages.

        Parameters
        ----------
        messages :
            List of dicts with ``role`` and ``content`` keys.

        Returns
        -------
        CompressResult
            The compressed messages and statistics.
        """
        ...


# Singleton registry — backends register themselves at import time.
compression_registry: BackendRegistry[CompressionBackend] = BackendRegistry("compression")
