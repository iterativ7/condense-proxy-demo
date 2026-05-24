"""Abstract base class for cache strategies.

Every cache strategy must implement this interface. New strategies
self-register via the ``cache_strategy_registry`` decorator.

Example::

    from condense.cache.strategies.base import CacheStrategy, cache_strategy_registry

    @cache_strategy_registry.register("my_strategy")
    class MyCacheStrategy(CacheStrategy):
        def __init__(self, *, config: dict, **kwargs):
            ...

        @property
        def available(self) -> bool:
            return True

        async def lookup(self, request, namespace) -> CacheHit | None:
            ...

        async def store(self, request, response, namespace) -> None:
            ...
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Optional

from condense.backends.registry import BackendRegistry


@dataclass
class CacheHit:
    """Result of a successful cache lookup.

    Attributes
    ----------
    response : dict
        The cached response to return to the client.
    strategy_name : str
        Name of the strategy that produced this hit (e.g. "exact", "semantic").
    similarity_score : float or None
        For semantic strategies, the cosine similarity score (0.0–1.0).
        None for exact-match hits.
    estimated_cost : float
        Estimated cost of the original request (for savings tracking).
    tokens_saved : int
        Estimated tokens saved by this cache hit.
    metadata : dict
        Strategy-specific metadata (cache key prefix, matched query, etc.).
    """

    response: dict[str, Any]
    strategy_name: str
    similarity_score: Optional[float] = None
    estimated_cost: float = 0.0
    tokens_saved: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)


class CacheStrategy(ABC):
    """Contract that every cache strategy must fulfil.

    Strategies are tried in order by the CacheStep. The first strategy
    that returns a ``CacheHit`` wins; remaining strategies are skipped.
    """

    @property
    @abstractmethod
    def strategy_name(self) -> str:
        """Short identifier for this strategy (e.g. 'exact', 'semantic')."""
        ...

    @property
    @abstractmethod
    def available(self) -> bool:
        """Whether the strategy loaded successfully and can operate."""
        ...

    @abstractmethod
    async def lookup(
        self,
        request: dict[str, Any],
        namespace: str = "",
    ) -> Optional[CacheHit]:
        """Look up a request in the cache.

        Returns a ``CacheHit`` if found, or ``None`` on miss.
        """
        ...

    @abstractmethod
    async def store(
        self,
        request: dict[str, Any],
        response: dict[str, Any],
        namespace: str = "",
    ) -> None:
        """Store a request/response pair in the cache."""
        ...

    async def clear(self) -> None:
        """Clear all entries managed by this strategy. Override if supported."""
        pass

    async def size(self) -> int:
        """Return number of entries. Override if supported."""
        return 0


# Singleton registry — strategies register themselves at import time.
cache_strategy_registry: BackendRegistry[CacheStrategy] = BackendRegistry("cache_strategy")
