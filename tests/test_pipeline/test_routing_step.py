"""Tests for RoutingStep — rule-based and model routing strategies."""

from unittest.mock import MagicMock, patch

import pytest
from condense.config.schema import CondenseConfig
from condense.pipeline.context import PipelineContext
from condense.pipeline.steps.routing_step import RoutingStep, _model_router_cache


def make_ctx(request=None):
    req = request or {
        "model": "gpt-4o",
        "messages": [{"role": "user", "content": "Hi"}],
    }
    return PipelineContext(
        original_request=req.copy(),
        request=req,
        config=CondenseConfig(),
    )


@pytest.fixture(autouse=True)
def clear_router_cache():
    """Clear the model router cache between tests."""
    _model_router_cache.clear()
    yield
    _model_router_cache.clear()


class TestRuleBasedRouting:
    """Tests for the existing rule-based routing strategy."""

    @pytest.mark.asyncio
    async def test_short_message_routing(self):
        """Short messages are routed to cheaper model."""
        step = RoutingStep({
            "enabled": True,
            "rules": [
                {"condition": "short_messages", "max_chars": 500, "model": "gpt-4o-mini"},
            ],
        })
        ctx = make_ctx()
        result = await step.execute(ctx)

        assert result.action == "next"
        assert result.technique == "routing"
        assert ctx.request["model"] == "gpt-4o-mini"
        assert ctx.routed_model == "gpt-4o-mini"
        assert ctx.original_model == "gpt-4o"

    @pytest.mark.asyncio
    async def test_long_message_no_routing(self):
        """Long messages are not routed."""
        step = RoutingStep({
            "enabled": True,
            "rules": [
                {"condition": "short_messages", "max_chars": 10, "model": "gpt-4o-mini"},
            ],
        })
        ctx = make_ctx(request={
            "model": "gpt-4o",
            "messages": [{"role": "user", "content": "A" * 100}],
        })
        result = await step.execute(ctx)

        assert result.action == "next"
        assert result.technique is None
        assert ctx.request["model"] == "gpt-4o"

    @pytest.mark.asyncio
    async def test_no_tools_routing(self):
        """Requests without tools are routed when rule matches."""
        step = RoutingStep({
            "enabled": True,
            "rules": [
                {"condition": "no_tools", "model": "gpt-4o-mini"},
            ],
        })
        ctx = make_ctx()
        result = await step.execute(ctx)

        assert result.action == "next"
        assert result.technique == "routing"
        assert ctx.request["model"] == "gpt-4o-mini"

    @pytest.mark.asyncio
    async def test_with_tools_no_routing(self):
        """Requests with tools don't match no_tools rule."""
        step = RoutingStep({
            "enabled": True,
            "rules": [
                {"condition": "no_tools", "model": "gpt-4o-mini"},
            ],
        })
        ctx = make_ctx(request={
            "model": "gpt-4o",
            "messages": [{"role": "user", "content": "Hello"}],
            "tools": [{"type": "function", "function": {"name": "search"}}],
        })
        result = await step.execute(ctx)

        assert result.action == "next"
        assert result.technique is None
        assert ctx.request["model"] == "gpt-4o"

    @pytest.mark.asyncio
    async def test_no_rules(self):
        """No rules means no routing."""
        step = RoutingStep({"enabled": True, "rules": []})
        ctx = make_ctx()
        result = await step.execute(ctx)
        assert result.action == "next"
        assert result.technique is None


