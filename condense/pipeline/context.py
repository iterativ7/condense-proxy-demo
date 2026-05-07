from dataclasses import dataclass, field
from typing import Optional, Dict, Any


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

    # Metadata
    metadata: Dict[str, Any] = field(default_factory=dict)
