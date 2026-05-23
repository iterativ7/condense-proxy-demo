import asyncio
import logging
from copy import deepcopy
from typing import List

from condense.pipeline.steps.base import BaseStep
from condense.pipeline.context import PipelineContext
from condense.pipeline.result import StepResult

logger = logging.getLogger(__name__)


class PipelineExecutor:
    def __init__(self, steps: List[BaseStep]):
        self.steps = [s for s in steps if s.is_enabled()]

    @staticmethod
    def _surface_overlap(left: str, right: str) -> bool:
        return (
            left == right
            or left.startswith(f"{right}:")
            or right.startswith(f"{left}:")
        )

    @classmethod
    def _has_dependency_hazard(cls, first: BaseStep, second: BaseStep) -> bool:
        first_reads = set(getattr(first, "reads", set()))
        first_writes = set(getattr(first, "writes", set()))
        second_reads = set(getattr(second, "reads", set()))
        second_writes = set(getattr(second, "writes", set()))

        pairs = [
            (left, right)
            for left in first_writes
            for right in second_reads.union(second_writes)
        ] + [
            (left, right)
            for left in second_writes
            for right in first_reads
        ]
        return any(cls._surface_overlap(left, right) for left, right in pairs)

    def _plan_batches(self) -> list[list[BaseStep]]:
        runnable = [step for step in self.steps if step.runs_forward()]
        if not runnable:
            return []

        index = {
            (step.optimization_id or f"{step.name}_{idx}"): idx
            for idx, step in enumerate(runnable)
        }
        ids = [step.optimization_id or f"{step.name}_{idx}" for idx, step in enumerate(runnable)]
        edges: dict[str, set[str]] = {sid: set() for sid in ids}

        for idx, step in enumerate(runnable):
            source = step.optimization_id or f"{step.name}_{idx}"
            for dep in getattr(step, "depends_on", tuple()):
                if dep in index:
                    edges[dep].add(source)

        for i in range(len(runnable)):
            for j in range(i + 1, len(runnable)):
                first = runnable[i]
                second = runnable[j]
                first_id = first.optimization_id or f"{first.name}_{i}"
                second_id = second.optimization_id or f"{second.name}_{j}"
                if second_id in edges[first_id] or first_id in edges[second_id]:
                    continue
                if (
                    not getattr(first, "supports_parallel", False)
                    or not getattr(second, "supports_parallel", False)
                    or getattr(first, "can_short_circuit", False)
                    or getattr(second, "can_short_circuit", False)
                    or self._has_dependency_hazard(first, second)
                ):
                    edges[first_id].add(second_id)

        in_degree = {sid: 0 for sid in ids}
        for targets in edges.values():
            for target in targets:
                in_degree[target] += 1

        pending = set(ids)
        batches: list[list[BaseStep]] = []
        while pending:
            level_ids = [sid for sid in ids if sid in pending and in_degree[sid] == 0]
            if not level_ids:
                return [runnable]
            batches.append([runnable[index[sid]] for sid in level_ids])
            for sid in level_ids:
                pending.remove(sid)
                for target in edges[sid]:
                    in_degree[target] -= 1

        return batches

    async def _run_backward(self, ctx: PipelineContext, applied: list[BaseStep], result: StepResult) -> None:
        for step in reversed(applied):
            if not step.runs_backward():
                continue
            try:
                await step.backward(ctx, result)
            except Exception as e:
                logger.error(
                    f"Backward hook for {step.__class__.__name__} failed: {e}",
                    exc_info=True,
                )

    @staticmethod
    def _record_step_update(ctx: PipelineContext, step: BaseStep, result: StepResult) -> None:
        updates = list(result.optimization_updates)
        if not updates:
            updates = [
                {
                    "optimization_id": getattr(step, "optimization_id", step.name),
                    "technique": result.technique or step.name,
                    "savings_usd": float(result.savings_usd),
                    "tokens_saved": int(result.tokens_saved or 0),
                    "details": result.details or {},
                }
            ]

        for update in updates:
            ctx.add_optimization_update(
                update,
                default_optimization_id=getattr(step, "optimization_id", step.name),
                default_action=result.action,
            )

    @staticmethod
    def _merge_parallel_ctx(
        base_ctx: PipelineContext,
        merged_ctx: PipelineContext,
        candidate_ctx: PipelineContext,
    ) -> PipelineContext:
        for key, value in candidate_ctx.request.items():
            if base_ctx.request.get(key) != value:
                merged_ctx.request[key] = deepcopy(value)
        for key in list(merged_ctx.request.keys()):
            if key in base_ctx.request and key not in candidate_ctx.request:
                merged_ctx.request.pop(key, None)

        for key, value in candidate_ctx.metadata.items():
            if base_ctx.metadata.get(key) != value:
                merged_ctx.metadata[key] = deepcopy(value)
        for key in list(merged_ctx.metadata.keys()):
            if key in base_ctx.metadata and key not in candidate_ctx.metadata:
                merged_ctx.metadata.pop(key, None)

        for attr in (
            "session_id",
            "session_turn",
            "cache_namespace",
            "original_model",
            "routed_model",
            "original_tokens",
            "optimized_tokens",
            "cache_hit",
            "cache_hit_type",
        ):
            if getattr(candidate_ctx, attr) != getattr(base_ctx, attr):
                setattr(merged_ctx, attr, getattr(candidate_ctx, attr))
        return merged_ctx

    async def execute(self, ctx: PipelineContext) -> StepResult:
        batches = self._plan_batches()
        applied: list[BaseStep] = []
        for batch in batches:
            try:
                if len(batch) == 1:
                    step = batch[0]
                    result = await step.forward(ctx)
                    applied.append(step)

                    if result.technique:
                        ctx.techniques_applied.append(result.technique)
                    ctx.total_savings_usd += result.savings_usd
                    self._record_step_update(ctx, step, result)

                    if result.action in {"short_circuit", "reject"}:
                        await self._run_backward(ctx, applied, result)
                        return result
                    continue

                base_ctx = deepcopy(ctx)

                async def run_one(one_step: BaseStep):
                    local_ctx = deepcopy(base_ctx)
                    local_result = await one_step.forward(local_ctx)
                    return one_step, local_ctx, local_result

                outcomes = await asyncio.gather(*(run_one(one_step) for one_step in batch))
                outcome_by_id: dict[int, tuple[BaseStep, PipelineContext, StepResult]] = {
                    id(step): (step, out_ctx, out_result)
                    for step, out_ctx, out_result in outcomes
                }
                for step in batch:
                    _, out_ctx, result = outcome_by_id[id(step)]
                    ctx = self._merge_parallel_ctx(base_ctx, ctx, out_ctx)
                    applied.append(step)
                    if result.technique:
                        ctx.techniques_applied.append(result.technique)
                    ctx.total_savings_usd += result.savings_usd
                    self._record_step_update(ctx, step, result)
                    if result.action in {"short_circuit", "reject"}:
                        await self._run_backward(ctx, applied, result)
                        return result

            except Exception as e:
                # FAILSAFE: skip broken step, never block a request
                names = ", ".join(one_step.__class__.__name__ for one_step in batch)
                logger.error(f"Step batch [{names}] failed: {e}", exc_info=True)
                continue

        # All steps passed — should have been handled by ForwardStep
        # If we get here, something is wrong (ForwardStep missing?)
        result = StepResult(action="reject", error="Pipeline completed without forwarding", status_code=500)
        await self._run_backward(ctx, applied, result)
        return result
