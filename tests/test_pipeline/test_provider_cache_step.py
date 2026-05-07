"""Tests for ProviderCacheStep."""

import pytest
import copy
from condense.config.schema import CondenseConfig
from condense.pipeline.context import PipelineContext
from condense.pipeline.steps.provider_cache_step import ProviderCacheStep


def make_ctx(request=None):
    req = request or {
        "model": "claude-3-5-sonnet-20241022",
        "messages": [
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": "Hello"},
        ],
    }
    return PipelineContext(
        original_request=copy.deepcopy(req),
        request=req,
        config=CondenseConfig(),
    )


class TestProviderCacheStep:
    @pytest.mark.asyncio
    async def test_anthropic_injects_system_cache_control(self):
        """Anthropic models get cache_control on system prompt."""
        step = ProviderCacheStep({
            "enabled": True,
            "anthropic": {
                "inject_cache_control": True,
                "cache_system_prompt": True,
                "cache_tools": True,
            },
        })
        ctx = make_ctx()
        result = await step.execute(ctx)

        assert result.action == "next"
        assert result.technique == "provider_cache_anthropic"

        # System message should now have cache_control
        system_msg = ctx.request["messages"][0]
        assert isinstance(system_msg["content"], list)
        assert system_msg["content"][0]["cache_control"] == {"type": "ephemeral"}

    @pytest.mark.asyncio
    async def test_anthropic_injects_tools_cache_control(self):
        """Anthropic models get cache_control on last tool."""
        step = ProviderCacheStep({
            "enabled": True,
            "anthropic": {
                "inject_cache_control": True,
                "cache_system_prompt": True,
                "cache_tools": True,
            },
        })
        ctx = make_ctx(request={
            "model": "claude-3-5-sonnet-20241022",
            "messages": [{"role": "user", "content": "Hello"}],
            "tools": [
                {"type": "function", "function": {"name": "tool1"}},
                {"type": "function", "function": {"name": "tool2"}},
            ],
        })
        result = await step.execute(ctx)

        # Last tool should have cache_control
        last_tool = ctx.request["tools"][-1]
        assert last_tool["cache_control"] == {"type": "ephemeral"}

    @pytest.mark.asyncio
    async def test_openai_no_modification(self):
        """OpenAI models don't get modifications (handled automatically)."""
        step = ProviderCacheStep({
            "enabled": True,
            "anthropic": {"inject_cache_control": True, "cache_system_prompt": True, "cache_tools": True},
            "openai": {"enabled": True},
        })
        ctx = make_ctx(request={
            "model": "gpt-4o",
            "messages": [
                {"role": "system", "content": "You are helpful."},
                {"role": "user", "content": "Hello"},
            ],
        })
        original_messages = copy.deepcopy(ctx.request["messages"])
        result = await step.execute(ctx)

        assert result.action == "next"
        assert result.technique == "provider_cache_openai"
        # Messages should be unchanged
        assert ctx.request["messages"] == original_messages

    @pytest.mark.asyncio
    async def test_unknown_provider_no_action(self):
        """Unknown providers get no modifications."""
        step = ProviderCacheStep({"enabled": True})
        ctx = make_ctx(request={
            "model": "custom-model",
            "messages": [{"role": "user", "content": "Hello"}],
        })
        result = await step.execute(ctx)
        assert result.action == "next"
        assert result.technique is None
