"""Rule-based model routing step.

Routes simple requests to cheaper models based on configurable rules.
"""

import logging
from condense.config.schema import RoutingRule
from condense.pipeline.context import PipelineContext
from condense.pipeline.result import StepResult
from condense.pipeline.steps.base import BaseStep
from condense.routing.rules import evaluate_rules

logger = logging.getLogger(__name__)


class RoutingStep(BaseStep):
    """Evaluate routing rules and swap model if a rule matches."""
    name = "routing"
    reads = frozenset({"request:model", "request:messages", "request:tools"})
    writes = frozenset({"request:model", "routing_state"})

    async def execute(self, ctx: PipelineContext) -> StepResult:
        rules_config = self.config.get("rules", [])
        if not rules_config:
            return StepResult(action="next")

        # Parse rules
        rules = [
            RoutingRule(**r) if isinstance(r, dict) else r
            for r in rules_config
        ]

        original_model = ctx.request.get("model", "")
        ctx.original_model = original_model

        target_model = evaluate_rules(ctx.request, rules)

        if target_model and target_model != original_model:
            logger.info(f"Routing: {original_model} → {target_model}")
            ctx.request["model"] = target_model
            ctx.routed_model = target_model
            return StepResult(action="next", technique="routing")

        return StepResult(action="next")
