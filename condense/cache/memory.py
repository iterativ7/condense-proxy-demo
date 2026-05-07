"""In-memory cache backend with LRU eviction and TTL support."""

import time
from collections import OrderedDict
from typing import Optional

from condense.cache.base import CacheBackend


class InMemoryCache(CacheBackend):
    """LRU + TTL in-memory cache using OrderedDict."""

    def __init__(self, max_size: int = 10000, default_ttl: int = 3600):
        self._store: OrderedDict[str, dict] = OrderedDict()
        self._ttls: dict[str, float] = {}
        self._max_size = max_size
        self._default_ttl = default_ttl

    async def get(self, key: str) -> Optional[dict]:
        if key not in self._store:
            return None

        # Check TTL
        if key in self._ttls and time.time() > self._ttls[key]:
            del self._store[key]
            del self._ttls[key]
            return None

        # Move to end (most recently used)
        self._store.move_to_end(key)
        return self._store[key]

    async def set(self, key: str, value: dict, ttl: Optional[int] = None) -> None:
        # If key exists, remove it first (to update position)
        if key in self._store:
            del self._store[key]

        # Evict LRU entries if at capacity
        while len(self._store) >= self._max_size:
            evicted_key, _ = self._store.popitem(last=False)
            self._ttls.pop(evicted_key, None)

        self._store[key] = value
        ttl_seconds = ttl if ttl is not None else self._default_ttl
        if ttl_seconds > 0:
            self._ttls[key] = time.time() + ttl_seconds

    async def delete(self, key: str) -> None:
        self._store.pop(key, None)
        self._ttls.pop(key, None)

    async def size(self) -> int:
        return len(self._store)

    async def clear(self) -> None:
        self._store.clear()
        self._ttls.clear()
