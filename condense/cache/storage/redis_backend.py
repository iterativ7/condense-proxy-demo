"""Redis storage backend for production deployments.

Install: ``pip install redis``
"""

import json
import logging
from typing import Optional

from condense.cache.base import CacheBackend
from condense.cache.storage.base import storage_registry

logger = logging.getLogger(__name__)


@storage_registry.register("redis")
class RedisStorage(CacheBackend):
    """Redis-backed key-value cache."""

    def __init__(
        self,
        *,
        redis_client=None,
        url: str = "redis://localhost:6379",
        ttl_seconds: int = 3600,
        key_prefix: str = "condense:",
        **kwargs,
    ):
        self._key_prefix = key_prefix
        self._default_ttl = ttl_seconds

        if redis_client is not None:
            self._redis = redis_client
        else:
            self._redis = self._connect(url)

    def _connect(self, url: str):
        try:
            import redis.asyncio as aioredis  # type: ignore[import-untyped]
            return aioredis.from_url(url, decode_responses=True)
        except ImportError:
            logger.warning("redis package not installed — RedisStorage unavailable")
            return None
        except Exception as exc:
            logger.warning("Redis connection failed: %s", exc)
            return None

    @property
    def available(self) -> bool:
        return self._redis is not None

    def _make_key(self, key: str) -> str:
        return f"{self._key_prefix}{key}"

    async def get(self, key: str) -> Optional[dict]:
        if self._redis is None:
            return None
        try:
            data = await self._redis.get(self._make_key(key))
            if data is None:
                return None
            return json.loads(data)
        except Exception as e:
            logger.error("Redis GET failed: %s", e)
            return None

    async def set(self, key: str, value: dict, ttl: Optional[int] = None) -> None:
        if self._redis is None:
            return
        try:
            ttl_seconds = ttl if ttl is not None else self._default_ttl
            serialized = json.dumps(value)
            if ttl_seconds > 0:
                await self._redis.setex(self._make_key(key), ttl_seconds, serialized)
            else:
                await self._redis.set(self._make_key(key), serialized)
        except Exception as e:
            logger.error("Redis SET failed: %s", e)

    async def delete(self, key: str) -> None:
        if self._redis is None:
            return
        try:
            await self._redis.delete(self._make_key(key))
        except Exception as e:
            logger.error("Redis DELETE failed: %s", e)

    async def size(self) -> int:
        if self._redis is None:
            return 0
        try:
            count = 0
            cursor = 0
            while True:
                cursor, keys = await self._redis.scan(
                    cursor, match=f"{self._key_prefix}*", count=100
                )
                count += len(keys)
                if cursor == 0:
                    break
            return count
        except Exception as e:
            logger.error("Redis SIZE failed: %s", e)
            return 0

    async def clear(self) -> None:
        if self._redis is None:
            return
        try:
            cursor = 0
            while True:
                cursor, keys = await self._redis.scan(
                    cursor, match=f"{self._key_prefix}*", count=100
                )
                if keys:
                    await self._redis.delete(*keys)
                if cursor == 0:
                    break
        except Exception as e:
            logger.error("Redis CLEAR failed: %s", e)