class TestModelRouting:
    """Tests for the ML-based model routing strategy."""

    def _mock_model_router(self, route_return: str | None):
        """Create a mock ModelRouter that returns the given model."""
        mock_router = MagicMock()
        mock_router.available = route_return is not None
        mock_router.route.return_value = route_return
        return mock_router

    @pytest.mark.asyncio
    async def test_model_routing_routes_to_weak(self):
        """Model routing swaps to weak model when router decides."""
        mock_router = self._mock_model_router("gpt-4o-mini")

        with patch(
            "condense.pipeline.steps.routing_step._get_or_create_model_router",
            return_value=mock_router,
        ):
            step = RoutingStep({
                "enabled": True,
                "model_routing": {
                    "enabled": True,
                    "strong": "gpt-4o",
                    "weak": "gpt-4o-mini",
                    "router_type": "smallest_llm",
                },
            })
            ctx = make_ctx()
            result = await step.execute(ctx)

        assert result.action == "next"
        assert result.technique == "routing"
        assert ctx.request["model"] == "gpt-4o-mini"
        assert ctx.routed_model == "gpt-4o-mini"
        assert ctx.original_model == "gpt-4o"

    @pytest.mark.asyncio
    async def test_model_routing_keeps_model_when_same(self):
        """No routing when router returns the same model as original."""
        mock_router = self._mock_model_router("gpt-4o")

        with patch(
            "condense.pipeline.steps.routing_step._get_or_create_model_router",
            return_value=mock_router,
        ):
            step = RoutingStep({
                "enabled": True,
                "model_routing": {
                    "enabled": True,
                    "strong": "gpt-4o",
                    "weak": "gpt-4o-mini",
                    "router_type": "largest_llm",
                },
            })
            ctx = make_ctx()
            result = await step.execute(ctx)

        assert result.action == "next"
        assert result.technique is None
        assert ctx.request["model"] == "gpt-4o"

    @pytest.mark.asyncio
    async def test_model_routing_returns_none_falls_through_to_rules(self):
        """When model routing returns None, rule-based routing is used as fallback."""
        mock_router = self._mock_model_router(None)
        mock_router.available = True

        with patch(
            "condense.pipeline.steps.routing_step._get_or_create_model_router",
            return_value=mock_router,
        ):
            step = RoutingStep({
                "enabled": True,
                "model_routing": {
                    "enabled": True,
                    "strong": "gpt-4o",
                    "weak": "gpt-4o-mini",
                    "router_type": "smallest_llm",
                },
                "rules": [
                    {"condition": "short_messages", "max_chars": 500, "model": "gpt-4o-mini"},
                ],
            })
            ctx = make_ctx()
            result = await step.execute(ctx)

        assert result.action == "next"
        assert result.technique == "routing"
        assert ctx.request["model"] == "gpt-4o-mini"

    @pytest.mark.asyncio
    async def test_model_routing_disabled_uses_rules(self):
        """When model_routing.enabled is false, only rules are evaluated."""
        step = RoutingStep({
            "enabled": True,
            "model_routing": {
                "enabled": False,
                "strong": "gpt-4o",
                "weak": "gpt-4o-mini",
            },
            "rules": [
                {"condition": "short_messages", "max_chars": 500, "model": "gpt-4o-mini"},
            ],
        })
        ctx = make_ctx()
        result = await step.execute(ctx)

        assert result.action == "next"
        assert result.technique == "routing"
        assert ctx.request["model"] == "gpt-4o-mini"

    @pytest.mark.asyncio
    async def test_model_routing_unavailable_falls_through(self):
        """When LLMRouter is unavailable, rules are used as fallback."""
        mock_router = MagicMock()
        mock_router.available = False

        with patch(
            "condense.pipeline.steps.routing_step._get_or_create_model_router",
            return_value=mock_router,
        ):
            step = RoutingStep({
                "enabled": True,
                "model_routing": {
                    "enabled": True,
                    "strong": "gpt-4o",
                    "weak": "gpt-4o-mini",
                },
                "rules": [
                    {"condition": "no_tools", "model": "gpt-4o-mini"},
                ],
            })
            ctx = make_ctx()
            result = await step.execute(ctx)

        assert result.action == "next"
        assert result.technique == "routing"
        assert ctx.request["model"] == "gpt-4o-mini"

    @pytest.mark.asyncio
    async def test_model_routing_wins_over_rules(self):
        """Model routing takes priority — rules are not evaluated when it decides."""
        mock_router = self._mock_model_router("claude-3-haiku")

        with patch(
            "condense.pipeline.steps.routing_step._get_or_create_model_router",
            return_value=mock_router,
        ):
            step = RoutingStep({
                "enabled": True,
                "model_routing": {
                    "enabled": True,
                    "strong": "gpt-4o",
                    "weak": "claude-3-haiku",
                    "router_type": "smallest_llm",
                },
                "rules": [
                    {"condition": "short_messages", "max_chars": 500, "model": "gpt-4o-mini"},
                ],
            })
            ctx = make_ctx()
            result = await step.execute(ctx)

        # Model routing should have won — routed to claude-3-haiku, not gpt-4o-mini
        assert result.technique == "routing"
        assert ctx.request["model"] == "claude-3-haiku"
        assert ctx.routed_model == "claude-3-haiku"

    @pytest.mark.asyncio
    async def test_no_strategies_configured(self):
        """No model_routing and no rules → no routing."""
        step = RoutingStep({"enabled": True})
        ctx = make_ctx()
        result = await step.execute(ctx)
        assert result.action == "next"
        assert result.technique is None
        assert ctx.request["model"] == "gpt-4o"

    @pytest.mark.asyncio
    async def test_original_model_always_tracked(self):
        """original_model is set regardless of whether routing happens."""
        step = RoutingStep({"enabled": True})
        ctx = make_ctx()
        await step.execute(ctx)
        assert ctx.original_model == "gpt-4o"
