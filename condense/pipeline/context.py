from dataclasses import dataclass, field
from typing import Optional, Dict, Any

from condense.pipeline.result import OptimizationUpdate


@dataclass
class RequestMetrics:
    """Canonical per-request metrics payload derived from pipeline context."""

    cache_hit: bool
    savings_usd: float
    cost_usd: float
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    tokens_saved_estimate: int
    routed: bool
    rejected: bool
    latency_ms: float
    optimization_updates: list[dict[str, Any]] = field(default_factory=list)

    def as_record_kwargs(self) -> dict[str, Any]:
        """Return kwargs shape expected by MetricsTracker.record_request()."""
        return {
            "cache_hit": self.cache_hit,
            "savings_usd": self.savings_usd,
            "cost_usd": self.cost_usd,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "tokens_saved_estimate": self.tokens_saved_estimate,
            "routed": self.routed,
            "rejected": self.rejected,
            "latency_ms": self.latency_ms,
            "optimization_updates": self.optimization_updates,
        }

@dataclass
class PipelineContext:
    """Shared state passed through all pipeline steps."""

    # Original request (immutable — for failsafe passthrough)
    original_request: dict

    # Working request (steps modify this)
    request: dict

    # Config
    config: Any  # CondenseConfig

    # Session info
    session_id: Optional[str] = None
    session_turn: int = 0

    # Cache namespace (API key hash for tenant isolation)
    cache_namespace: str = ""

    # Tracking (accumulated by steps)
    original_model: Optional[str] = None
    routed_model: Optional[str] = None
    original_tokens: int = 0
    optimized_tokens: int = 0
    cache_hit: bool = False
    cache_hit_type: Optional[str] = None  # "exact" | "semantic"
    techniques_applied: list = field(default_factory=list)
    total_savings_usd: float = 0.0
    optimization_updates: list[dict[str, Any]] = field(default_factory=list)

    # Metadata
    metadata: Dict[str, Any] = field(default_factory=dict)

    @staticmethod
    def _extract_usage_metrics(response_payload: Any) -> tuple[int, int, int]:
        """Extract usage token counters from an upstream/cached response payload."""
        if not isinstance(response_payload, dict):
            return 0, 0, 0

        usage = response_payload.get("usage", {})
        if not isinstance(usage, dict):
            return 0, 0, 0

        prompt_tokens = int(usage.get("prompt_tokens") or 0)
        completion_tokens = int(usage.get("completion_tokens") or 0)
        total_tokens = usage.get("total_tokens")
        if total_tokens is None:
            total_tokens = prompt_tokens + completion_tokens
        return prompt_tokens, completion_tokens, int(total_tokens)

    def add_optimization_update(
        self,
        update: OptimizationUpdate | dict[str, Any],
        *,
        default_optimization_id: Optional[str] = None,
        default_action: Optional[str] = None,
    ) -> None:
        """Record a normalized optimization update with contract validation."""
        if isinstance(update, OptimizationUpdate):
            update_obj = update
        else:
            merged = dict(update)
            if merged.get("tokens_saved") is None:
                merged["tokens_saved"] = 0
            if default_optimization_id and not merged.get("optimization_id"):
                merged["optimization_id"] = default_optimization_id
            if default_action and not merged.get("action"):
                merged["action"] = default_action
            update_obj = OptimizationUpdate(**merged)

        if default_optimization_id and not update_obj.optimization_id:
            update_obj.optimization_id = default_optimization_id
        if default_action and not update_obj.action:
            update_obj.action = default_action
        if update_obj.tokens_saved is None:
            update_obj.tokens_saved = 0

        update_obj.validate()
        self.optimization_updates.append(
            {
                "optimization_id": update_obj.optimization_id,
                "technique": update_obj.technique,
                "savings_usd": update_obj.savings_usd,
                "tokens_saved": update_obj.tokens_saved,
                "details": update_obj.details or {},
                "action": update_obj.action,
            }
        )

    def build_request_metrics(self, result: Any, latency_ms: float) -> RequestMetrics:
        """Build canonical request metrics from pipeline state + final result."""
        prompt_tokens, completion_tokens, total_tokens = self._extract_usage_metrics(
            getattr(result, "response", None)
        )
        tokens_saved_estimate = total_tokens if self.cache_hit else 0
        return RequestMetrics(
            cache_hit=self.cache_hit,
            savings_usd=self.total_savings_usd,
            cost_usd=self.metadata.get("estimated_cost", 0.0),
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            tokens_saved_estimate=tokens_saved_estimate,
            routed=self.routed_model is not None,
            rejected=getattr(result, "action", "") == "reject",
            latency_ms=latency_ms,
            optimization_updates=list(self.optimization_updates),
        )
