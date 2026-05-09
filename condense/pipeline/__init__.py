"""Pipeline factory — builds the optimization pipeline from config."""

from typing import Optional

from condense.config.schema import OptimizationEntry
from condense.pipeline.executor import PipelineExecutor
from condense.pipeline.steps.cache_step import CacheStep
from condense.pipeline.steps.provider_cache_step import ProviderCacheStep
from condense.pipeline.steps.routing_step import RoutingStep
from condense.pipeline.steps.budget_step import BudgetStep
from condense.pipeline.steps.forward_step import ForwardStep

import httpx

from condense.cache.base import CacheBackend
from condense.session.store import SessionStore


def _materialize_step(
    entry: OptimizationEntry,
    cache_backend: Optional[CacheBackend],
    session_store: Optional[SessionStore],
):
    if entry.type == "cache":
        if cache_backend is None:
            raise ValueError("Cache optimization enabled but no cache backend is configured")
        return CacheStep(entry.config, cache_backend)
    if entry.type == "provider_cache":
        return ProviderCacheStep(entry.config)
    if entry.type == "routing":
        return RoutingStep(entry.config)
    if entry.type == "budget":
        if session_store is None:
            raise ValueError("Budget optimization enabled but no session store is configured")
        return BudgetStep(entry.config, session_store)
    raise ValueError(f"Unsupported optimization type: {entry.type!r}")


def build_pipeline(
    config,
    cache_backend: Optional[CacheBackend],
    session_store: Optional[SessionStore],
    http_client: httpx.AsyncClient,
) -> PipelineExecutor:
    """Build optimization pipeline from config. Only enabled steps are included."""
    steps = []
    enabled_entries = [entry for entry in config.optimizations if entry.enabled]
    optimization_ids = [entry.id for entry in enabled_entries]

    for entry in enabled_entries:
        step = _materialize_step(entry, cache_backend, session_store)
        step.optimization_id = entry.id
        step.depends_on = tuple(entry.depends_on)
        step.execution_stage = entry.stage
        if entry.parallelizable is not None:
            step.supports_parallel = entry.parallelizable
        steps.append(step)

    forward = ForwardStep(config.upstream.model_dump(), http_client)
    forward.optimization_id = "forward"
    forward.depends_on = tuple(optimization_ids)
    steps.append(forward)

    return PipelineExecutor(steps)
