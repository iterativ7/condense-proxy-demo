"""Tests for ForwardStep."""

import pytest
import httpx
from condense.config.schema import CondenseConfig
from condense.pipeline.context import PipelineContext
from condense.pipeline.steps.forward_step import ForwardStep


def make_ctx(request=None):
    req = request or {
        "model": "gpt-4o",
        "messages": [{"role": "user", "content": "Hello"}],
    }
    return PipelineContext(
        original_request=req.copy(),
        request=req,
        config=CondenseConfig(),
        metadata={},
    )


class TestForwardStep:
    @pytest.mark.asyncio
    async def test_successful_forward(self, monkeypatch):
        """Successful upstream response is returned."""
        response_data = {
            "id": "chatcmpl-123",
            "choices": [{"message": {"content": "Hi"}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        }

        async def fake_acompletion(**kwargs):
            return response_data

        monkeypatch.setattr(
            "condense.pipeline.steps.forward_step.litellm.acompletion",
            fake_acompletion,
        )

        async with httpx.AsyncClient() as client:
            step = ForwardStep({
                "url": "https://api.openai.com/v1",
                "timeout_seconds": 30,
            }, client)
            ctx = make_ctx()
            result = await step.execute(ctx)

        assert result.action == "short_circuit"
        assert result.status_code == 200
        assert result.response["id"] == "chatcmpl-123"

    @pytest.mark.asyncio
    async def test_upstream_error_passthrough(self, monkeypatch):
        """Upstream 4xx/5xx errors are passed through."""

        class FakeAuthError(Exception):
            status_code = 401

        async def fake_acompletion(**kwargs):
            raise FakeAuthError("Invalid API key")

        monkeypatch.setattr(
            "condense.pipeline.steps.forward_step.litellm.acompletion",
            fake_acompletion,
        )

        async with httpx.AsyncClient() as client:
            step = ForwardStep({
                "url": "https://api.openai.com/v1",
                "timeout_seconds": 30,
            }, client)
            ctx = make_ctx()
            result = await step.execute(ctx)

        assert result.action == "short_circuit"
        assert result.status_code == 401
        assert "Invalid API key" in result.response["error"]["message"]

    @pytest.mark.asyncio
    async def test_cost_estimation(self, monkeypatch):
        """Cost estimation is computed and stored."""
        response_data = {
            "id": "chatcmpl-123",
            "choices": [{"message": {"content": "Hi"}}],
            "usage": {"prompt_tokens": 1000, "completion_tokens": 500, "total_tokens": 1500},
        }

        async def fake_acompletion(**kwargs):
            return response_data

        monkeypatch.setattr(
            "condense.pipeline.steps.forward_step.litellm.acompletion",
            fake_acompletion,
        )

        async with httpx.AsyncClient() as client:
            step = ForwardStep({
                "url": "https://api.openai.com/v1",
                "timeout_seconds": 30,
            }, client)
            ctx = make_ctx()
            result = await step.execute(ctx)

        assert result.response.get("_condense_estimated_cost", 0) > 0
        assert ctx.metadata.get("estimated_cost", 0) > 0
