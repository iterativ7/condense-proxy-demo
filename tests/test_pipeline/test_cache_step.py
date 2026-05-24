"""Tests for CacheStep with strategy-based architecture."""

import pytest
from condense.config.schema import CondenseConfig
from condense.pipeline.context import PipelineContext
from condense.pipeline.steps.cache_step import CacheStep, _strategy_cache


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


def _exact_config(**overrides):
    """Build a config dict with exact strategy enabled."""
    cfg = {
        "strategies": {
            "exact": {
                "enabled": True,
                "backend": "memory",
                "max_size": 100,
                "ttl_seconds": 60,
                "non_deterministic": "skip",
                **overrides,
            },
        },
    }
    return cfg


@pytest.fixture(autouse=True)
def clear_strategy_cache():
    _strategy_cache.clear()
    yield
    _strategy_cache.clear()


class TestCacheStep:
    @pytest.mark.asyncio
    async def test_cache_miss(self):
        """Cache miss returns next action."""
        step = CacheStep(_exact_config())
        ctx = make_ctx()
        result = await step.execute(ctx)
        assert result.action == "next"

    @pytest.mark.asyncio
    async def test_cache_hit(self):
        """Cache hit short-circuits with cached response."""
        step = CacheStep(_exact_config())
        ctx = make_ctx()

        # Prime the cache via the strategy's store method
        cached_response = {"choices": [{"message": {"content": "Hi"}}]}
        await step.strategies[0].store(ctx.request, cached_response, ctx.cache_namespace)

        result = await step.execute(ctx)
        assert result.action == "short_circuit"
        assert result.response == cached_response
        assert result.technique == "exact_cache"

    @pytest.mark.asyncio
    async def test_skip_non_deterministic(self):
        """Non-deterministic requests are skipped when policy is 'skip'."""
        step = CacheStep(_exact_config(non_deterministic="skip"))
        ctx = make_ctx(request={
            "model": "gpt-4o",
            "messages": [{"role": "user", "content": "Hello"}],
            "temperature": 0.7,
        })
        result = await step.execute(ctx)
        assert result.action == "next"

    @pytest.mark.asyncio
    async def test_allow_non_deterministic(self):
        """Non-deterministic requests are cached when policy is 'allow'."""
        step = CacheStep(_exact_config(non_deterministic="allow"))
        ctx = make_ctx(request={
            "model": "gpt-4o",
            "messages": [{"role": "user", "content": "Hello"}],
            "temperature": 0.7,
        })
        result = await step.execute(ctx)
        assert result.action == "next"

    @pytest.mark.asyncio
    async def test_tenant_isolation(self):
        """Different namespaces produce different cache entries."""
        step = CacheStep(_exact_config())

        # Store for tenant A
        ctx_a = make_ctx(namespace="tenant-a")
        await step.strategies[0].store(
            ctx_a.request, {"tenant": "a"}, "tenant-a"
        )

        # Tenant B should miss
        ctx_b = make_ctx(namespace="tenant-b")
        result = await step.execute(ctx_b)
        assert result.action == "next"

        # Tenant A should hit
        result = await step.execute(ctx_a)
        assert result.action == "short_circuit"

    @pytest.mark.asyncio
    async def test_no_strategies(self):
        """No strategies available — passes through."""
        step = CacheStep({"strategies": {}})
        ctx = make_ctx()
        result = await step.execute(ctx)
        assert result.action == "next"

    @pytest.mark.asyncio
    async def test_store_response(self):
        """store_response stores in all enabled strategies."""
        step = CacheStep(_exact_config())
        request = {
            "model": "gpt-4o",
            "messages": [{"role": "user", "content": "Test store"}],
            "temperature": 0,
        }
        response = {
            "choices": [{"message": {"content": "Stored!"}}],
            "usage": {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8},
        }

        await step.store_response(request, response, "ns")

        # Should be retrievable via lookup
        ctx = make_ctx(request=request, namespace="ns")
        result = await step.execute(ctx)
        assert result.action == "short_circuit"
        assert result.response == response

    @pytest.mark.asyncio
    async def test_cache_hit_tracks_savings(self):
        """Cache hit should report tokens saved from usage."""
        step = CacheStep(_exact_config())
        request = {
            "model": "gpt-4o",
            "messages": [{"role": "user", "content": "Savings test"}],
            "temperature": 0,
        }
        response = {
            "choices": [{"message": {"content": "ok"}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        }

        await step.store_response(request, response, "test")
        ctx = make_ctx(request=request)
        result = await step.execute(ctx)

        assert result.action == "short_circuit"
        assert result.tokens_saved == 15

    @pytest.mark.asyncio
    async def test_backward_compat_legacy_config(self):
        """Old-style config (no 'strategies' key) should still work."""
        legacy_config = {
            "enabled": True,
            "exact": {
                "enabled": True,
                "backend": "memory",
                "max_size": 100,
                "ttl_seconds": 60,
            },
            "non_deterministic": "skip",
        }
        step = CacheStep(legacy_config)
        ctx = make_ctx()
        result = await step.execute(ctx)
        # Should not crash — falls back to treating the whole config as exact
        assert result.action == "next"
