"""Tests for in-memory cache backend."""

import pytest
import time
from unittest.mock import patch
from condense.cache.memory import InMemoryCache


@pytest.fixture
def cache():
    return InMemoryCache(max_size=5, default_ttl=60)


class TestInMemoryCache:
    @pytest.mark.asyncio
    async def test_get_miss(self, cache):
        """Cache miss returns None."""
        result = await cache.get("nonexistent")
        assert result is None

    @pytest.mark.asyncio
    async def test_set_and_get(self, cache):
        """Set and retrieve a value."""
        await cache.set("key1", {"data": "value1"})
        result = await cache.get("key1")
        assert result == {"data": "value1"}

    @pytest.mark.asyncio
    async def test_size(self, cache):
        """Size tracks number of entries."""
        assert await cache.size() == 0
        await cache.set("key1", {"a": 1})
        assert await cache.size() == 1
        await cache.set("key2", {"b": 2})
        assert await cache.size() == 2

    @pytest.mark.asyncio
    async def test_delete(self, cache):
        """Delete removes an entry."""
        await cache.set("key1", {"data": "value"})
        await cache.delete("key1")
        assert await cache.get("key1") is None
        assert await cache.size() == 0

    @pytest.mark.asyncio
    async def test_clear(self, cache):
        """Clear removes all entries."""
        await cache.set("key1", {"a": 1})
        await cache.set("key2", {"b": 2})
        await cache.clear()
        assert await cache.size() == 0

    @pytest.mark.asyncio
    async def test_lru_eviction(self, cache):
        """Oldest entries are evicted when max_size is reached."""
        for i in range(6):  # max_size is 5
            await cache.set(f"key{i}", {"i": i})

        # key0 should have been evicted
        assert await cache.get("key0") is None
        # key5 should exist
        assert await cache.get("key5") == {"i": 5}
        assert await cache.size() == 5

    @pytest.mark.asyncio
    async def test_ttl_expiration(self, cache):
        """Entries expire after TTL."""
        await cache.set("key1", {"data": "value"}, ttl=1)

        # Should exist immediately
        assert await cache.get("key1") is not None

        # Simulate time passing
        cache._ttls["key1"] = time.time() - 1

        # Should be expired
        assert await cache.get("key1") is None

    @pytest.mark.asyncio
    async def test_lru_access_updates_order(self, cache):
        """Accessing an entry moves it to the end (most recently used)."""
        await cache.set("key1", {"a": 1})
        await cache.set("key2", {"b": 2})
        await cache.set("key3", {"c": 3})

        # Access key1 to make it most recently used
        await cache.get("key1")

        # Fill up cache to trigger eviction
        await cache.set("key4", {"d": 4})
        await cache.set("key5", {"e": 5})
        await cache.set("key6", {"f": 6})

        # key2 should be evicted (least recently used), not key1
        assert await cache.get("key2") is None
        assert await cache.get("key1") is not None

    @pytest.mark.asyncio
    async def test_overwrite_existing_key(self, cache):
        """Setting an existing key updates its value."""
        await cache.set("key1", {"version": 1})
        await cache.set("key1", {"version": 2})
        result = await cache.get("key1")
        assert result == {"version": 2}
        assert await cache.size() == 1
