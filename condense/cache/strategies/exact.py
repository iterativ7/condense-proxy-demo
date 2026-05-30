"""Exact-match cache strategy.

Uses SHA-256 hashing of request parameters for deterministic cache
lookup.  Supports pluggable storage backends via the storage registry.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from condense.cache.key import compute_cache_key
from condense.cache.storage.base import storage_registry
from condense.cache.strategies.base import CacheHit, CacheStrategy, cache_strategy_registry

# Trigger auto-registration of built-in storage backends.
import condense.cache.storage  # noqa: F401

logger = logging.getLogger(__name__)


@cache_strategy_registry.register("exact")
class ExactCacheStrategy(CacheStrategy):
    """Deterministic exact-match cache.

    Computes a SHA-256 hash of the request parameters and uses a
    key-value storage backend (memory, Redis, etc.) for persistence.

    Parameters (from config dict)
    ----------
    backend : str
        Storage backend name (default: "memory").
    max_size : int
        Maximum entries for in-memory backends (default: 10000).
    ttl_seconds : int
        Time-to-live in seconds (default: 3600).
    non_deterministic : str
        How to handle non-deterministic requests: "skip", "allow", "normalize".
    """

    def __init__(self, *, config: dict, **kwargs):
        self._config = config
        backend_name = config.get("backend", "memory")
        backend_cls = storage_registry.get(backend_name)

        if backend_cls is None:
            available = ", ".join(storage_registry.available_names()) or "(none)"
            logger.warning(
                "Unknown cache storage backend %r. Available: %s",
                backend_name,
                available,
            )
            self._storage = None
        else:
            self._storage = backend_cls(
                max_size=config.get("max_size", 10000),
                ttl_seconds=config.get("ttl_seconds", 3600),
                url=config.get("url", "redis://localhost:6379"),
                key_prefix=config.get("key_prefix", "condense:"),
            )

        self._non_deterministic = config.get("non_deterministic", "skip")
        self._ttl_seconds = config.get("ttl_seconds", 3600)

    @property
    def strategy_name(self) -> str:
        return "exact"

    @property
    def available(self) -> bool:
        if self._storage is None:
            return False
        # RedisStorage has an .available property; InMemoryStorage is always available
        if hasattr(self._storage, "available"):
            return self._storage.available
        return True

    def _should_skip(self, request: dict) -> Optional[str]:
        """Check if we should skip caching for this request.

        Returns a reason string if skipping, or None if we should proceed.
        """
        temperature = request.get("temperature")
        if self._non_deterministic == "skip" and temperature is not None and temperature > 0:
            return "non_deterministic_skipped"
        return None

    async def lookup(
        self,
        request: dict[str, Any],
        namespace: str = "",
    ) -> Optional[CacheHit]:
        if not self.available:
            return None

        skip_reason = self._should_skip(request)
        if skip_reason:
            return None

        # Compute key
        request_for_key = request.copy()
        if self._non_deterministic == "normalize":
            request_for_key.pop("temperature", None)

        cache_key = compute_cache_key(request_for_key, namespace)
        cached = await self._storage.get(cache_key)

        if cached is not None:
            logger.info("Exact cache HIT: %s...", cache_key[:16])
            estimated_cost = float(cached.get("_condense_estimated_cost", 0.0))

            # Extract tokens for savings tracking
            usage = cached.get("usage", {}) if isinstance(cached, dict) else {}
            tokens_saved = 0
            if isinstance(usage, dict):
                tokens_saved = int(usage.get("total_tokens", 0))
                if tokens_saved == 0:
                    tokens_saved = int(usage.get("prompt_tokens", 0)) + int(
                        usage.get("completion_tokens", 0)
                    )

            return CacheHit(
                response=cached,
                strategy_name="exact",
                estimated_cost=estimated_cost,
                tokens_saved=tokens_saved,
                metadata={
                    "cache_key_prefix": cache_key[:16],
                    "cache_key": cache_key,
                },
            )

        logger.debug("Exact cache MISS: %s...", cache_key[:16])
        return None

    async def store(
        self,
        request: dict[str, Any],
        response: dict[str, Any],
        namespace: str = "",
    ) -> None:
        if not self.available:
            return

        skip_reason = self._should_skip(request)
        if skip_reason:
            return

        request_for_key = request.copy()
        if self._non_deterministic == "normalize":
            request_for_key.pop("temperature", None)

        cache_key = compute_cache_key(request_for_key, namespace)
        await self._storage.set(cache_key, response, ttl=self._ttl_seconds)

    async def clear(self) -> None:
        if self._storage:
            await self._storage.clear()

    async def size(self) -> int:
        if self._storage:
            return await self._storage.size()
        return 0
