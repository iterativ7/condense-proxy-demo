from abc import ABC, abstractmethod
from condense.pipeline.context import PipelineContext
from condense.pipeline.result import StepResult


class BaseStep(ABC):
    """Base class for all optimization pipeline steps."""

    name: str = "step"
    optimization_id: str | None = None
    supports_parallel: bool = False
    can_short_circuit: bool = False
    reads: frozenset[str] = frozenset({"request", "metadata"})
    writes: frozenset[str] = frozenset()
    execution_stage: str = "both"

    def __init__(self, config: dict):
        self.config = config
        self.enabled = config.get("enabled", True)
        self.depends_on: tuple[str, ...] = tuple()

    @abstractmethod
    async def execute(self, ctx: PipelineContext) -> StepResult:
        """Execute this optimization step.

        Returns:
            StepResult with action:
              - "next" → continue to next step
              - "short_circuit" → return response immediately (cache hit)
              - "reject" → return error (budget exceeded)
        """
        pass

    async def forward(self, ctx: PipelineContext) -> StepResult:
        """Forward phase hook. Defaults to legacy execute() behavior."""
        return await self.execute(ctx)

    async def backward(self, ctx: PipelineContext, result: StepResult) -> None:
        """Backward phase hook for post-processing. Default is no-op."""
        return None

    def is_enabled(self) -> bool:
        return self.enabled

    def runs_forward(self) -> bool:
        return self.execution_stage in {"both", "forward"}

    def runs_backward(self) -> bool:
        return self.execution_stage in {"both", "backward"}
