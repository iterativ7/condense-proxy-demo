"""Exact-cache storage backend implementations.

Importing this package auto-registers all built-in storage backends.
"""

from condense.cache.storage import memory_backend  # noqa: F401
from condense.cache.storage import redis_backend  # noqa: F401
