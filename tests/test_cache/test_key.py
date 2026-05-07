"""Tests for cache key computation."""

import pytest
from condense.cache.key import compute_cache_key


class TestCacheKey:
    def test_deterministic(self):
        """Same request should always produce the same key."""
        request = {
            "model": "gpt-4o",
            "messages": [{"role": "user", "content": "Hello"}],
            "temperature": 0,
        }
        key1 = compute_cache_key(request)
        key2 = compute_cache_key(request)
        assert key1 == key2

    def test_different_messages_different_keys(self):
        """Different messages should produce different keys."""
        req1 = {"model": "gpt-4o", "messages": [{"role": "user", "content": "Hello"}]}
        req2 = {"model": "gpt-4o", "messages": [{"role": "user", "content": "Goodbye"}]}
        assert compute_cache_key(req1) != compute_cache_key(req2)

    def test_different_models_different_keys(self):
        """Different models should produce different keys."""
        req1 = {"model": "gpt-4o", "messages": [{"role": "user", "content": "Hello"}]}
        req2 = {"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "Hello"}]}
        assert compute_cache_key(req1) != compute_cache_key(req2)

    def test_namespace_isolation(self):
        """Different namespaces should produce different keys."""
        request = {"model": "gpt-4o", "messages": [{"role": "user", "content": "Hello"}]}
        key1 = compute_cache_key(request, namespace="tenant-a")
        key2 = compute_cache_key(request, namespace="tenant-b")
        assert key1 != key2
        assert key1.startswith("tenant-a:")
        assert key2.startswith("tenant-b:")

    def test_no_namespace(self):
        """Key without namespace should not have a prefix."""
        request = {"model": "gpt-4o", "messages": [{"role": "user", "content": "Hello"}]}
        key = compute_cache_key(request)
        assert ":" not in key  # No namespace prefix (just hex chars)

    def test_ignores_extra_params(self):
        """Non-cache params should be ignored."""
        req1 = {"model": "gpt-4o", "messages": [{"role": "user", "content": "Hello"}]}
        req2 = {"model": "gpt-4o", "messages": [{"role": "user", "content": "Hello"}], "stream": True}
        assert compute_cache_key(req1) == compute_cache_key(req2)

    def test_includes_tools_in_key(self):
        """Tools should be part of the cache key."""
        base = {"model": "gpt-4o", "messages": [{"role": "user", "content": "Hello"}]}
        with_tools = {
            **base,
            "tools": [{"type": "function", "function": {"name": "get_weather"}}],
        }
        assert compute_cache_key(base) != compute_cache_key(with_tools)

    def test_temperature_affects_key(self):
        """Different temperatures should produce different keys."""
        req1 = {"model": "gpt-4o", "messages": [{"role": "user", "content": "Hello"}], "temperature": 0}
        req2 = {"model": "gpt-4o", "messages": [{"role": "user", "content": "Hello"}], "temperature": 1}
        assert compute_cache_key(req1) != compute_cache_key(req2)

    def test_key_is_sha256(self):
        """Key should be a valid SHA-256 hex digest."""
        request = {"model": "gpt-4o", "messages": []}
        key = compute_cache_key(request)
        assert len(key) == 64
        assert all(c in "0123456789abcdef" for c in key)
