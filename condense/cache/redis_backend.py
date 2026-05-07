"""Redis cache backend for production deployments."""

import json
import logging
from typing import Optional

from condense.cache.base import CacheBackend

logger = logging.getLogger(__name__)


class RedisCache(CacheBackend):
    """Redis-backed cache for exact-match caching."""

    def __init__(self, redis_client, default_ttl: int = 3600, key_prefix: str = "condense:"):
        self._redis = redis_client
        self._default_ttl = default_ttl
        self._key_prefix = key_prefix

    def _make_key(self, key: str) -> str:
        return f"{self._key_prefix}{key}"

    async def get(self, key: str) -> Optional[dict]:
        try:
            data = await self._redis.get(self._make_key(key))
            if data is None:
                return None
            return json.loads(data)
        except Exception as e:
            logger.error(f"Redis GET failed: {e}")
            return None

    async def set(self, key: str, value: dict, ttl: Optional[int] = None) -> None:
        try:
            ttl_seconds = ttl if ttl is not None else self._default_ttl
            serialized = json.dumps(value)
            if ttl_seconds > 0:
                await self._redis.setex(self._make_key(key), ttl_seconds, serialized)
            else:
                await self._redis.set(self._make_key(key), serialized)
        except Exception as e:
            logger.error(f"Redis SET failed: {e}")

    async def delete(self, key: str) -> None:
        try:
            await self._redis.delete(self._make_key(key))
        except Exception as e:
            logger.error(f"Redis DELETE failed: {e}")

    async def size(self) -> int:
        try:
            # Count keys with our prefix using SCAN
            count = 0
            cursor = 0
            while True:
                cursor, keys = await self._redis.scan(cursor, match=f"{self._key_prefix}*", count=100)
                count += len(keys)
                if cursor == 0:
                    break
            return count
        except Exception as e:
            logger.error(f"Redis SIZE failed: {e}")
            return 0

    async def clear(self) -> None:
        try:
            cursor = 0
            while True:
                cursor, keys = await self._redis.scan(cursor, match=f"{self._key_prefix}*", count=100)
                if keys:
                    await self._redis.delete(*keys)
                if cursor == 0:
                    break
        except Exception as e:
            logger.error(f"Redis CLEAR failed: {e}")
