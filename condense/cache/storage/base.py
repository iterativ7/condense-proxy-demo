"""Storage backend registry for exact-match caching.

Storage backends provide the key-value persistence layer used by
``ExactCacheStrategy``.
"""

from condense.backends.registry import BackendRegistry
from condense.cache.base import CacheBackend

# Re-export CacheBackend so storage backends only need one import.
__all__ = ["CacheBackend", "storage_registry"]

storage_registry: BackendRegistry[CacheBackend] = BackendRegistry("cache_storage")
