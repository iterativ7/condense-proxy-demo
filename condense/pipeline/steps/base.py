from abc import ABC, abstractmethod
from condense.pipeline.context import PipelineContext
from condense.pipeline.result import StepResult


class BaseStep(ABC):
    """Base class for all optimization pipeline steps."""

    def __init__(self, config: dict):
        self.config = config
        self.enabled = config.get("enabled", True)

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

    def is_enabled(self) -> bool:
        return self.enabled
