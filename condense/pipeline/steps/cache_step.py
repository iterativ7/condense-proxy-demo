"""Exact-match cache lookup and store step."""

import logging
from condense.cache.base import CacheBackend
from condense.cache.key import compute_cache_key
from condense.pipeline.context import PipelineContext
from condense.pipeline.result import StepResult
from condense.pipeline.steps.base import BaseStep

logger = logging.getLogger(__name__)


class CacheStep(BaseStep):
    """Check exact-match cache before forwarding to upstream.

    On cache hit, short-circuits the pipeline and returns the cached response.
    After a cache miss + upstream response, the ForwardStep stores into cache
    via a background task — this step only handles lookup.
    """
    name = "cache"
    can_short_circuit = True
    reads = frozenset({"request"})
    writes = frozenset({"metadata:cache_key", "cache_state"})

    def __init__(self, config: dict, cache_backend: CacheBackend):
        super().__init__(config)
        self.cache = cache_backend

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
        # Check non-deterministic handling
        non_det = self.config.get("non_deterministic", "skip")
        temperature = ctx.request.get("temperature")

        if non_det == "skip" and temperature is not None and temperature > 0:
            logger.debug("Skipping cache for non-deterministic request (temperature > 0)")
            return StepResult(
                action="next",
                tokens_saved=0,
                optimization_updates=[
                    {
                        "technique": "exact_cache",
                        "savings_usd": 0.0,
                        "tokens_saved": 0,
                        "details": {
                            "cache_hit": False,
                            "reason": "non_deterministic_skipped",
                        },
                    }
                ],
            )

        # Compute cache key
        request_for_key = ctx.request.copy()
        if non_det == "normalize":
            # Exclude temperature from cache key
            request_for_key.pop("temperature", None)

        cache_key = compute_cache_key(request_for_key, ctx.cache_namespace)
        ctx.metadata["cache_key"] = cache_key

        # Look up
        cached = await self.cache.get(cache_key)
        if cached is not None:
            logger.info(f"Cache HIT: {cache_key[:16]}...")
            ctx.cache_hit = True
            ctx.cache_hit_type = "exact"
            estimated_cost = float(cached.get("_condense_estimated_cost", 0.0))
            tokens_saved = self._extract_total_tokens(cached)
            return StepResult(
                action="short_circuit",
                response=cached,
                technique="exact_cache",
                savings_usd=estimated_cost,
                tokens_saved=tokens_saved,
                details={
                    "cache_hit": True,
                    "cache_key_prefix": cache_key[:16],
                    "cache_type": "exact",
                },
                optimization_updates=[
                    {
                        "technique": "exact_cache",
                        "savings_usd": estimated_cost,
                        "tokens_saved": tokens_saved,
                        "details": {
                            "cache_hit": True,
                            "cache_key_prefix": cache_key[:16],
                            "cache_type": "exact",
                            "estimated_cost_reused": estimated_cost,
                        },
                    }
                ],
            )

        logger.debug(f"Cache MISS: {cache_key[:16]}...")
        return StepResult(
            action="next",
            technique="exact_cache",
            savings_usd=0.0,
            tokens_saved=0,
            details={
                "cache_hit": False,
                "cache_key_prefix": cache_key[:16],
                "cache_type": "exact",
            },
            optimization_updates=[
                {
                    "technique": "exact_cache",
                    "savings_usd": 0.0,
                    "tokens_saved": 0,
                    "details": {
                        "cache_hit": False,
                        "cache_key_prefix": cache_key[:16],
                        "cache_type": "exact",
                    },
                }
            ],
        )
