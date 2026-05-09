"""Session-level budget enforcement step.

Enforces per-session cost caps, turn limits, and loop detection.
"""

import logging
from condense.pipeline.context import PipelineContext
from condense.pipeline.result import StepResult
from condense.pipeline.steps.base import BaseStep
from condense.session.store import SessionStore

logger = logging.getLogger(__name__)


class BudgetStep(BaseStep):
    """Check session budget constraints before forwarding."""
    name = "budget"
    reads = frozenset({"session_state", "metadata:estimated_cost"})

    def __init__(self, config: dict, session_store: SessionStore):
        super().__init__(config)
        self.session_store = session_store

    async def execute(self, ctx: PipelineContext) -> StepResult:
        if not ctx.session_id:
            return StepResult(action="next")

        session = await self.session_store.get_or_create(ctx.session_id)

        # Check turn limit
        max_turns = self.config.get("max_turns_per_session", 100)
        if session.turn_count >= max_turns:
            logger.warning(
                f"Session {ctx.session_id[:8]} exceeded turn limit "
                f"({session.turn_count}/{max_turns})"
            )
            return StepResult(
                action="reject",
                error=f"Session turn limit exceeded ({max_turns} turns)",
                status_code=429,
                technique="budget",
            )

        # Check cost cap
        max_cost = self.config.get("max_session_cost_usd", 10.0)
        if session.total_cost_usd >= max_cost:
            logger.warning(
                f"Session {ctx.session_id[:8]} exceeded cost limit "
                f"(${session.total_cost_usd:.2f}/${max_cost:.2f})"
            )
            return StepResult(
                action="reject",
                error=f"Session cost limit exceeded (${max_cost:.2f})",
                status_code=429,
                technique="budget",
            )

        # Loop detection
        loop_window = self.config.get("loop_detection_window", 5)
        if loop_window > 0 and len(session.recent_request_hashes) >= loop_window:
            recent = list(session.recent_request_hashes)[-loop_window:]
            # Detect loop: all recent hashes are the same
            if len(set(recent)) == 1:
                logger.warning(
                    f"Session {ctx.session_id[:8]} detected request loop "
                    f"(same request repeated {loop_window} times)"
                )
                return StepResult(
                    action="reject",
                    error=f"Request loop detected ({loop_window} identical requests)",
                    status_code=429,
                    technique="budget",
                )

        return StepResult(action="next", technique="budget")
