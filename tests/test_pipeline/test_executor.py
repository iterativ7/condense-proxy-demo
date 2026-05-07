"""Tests for PipelineExecutor."""

import pytest
from unittest.mock import AsyncMock

from condense.config.schema import CondenseConfig
from condense.pipeline.context import PipelineContext
from condense.pipeline.executor import PipelineExecutor
from condense.pipeline.result import StepResult
from condense.pipeline.steps.base import BaseStep


class MockStep(BaseStep):
    """A mock pipeline step for testing."""

    def __init__(self, result: StepResult, enabled: bool = True, should_raise: bool = False):
        super().__init__({"enabled": enabled})
        self._result = result
        self._should_raise = should_raise
        self.executed = False

    async def execute(self, ctx: PipelineContext) -> StepResult:
        if self._should_raise:
            raise RuntimeError("Step crashed!")
        self.executed = True
        return self._result


def make_ctx(request=None):
    return PipelineContext(
        original_request=request or {"model": "gpt-4o", "messages": []},
        request=request or {"model": "gpt-4o", "messages": []},
        config=CondenseConfig(),
    )


class TestPipelineExecutor:
    @pytest.mark.asyncio
    async def test_all_next_reaches_end(self):
        """If all steps return 'next', pipeline ends with reject (no ForwardStep)."""
        steps = [
            MockStep(StepResult(action="next")),
            MockStep(StepResult(action="next")),
        ]
        executor = PipelineExecutor(steps)
        result = await executor.execute(make_ctx())
        assert result.action == "reject"
        assert "without forwarding" in result.error

    @pytest.mark.asyncio
    async def test_short_circuit(self):
        """Short circuit stops pipeline and returns response."""
        steps = [
            MockStep(StepResult(action="short_circuit", response={"cached": True}, technique="cache")),
            MockStep(StepResult(action="next")),  # Should not execute
        ]
        executor = PipelineExecutor(steps)
        ctx = make_ctx()
        result = await executor.execute(ctx)

        assert result.action == "short_circuit"
        assert result.response == {"cached": True}
        assert "cache" in ctx.techniques_applied
        assert not steps[1].executed

    @pytest.mark.asyncio
    async def test_reject(self):
        """Reject stops pipeline and returns error."""
        steps = [
            MockStep(StepResult(action="next")),
            MockStep(StepResult(action="reject", error="Budget exceeded", status_code=429)),
        ]
        executor = PipelineExecutor(steps)
        result = await executor.execute(make_ctx())

        assert result.action == "reject"
        assert result.error == "Budget exceeded"
        assert result.status_code == 429

    @pytest.mark.asyncio
    async def test_disabled_steps_skipped(self):
        """Disabled steps are excluded from execution."""
        step1 = MockStep(StepResult(action="next"), enabled=True)
        step2 = MockStep(StepResult(action="reject", error="Should not run"), enabled=False)
        step3 = MockStep(StepResult(action="short_circuit", response={"ok": True}))

        executor = PipelineExecutor([step1, step2, step3])
        result = await executor.execute(make_ctx())

        assert result.action == "short_circuit"
        assert step1.executed
        assert not step2.executed

    @pytest.mark.asyncio
    async def test_failsafe_skip_broken_step(self):
        """Broken steps are skipped (failsafe), pipeline continues."""
        steps = [
            MockStep(StepResult(action="next"), should_raise=True),
            MockStep(StepResult(action="short_circuit", response={"ok": True})),
        ]
        executor = PipelineExecutor(steps)
        result = await executor.execute(make_ctx())

        assert result.action == "short_circuit"
        assert result.response == {"ok": True}

    @pytest.mark.asyncio
    async def test_techniques_accumulated(self):
        """Techniques from all steps are accumulated in context."""
        steps = [
            MockStep(StepResult(action="next", technique="cache_check")),
            MockStep(StepResult(action="next", technique="routing")),
            MockStep(StepResult(action="short_circuit", response={}, technique="forward")),
        ]
        executor = PipelineExecutor(steps)
        ctx = make_ctx()
        await executor.execute(ctx)

        assert ctx.techniques_applied == ["cache_check", "routing", "forward"]

    @pytest.mark.asyncio
    async def test_savings_accumulated(self):
        """Savings from all steps are accumulated in context."""
        steps = [
            MockStep(StepResult(action="next", savings_usd=0.01)),
            MockStep(StepResult(action="short_circuit", response={}, savings_usd=0.02)),
        ]
        executor = PipelineExecutor(steps)
        ctx = make_ctx()
        await executor.execute(ctx)

        assert abs(ctx.total_savings_usd - 0.03) < 1e-9
