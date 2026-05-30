"""Multi-strategy cache step.

Tries each enabled cache strategy in order (e.g. exact → semantic).
The first strategy that returns a ``CacheHit`` wins and short-circuits
the pipeline.  After a successful upstream response, stores in all
enabled strategies.
"""

import logging
from typing import Optional

from condense.cache.strategies.base import (
    CacheHit,
    CacheStrategy,
    cache_strategy_registry,
)
from condense.pipeline.context import PipelineContext
from condense.pipeline.result import StepResult
from condense.pipeline.steps.base import BaseStep

# Trigger auto-registration of built-in strategies.
import condense.cache.strategies  # noqa: F401

logger = logging.getLogger(__name__)

# Cache strategy instances, keyed by config signature.
_strategy_cache: dict[str, list[CacheStrategy]] = {}


def _build_strategies(config: dict) -> list[CacheStrategy]:
    """Build and cache strategy instances from config.

    Strategies are created in a deterministic order: exact first,
    then semantic, then any others alphabetically.
    """
    import json

    cache_key = json.dumps(config, sort_keys=True, default=str)
    if cache_key in _strategy_cache:
        return _strategy_cache[cache_key]

    strategies_config = config.get("strategies", {})
    if not strategies_config:
        # Backward compatibility: old format had config.exact + config.non_deterministic
        exact_block = config.get("exact", {})
        if isinstance(exact_block, dict) and exact_block:
            merged = dict(exact_block)  # copy to avoid mutating original
            non_det = config.get("non_deterministic", "skip")
            merged.setdefault("non_deterministic", non_det)
            strategies_config = {"exact": merged}
        else:
            # Treat entire config as exact strategy config
            strategies_config = {"exact": dict(config)}

    # Priority order: exact first, semantic second, rest alphabetical
    priority = {"exact": 0, "semantic": 1}
    ordered_names = sorted(
        strategies_config.keys(),
        key=lambda n: (priority.get(n, 99), n),
    )

    built: list[CacheStrategy] = []
    for name in ordered_names:
        strat_config = strategies_config[name]
        if not isinstance(strat_config, dict):
            continue

        enabled = strat_config.get("enabled", True)
        if not enabled:
            logger.debug("Cache strategy %r disabled", name)
            continue

        cls = cache_strategy_registry.get(name)
        if cls is None:
            available = ", ".join(cache_strategy_registry.available_names())
            logger.warning(
                "Unknown cache strategy %r. Available: %s", name, available
            )
            continue

        try:
            strategy = cls(config=strat_config)
            if strategy.available:
                built.append(strategy)
                logger.info("Cache strategy %r loaded", name)
            else:
                logger.warning(
                    "Cache strategy %r loaded but unavailable "
                    "(missing dependencies?)",
                    name,
                )
        except Exception as exc:
            logger.warning("Failed to create cache strategy %r: %s", name, exc)

    _strategy_cache[cache_key] = built
    return built


class CacheStep(BaseStep):
    """Multi-strategy cache lookup and store.

    On the forward pass: tries each enabled strategy in order.
    On cache hit, short-circuits the pipeline.

    After a cache miss + successful upstream response, the response
    is stored in all enabled strategies (handled by the routes layer).
    """

    name = "cache"
    can_short_circuit = True
    reads = frozenset({"request"})
    writes = frozenset({"metadata:cache_key", "cache_state"})

    def __init__(self, config: dict, cache_backend=None):
        """Initialize CacheStep.

        Parameters
        ----------
        config : dict
            Cache configuration from YAML.
        cache_backend : CacheBackend or None
            Legacy parameter — kept for backward compatibility with
            existing pipeline factory. Ignored when strategies are
            configured; used as fallback storage for exact strategy
            when no strategies block exists.
        """
        super().__init__(config)
        self._legacy_backend = cache_backend
        self._strategies: Optional[list[CacheStrategy]] = None

    @property
    def strategies(self) -> list[CacheStrategy]:
        """Lazily build strategies on first access."""
        if self._strategies is None:
            self._strategies = _build_strategies(self.config)
        return self._strategies

    @staticmethod
    def _extract_total_tokens(payload: dict) -> int:
        usage = payload.get("usage", {}) if isinstance(payload, dict) else {}
        if not isinstance(usage, dict):
            return 0
        total_tokens = usage.get("total_tokens")
        if total_tokens is not None:
            return int(total_tokens)
        prompt_tokens = int(usage.get("prompt_tokens") or 0)
        completion_tokens = int(usage.get("completion_tokens") or 0)
        return prompt_tokens + completion_tokens

    async def execute(self, ctx: PipelineContext) -> StepResult:
        strategies = self.strategies

        # Store ref so routes layer can call store_response after upstream
        ctx.metadata["_cache_step"] = self

        if not strategies:
            # No strategies available — pass through
            return StepResult(
                action="next",
                optimization_updates=[{
                    "technique": "cache",
                    "savings_usd": 0.0,
                    "tokens_saved": 0,
                    "details": {"cache_hit": False, "reason": "no_strategies"},
                }],
            )

        # Try each strategy in order
        for strategy in strategies:
            try:
                hit = await strategy.lookup(ctx.request, ctx.cache_namespace)
            except Exception as exc:
                logger.warning(
                    "Cache strategy %r lookup failed: %s",
                    strategy.strategy_name,
                    exc,
                )
                continue

            if hit is not None:
                logger.info(
                    "Cache HIT via %r strategy",
                    hit.strategy_name,
                )
                ctx.cache_hit = True
                ctx.cache_hit_type = hit.strategy_name
                ctx.metadata["cache_key"] = hit.metadata.get("cache_key", "")

                return StepResult(
                    action="short_circuit",
                    response=hit.response,
                    technique=f"{hit.strategy_name}_cache",
                    savings_usd=hit.estimated_cost,
                    tokens_saved=hit.tokens_saved,
                    details={
                        "cache_hit": True,
                        "cache_type": hit.strategy_name,
                        "similarity_score": hit.similarity_score,
                        **hit.metadata,
                    },
                    optimization_updates=[{
                        "technique": f"{hit.strategy_name}_cache",
                        "savings_usd": hit.estimated_cost,
                        "tokens_saved": hit.tokens_saved,
                        "details": {
                            "cache_hit": True,
                            "cache_type": hit.strategy_name,
                            "similarity_score": hit.similarity_score,
                            "estimated_cost_reused": hit.estimated_cost,
                            **hit.metadata,
                        },
                    }],
                )

        # All strategies missed
        logger.debug("Cache MISS (all strategies)")
        return StepResult(
            action="next",
            technique="cache",
            savings_usd=0.0,
            tokens_saved=0,
            details={"cache_hit": False, "cache_type": "none"},
            optimization_updates=[{
                "technique": "cache",
                "savings_usd": 0.0,
                "tokens_saved": 0,
                "details": {"cache_hit": False, "cache_type": "none"},
            }],
        )

    async def store_response(
        self,
        request: dict,
        response: dict,
        namespace: str = "",
    ) -> None:
        """Store a response in all enabled strategies.

        Called by the routes layer after a successful upstream response.
        """
        for strategy in self.strategies:
            try:
                await strategy.store(request, response, namespace)
            except Exception as exc:
                logger.warning(
                    "Cache strategy %r store failed: %s",
                    strategy.strategy_name,
                    exc,
                )
