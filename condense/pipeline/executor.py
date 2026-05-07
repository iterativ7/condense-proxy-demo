import logging
from typing import List
from condense.pipeline.steps.base import BaseStep
from condense.pipeline.context import PipelineContext
from condense.pipeline.result import StepResult

logger = logging.getLogger(__name__)


class PipelineExecutor:
    def __init__(self, steps: List[BaseStep]):
        self.steps = [s for s in steps if s.is_enabled()]

    async def execute(self, ctx: PipelineContext) -> StepResult:
        for step in self.steps:
            try:
                result = await step.execute(ctx)

                if result.action == "short_circuit":
                    if result.technique:
                        ctx.techniques_applied.append(result.technique)
                    ctx.total_savings_usd += result.savings_usd
                    return result

                if result.action == "reject":
                    return result

                # action == "next": accumulate and continue
                if result.technique:
                    ctx.techniques_applied.append(result.technique)
                ctx.total_savings_usd += result.savings_usd

            except Exception as e:
                # FAILSAFE: skip broken step, never block a request
                logger.error(f"Step {step.__class__.__name__} failed: {e}", exc_info=True)
                continue

        # All steps passed — should have been handled by ForwardStep
        # If we get here, something is wrong (ForwardStep missing?)
        return StepResult(action="reject", error="Pipeline completed without forwarding", status_code=500)
