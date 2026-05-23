from types import SimpleNamespace

import pytest

from condense.pipeline.context import PipelineContext
from condense.pipeline.result import OptimizationUpdate


def _make_context() -> PipelineContext:
    return PipelineContext(
        original_request={"model": "ollama/gemma3:4b", "messages": []},
        request={"model": "ollama/gemma3:4b", "messages": []},
        config=SimpleNamespace(),
        metadata={"estimated_cost": 0.0002},
    )


def test_optimization_update_defaults_tokens_saved_to_zero():
    update = OptimizationUpdate(optimization_id="cache", technique="exact_cache")
    update.validate()
    assert update.tokens_saved == 0


def test_context_add_optimization_update_accepts_minimal_required_contract():
    ctx = _make_context()
    ctx.add_optimization_update(
        {
            "optimization_id": "cache",
            "technique": "exact_cache",
            "savings_usd": 0.0002,
            "details": {"cache_hit": True},
        }
    )
    assert len(ctx.optimization_updates) == 1
    assert ctx.optimization_updates[0]["optimization_id"] == "cache"


def test_request_metrics_includes_optimization_updates():
    ctx = _make_context()
    ctx.cache_hit = True
    ctx.total_savings_usd = 0.0002
    ctx.add_optimization_update(
        {
            "optimization_id": "cache",
            "technique": "exact_cache",
            "tokens_saved": 25,
            "details": {"cache_hit": True},
        }
    )
    result = SimpleNamespace(action="short_circuit", response={"usage": {"prompt_tokens": 10, "completion_tokens": 5}})
    metrics = ctx.build_request_metrics(result, latency_ms=2.5)
    payload = metrics.as_record_kwargs()
    assert payload["total_tokens"] == 15
    assert payload["optimization_updates"][0]["optimization_id"] == "cache"
