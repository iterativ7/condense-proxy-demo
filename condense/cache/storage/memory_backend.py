"""In-memory LRU + TTL storage backend.

Zero external dependencies — suitable for development and small-scale
deployments.

Install: included by default.
"""

import time
from collections import OrderedDict
from typing import Optional

from condense.cache.base import CacheBackend
from condense.cache.storage.base import storage_registry


@storage_registry.register("memory")
class InMemoryStorage(CacheBackend):
    """LRU + TTL in-memory cache using OrderedDict."""

    def __init__(self, *, max_size: int = 10000, ttl_seconds: int = 3600, **kwargs):
        self._store: OrderedDict[str, dict] = OrderedDict()
        self._ttls: dict[str, float] = {}
        self._max_size = max_size
        self._default_ttl = ttl_seconds

    async def get(self, key: str) -> Optional[dict]:
        if key not in self._store:
            return None
        if key in self._ttls and time.time() > self._ttls[key]:
            del self._store[key]
            del self._ttls[key]
            return None
        self._store.move_to_end(key)
        return self._store[key]

    async def set(self, key: str, value: dict, ttl: Optional[int] = None) -> None:
        if key in self._store:
            del self._store[key]
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
