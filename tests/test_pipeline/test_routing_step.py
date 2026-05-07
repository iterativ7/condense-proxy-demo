"""Tests for RoutingStep."""

import pytest
from condense.config.schema import CondenseConfig
from condense.pipeline.context import PipelineContext
from condense.pipeline.steps.routing_step import RoutingStep


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


class TestRoutingStep:
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
