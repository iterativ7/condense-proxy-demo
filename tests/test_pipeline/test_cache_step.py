"""Tests for CacheStep."""

import pytest
from condense.cache.memory import InMemoryCache
from condense.config.schema import CondenseConfig
from condense.pipeline.context import PipelineContext
from condense.pipeline.steps.cache_step import CacheStep


def make_ctx(request=None, namespace="test"):
    req = request or {
        "model": "gpt-4o",
        "messages": [{"role": "user", "content": "Hello"}],
        "temperature": 0,
    }
    return PipelineContext(
        original_request=req.copy(),
        request=req,
        config=CondenseConfig(),
        cache_namespace=namespace,
    )


@pytest.fixture
def cache():
    return InMemoryCache(max_size=100, default_ttl=60)


class TestCacheStep:
    @pytest.mark.asyncio
    async def test_cache_miss(self, cache):
        """Cache miss returns next action."""
        step = CacheStep({"enabled": True, "non_deterministic": "skip"}, cache)
        ctx = make_ctx()
        result = await step.execute(ctx)
        assert result.action == "next"

    @pytest.mark.asyncio
    async def test_cache_hit(self, cache):
        """Cache hit short-circuits with cached response."""
        step = CacheStep({"enabled": True, "non_deterministic": "skip"}, cache)
        ctx = make_ctx()

        # Prime the cache
        cached_response = {"choices": [{"message": {"content": "Hi"}}]}
        from condense.cache.key import compute_cache_key
        key = compute_cache_key(ctx.request, ctx.cache_namespace)
        await cache.set(key, cached_response)

        result = await step.execute(ctx)
        assert result.action == "short_circuit"
        assert result.response == cached_response
        assert result.technique == "exact_cache"

    @pytest.mark.asyncio
    async def test_skip_non_deterministic(self, cache):
        """Non-deterministic requests are skipped when policy is 'skip'."""
        step = CacheStep({"enabled": True, "non_deterministic": "skip"}, cache)
        ctx = make_ctx(request={
            "model": "gpt-4o",
            "messages": [{"role": "user", "content": "Hello"}],
            "temperature": 0.7,
        })
        result = await step.execute(ctx)
        assert result.action == "next"

    @pytest.mark.asyncio
    async def test_allow_non_deterministic(self, cache):
        """Non-deterministic requests are cached when policy is 'allow'."""
        step = CacheStep({"enabled": True, "non_deterministic": "allow"}, cache)
        ctx = make_ctx(request={
            "model": "gpt-4o",
            "messages": [{"role": "user", "content": "Hello"}],
            "temperature": 0.7,
        })
        # Should not skip — should check cache (miss in this case)
        result = await step.execute(ctx)
        assert result.action == "next"
        # Cache key should have been computed
        assert "cache_key" in ctx.metadata

    @pytest.mark.asyncio
    async def test_tenant_isolation(self, cache):
        """Different namespaces produce different cache entries."""
        step = CacheStep({"enabled": True, "non_deterministic": "skip"}, cache)

        # Store for tenant A
        ctx_a = make_ctx(namespace="tenant-a")
        from condense.cache.key import compute_cache_key
        key_a = compute_cache_key(ctx_a.request, "tenant-a")
        await cache.set(key_a, {"tenant": "a"})

        # Tenant B should miss
        ctx_b = make_ctx(namespace="tenant-b")
        result = await step.execute(ctx_b)
        assert result.action == "next"

        # Tenant A should hit
        result = await step.execute(ctx_a)
        assert result.action == "short_circuit"
