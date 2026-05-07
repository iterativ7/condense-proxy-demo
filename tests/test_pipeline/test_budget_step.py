"""Tests for BudgetStep."""

import pytest
from condense.config.schema import CondenseConfig
from condense.pipeline.context import PipelineContext
from condense.pipeline.steps.budget_step import BudgetStep
from condense.session.store import SessionStore


def make_ctx(session_id="test-session"):
    return PipelineContext(
        original_request={"model": "gpt-4o", "messages": []},
        request={"model": "gpt-4o", "messages": []},
        config=CondenseConfig(),
        session_id=session_id,
    )


@pytest.fixture
def store():
    return SessionStore()


class TestBudgetStep:
    @pytest.mark.asyncio
    async def test_under_budget_passes(self, store):
        """Requests under budget pass through."""
        step = BudgetStep({
            "enabled": True,
            "max_session_cost_usd": 10.0,
            "max_turns_per_session": 100,
            "loop_detection_window": 5,
        }, store)
        ctx = make_ctx()
        result = await step.execute(ctx)
        assert result.action == "next"

    @pytest.mark.asyncio
    async def test_turn_limit_exceeded(self, store):
        """Exceeding turn limit rejects the request."""
        step = BudgetStep({
            "enabled": True,
            "max_session_cost_usd": 10.0,
            "max_turns_per_session": 5,
            "loop_detection_window": 5,
        }, store)

        session = await store.get_or_create("test-session")
        session.turn_count = 5

        ctx = make_ctx()
        result = await step.execute(ctx)
        assert result.action == "reject"
        assert result.status_code == 429
        assert "turn limit" in result.error.lower()

    @pytest.mark.asyncio
    async def test_cost_limit_exceeded(self, store):
        """Exceeding cost limit rejects the request."""
        step = BudgetStep({
            "enabled": True,
            "max_session_cost_usd": 1.0,
            "max_turns_per_session": 100,
            "loop_detection_window": 5,
        }, store)

        session = await store.get_or_create("test-session")
        session.total_cost_usd = 1.5

        ctx = make_ctx()
        result = await step.execute(ctx)
        assert result.action == "reject"
        assert result.status_code == 429

    @pytest.mark.asyncio
    async def test_loop_detection(self, store):
        """Repeated identical requests trigger loop detection."""
        step = BudgetStep({
            "enabled": True,
            "max_session_cost_usd": 10.0,
            "max_turns_per_session": 100,
            "loop_detection_window": 3,
        }, store)

        session = await store.get_or_create("test-session")
        # Simulate 3 identical request hashes
        for _ in range(3):
            session.recent_request_hashes.append("same-hash")

        ctx = make_ctx()
        result = await step.execute(ctx)
        assert result.action == "reject"
        assert "loop" in result.error.lower()

    @pytest.mark.asyncio
    async def test_no_session_passes(self, store):
        """Requests without a session ID pass through."""
        step = BudgetStep({
            "enabled": True,
            "max_session_cost_usd": 10.0,
            "max_turns_per_session": 100,
            "loop_detection_window": 5,
        }, store)
        ctx = make_ctx(session_id=None)
        result = await step.execute(ctx)
        assert result.action == "next"
