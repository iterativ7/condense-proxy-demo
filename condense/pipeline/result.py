from dataclasses import dataclass, field
from typing import Optional, Any


@dataclass
class OptimizationUpdate:
    """Structured update emitted by an optimization step."""

    optimization_id: str
    technique: Optional[str] = None
    savings_usd: Optional[float] = None
    tokens_saved: int = 0
    details: dict[str, Any] = field(default_factory=dict)
    action: Optional[str] = None

    def validate(self) -> None:
        """Ensure required savings contract is respected."""
        if self.savings_usd is None and self.tokens_saved is None:
            raise ValueError(
                "Optimization update must include at least one of savings_usd or tokens_saved"
            )


@dataclass
class StepResult:
    action: str  # "next" | "short_circuit" | "reject"
    response: Optional[Any] = None  # For short_circuit
    error: Optional[str] = None  # For reject
    status_code: int = 200
    technique: Optional[str] = None  # Which technique acted
    savings_usd: float = 0.0
    tokens_saved: int = 0
    details: dict[str, Any] = field(default_factory=dict)
    optimization_updates: list[OptimizationUpdate | dict[str, Any]] = field(default_factory=list)
