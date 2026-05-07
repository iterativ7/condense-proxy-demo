from dataclasses import dataclass
from typing import Optional, Any


@dataclass
class StepResult:
    action: str  # "next" | "short_circuit" | "reject"
    response: Optional[Any] = None  # For short_circuit
    error: Optional[str] = None  # For reject
    status_code: int = 200
    technique: Optional[str] = None  # Which technique acted
    savings_usd: float = 0.0
