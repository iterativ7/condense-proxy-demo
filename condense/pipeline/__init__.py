"""Pipeline factory — builds the optimization pipeline from config."""

from condense.pipeline.executor import PipelineExecutor
from condense.pipeline.steps.cache_step import CacheStep
from condense.pipeline.steps.provider_cache_step import ProviderCacheStep
from condense.pipeline.steps.routing_step import RoutingStep
from condense.pipeline.steps.budget_step import BudgetStep
from condense.pipeline.steps.forward_step import ForwardStep

import httpx

from condense.cache.base import CacheBackend
from condense.session.store import SessionStore


def build_pipeline(config, cache_backend: CacheBackend, session_store: SessionStore, http_client: httpx.AsyncClient) -> PipelineExecutor:
    """Build optimization pipeline from config. Only enabled steps are included."""
    steps = []
    opt = config.optimizations

    # Order: Cache (short-circuit) → Provider cache → Route → Budget → Forward
    if opt.cache.enabled:
        steps.append(CacheStep(opt.cache.model_dump(), cache_backend))

    if opt.provider_cache.enabled:
        steps.append(ProviderCacheStep(opt.provider_cache.model_dump()))

    if opt.routing.enabled:
        steps.append(RoutingStep(opt.routing.model_dump()))

    if opt.budget.enabled:
        steps.append(BudgetStep(opt.budget.model_dump(), session_store))

    # Always last
    steps.append(ForwardStep(config.upstream.model_dump(), http_client))

    return PipelineExecutor(steps)
