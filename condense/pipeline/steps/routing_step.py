"""Model routing step.

Supports two routing strategies that can be used independently or together:

1. **Model routing** (ML-based) — uses LLMRouter to intelligently pick
   strong vs weak models based on query complexity analysis.
2. **Rule-based routing** — static condition rules (short_messages, no_tools)
   that route to cheaper models when conditions match.

When both are configured, model routing runs first. If it doesn't produce
a routing decision, rule-based routing serves as a fallback.
"""

import logging
from typing import Optional

from condense.config.schema import ModelRoutingConfig, RoutingRule
from condense.pipeline.context import PipelineContext
from condense.pipeline.result import StepResult
from condense.pipeline.steps.base import BaseStep
from condense.routing.rules import evaluate_rules

logger = logging.getLogger(__name__)

# Lazy-initialized model router instance, cached per config signature.
_model_router_cache: dict[str, "ModelRouter"] = {}


def _get_or_create_model_router(mr_config: ModelRoutingConfig):
    """Return a cached ModelRouter for the given config, creating one if needed.

    ModelRouter initialization can be expensive (loading ML models), so we
    cache instances keyed by their configuration signature.
    """
    from condense.routing.model_router import ModelRouter

    cache_key = (
        f"{mr_config.strong}|{mr_config.weak}|{mr_config.threshold}|"
        f"{mr_config.router_type}|{mr_config.config_path}"
    )
    if cache_key not in _model_router_cache:
        _model_router_cache[cache_key] = ModelRouter(
            strong=mr_config.strong,
            weak=mr_config.weak,
            threshold=mr_config.threshold,
            router_type=mr_config.router_type,
            config_path=mr_config.config_path,
        )
    return _model_router_cache[cache_key]


class RoutingStep(BaseStep):
    """Evaluate routing strategies and swap model when a match is found.

    Strategies are evaluated in order:
    1. Model routing (ML-based, if configured and enabled)
    2. Rule-based routing (if rules are configured)

    The first strategy that produces a routing decision wins.
    """

    name = "routing"
    reads = frozenset({"request:model", "request:messages", "request:tools"})
    writes = frozenset({"request:model", "routing_state"})

    async def execute(self, ctx: PipelineContext) -> StepResult:
        original_model = ctx.request.get("model", "")
        ctx.original_model = original_model

        target_model: Optional[str] = None
        technique_detail: Optional[str] = None

        # --- Strategy 1: ML-based model routing ---
        mr_config_raw = self.config.get("model_routing")
        if mr_config_raw and isinstance(mr_config_raw, dict):
            mr_config = ModelRoutingConfig(**mr_config_raw)
            if mr_config.enabled:
                router = _get_or_create_model_router(mr_config)
                if router.available:
                    routed = router.route(ctx.request)
                    if routed and routed != original_model:
                        target_model = routed
                        technique_detail = "model_routing"
                        logger.info(
                            "Model routing: %s → %s (strategy=%s)",
                            original_model,
                            target_model,
                            mr_config.router_type,
                        )

        # --- Strategy 2: Rule-based routing (fallback) ---
        if target_model is None:
            rules_config = self.config.get("rules", [])
            if rules_config:
                rules = [
                    RoutingRule(**r) if isinstance(r, dict) else r
                    for r in rules_config
                ]
                routed = evaluate_rules(ctx.request, rules)
                if routed and routed != original_model:
                    target_model = routed
                    technique_detail = "rule_routing"
                    logger.info("Rule routing: %s → %s", original_model, target_model)

        # --- Apply routing decision ---
        if target_model:
            ctx.request["model"] = target_model
            ctx.routed_model = target_model
            return StepResult(
                action="next",
                technique="routing",
                savings_usd=0.0,
                tokens_saved=0,
                details={
                    "from_model": original_model,
                    "to_model": target_model,
                    "strategy": technique_detail,
                },
                optimization_updates=[
                    {
                        "technique": "routing",
                        "savings_usd": 0.0,
                        "tokens_saved": 0,
                        "details": {
                            "from_model": original_model,
                            "to_model": target_model,
                            "strategy": technique_detail,
                        },
                    }
                ],
            )

        return StepResult(
            action="next",
            savings_usd=0.0,
            tokens_saved=0,
            optimization_updates=[
                {
                    "technique": "routing",
                    "savings_usd": 0.0,
                    "tokens_saved": 0,
                    "details": {
                        "from_model": original_model,
                        "to_model": original_model,
                        "strategy": "none",
                    },
                }
            ],
        )
