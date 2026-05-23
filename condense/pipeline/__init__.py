"""Pipeline factory — builds the optimization pipeline from config.

Step types are resolved via a registry so new optimization types can be
added without editing this file.  Built-in types are registered below;
third-party types simply need to be imported before ``build_pipeline``
is called.
"""

from __future__ import annotations

import logging
from typing import Callable, Dict, Optional, Type

from condense.config.schema import OptimizationEntry
from condense.pipeline.executor import PipelineExecutor
from condense.pipeline.steps.base import BaseStep
from condense.pipeline.steps.forward_step import ForwardStep

import httpx

from condense.cache.base import CacheBackend
from condense.session.store import SessionStore

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Step registry
# ---------------------------------------------------------------------------

# Factory signature: (config, cache_backend, session_store) -> BaseStep
StepFactory = Callable[
    [dict, Optional[CacheBackend], Optional[SessionStore]],
    BaseStep,
]

_STEP_REGISTRY: Dict[str, StepFactory] = {}


def register_step_type(name: str) -> Callable[[StepFactory], StepFactory]:
    """Decorator that registers a step factory under *name*.

    Example::

        @register_step_type("my_step")
        def _make_my_step(config, cache_backend, session_store):
            return MyStep(config)
    """

    def decorator(factory: StepFactory) -> StepFactory:
        canonical = name.replace("-", "_").lower()
        if canonical in _STEP_REGISTRY:
            existing = _STEP_REGISTRY[canonical]
            raise ValueError(
                f"Step type {canonical!r} already registered "
                f"(existing: {existing.__name__}, new: {factory.__name__})"
            )
        _STEP_REGISTRY[canonical] = factory
        return factory

    return decorator


# ---------------------------------------------------------------------------
# Built-in step registrations
# ---------------------------------------------------------------------------

@register_step_type("cache")
def _make_cache(config, cache_backend, session_store):
    from condense.pipeline.steps.cache_step import CacheStep
    if cache_backend is None:
        raise ValueError("Cache optimization enabled but no cache backend is configured")
    return CacheStep(config, cache_backend)


@register_step_type("provider_cache")
def _make_provider_cache(config, cache_backend, session_store):
    from condense.pipeline.steps.provider_cache_step import ProviderCacheStep
    return ProviderCacheStep(config)


@register_step_type("routing")
def _make_routing(config, cache_backend, session_store):
    from condense.pipeline.steps.routing_step import RoutingStep
    return RoutingStep(config)


@register_step_type("budget")
def _make_budget(config, cache_backend, session_store):
    from condense.pipeline.steps.budget_step import BudgetStep
    if session_store is None:
        raise ValueError("Budget optimization enabled but no session store is configured")
    return BudgetStep(config, session_store)


@register_step_type("compression")
def _make_compression(config, cache_backend, session_store):
    from condense.pipeline.steps.compression_step import CompressionStep
    return CompressionStep(config)


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def _materialize_step(
    entry: OptimizationEntry,
    cache_backend: Optional[CacheBackend],
    session_store: Optional[SessionStore],
) -> BaseStep:
    canonical = entry.type.replace("-", "_").lower()
    factory = _STEP_REGISTRY.get(canonical)
    if factory is None:
        available = ", ".join(sorted(_STEP_REGISTRY)) or "(none)"
        raise ValueError(
            f"Unsupported optimization type: {entry.type!r}. "
            f"Available: {available}"
        )
    return factory(entry.config, cache_backend, session_store)


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
