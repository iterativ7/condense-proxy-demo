"""Tests for Redis cache backend (using mocks)."""

import pytest
import json
from unittest.mock import AsyncMock, MagicMock
from condense.cache.redis_backend import RedisCache


@pytest.fixture
def mock_redis():
    redis = AsyncMock()
    return redis


@pytest.fixture
def redis_cache(mock_redis):
    return RedisCache(mock_redis, default_ttl=3600, key_prefix="test:")


class TestRedisCache:
    @pytest.mark.asyncio
    async def test_get_hit(self, redis_cache, mock_redis):
        """Get returns cached data on hit."""
        mock_redis.get.return_value = json.dumps({"data": "value"})
        result = await redis_cache.get("key1")
        assert result == {"data": "value"}
        mock_redis.get.assert_called_once_with("test:key1")

    @pytest.mark.asyncio
    async def test_get_miss(self, redis_cache, mock_redis):
        """Get returns None on miss."""
        mock_redis.get.return_value = None
        result = await redis_cache.get("key1")
        assert result is None

    @pytest.mark.asyncio
    async def test_set_with_ttl(self, redis_cache, mock_redis):
        """Set stores data with TTL."""
        await redis_cache.set("key1", {"data": "value"}, ttl=120)
        mock_redis.setex.assert_called_once_with(
            "test:key1", 120, json.dumps({"data": "value"})
        )

    @pytest.mark.asyncio
    async def test_set_default_ttl(self, redis_cache, mock_redis):
        """Set uses default TTL when none specified."""
        await redis_cache.set("key1", {"data": "value"})
        mock_redis.setex.assert_called_once_with(
            "test:key1", 3600, json.dumps({"data": "value"})
        )

    @pytest.mark.asyncio
    async def test_delete(self, redis_cache, mock_redis):
        """Delete removes a key."""
        await redis_cache.delete("key1")
        mock_redis.delete.assert_called_once_with("test:key1")

    @pytest.mark.asyncio
    async def test_get_error_returns_none(self, redis_cache, mock_redis):
        """Redis errors on GET return None (failsafe)."""
        mock_redis.get.side_effect = Exception("Connection lost")
        result = await redis_cache.get("key1")
        assert result is None
