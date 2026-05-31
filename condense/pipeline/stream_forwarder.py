"""Real-time upstream streaming helpers."""

from __future__ import annotations

import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

import litellm

from condense.pipeline.context import PipelineContext
from condense.pipeline.steps.forward_step import ForwardStep
from condense.server.stream_protocols import infer_stream_protocol, resolve_stream_protocol

logger = logging.getLogger(__name__)


class StreamForwarder:
    """Forward upstream chunks to client while buffering final completion."""

    def __init__(self, forward_step: ForwardStep):
        self.forward_step = forward_step

    def sse_iterator(
        self,
        ctx: PipelineContext,
        *,
        on_complete: Callable[[dict[str, Any], int, float, float], Awaitable[None]],
    ) -> AsyncIterator[bytes]:
        async def _run() -> AsyncIterator[bytes]:
            started_at = time.perf_counter()
            first_chunk_ms = 0.0
            chunk_dicts: list[dict[str, Any]] = []
            status_code = 200
            final_response: dict[str, Any] | None = None
            protocol_name = infer_stream_protocol(
                requested=ctx.request.get("stream_protocol"),
            )
            adapter = resolve_stream_protocol(protocol_name)

            payload = self.forward_step.build_payload(ctx)
            payload["stream"] = True
            payload.setdefault("stream_options", {"include_usage": True})

            try:
                raw_stream = await litellm.acompletion(**payload)
                async for raw_chunk in raw_stream:
                    chunk = adapter.normalize_chunk(raw_chunk)
                    if not chunk_dicts:
                        first_chunk_ms = (time.perf_counter() - started_at) * 1000
                    chunk_dicts.append(chunk)
                    yield adapter.to_client_chunk(chunk)
                yield adapter.done_chunk()
                final_response = adapter.finalize_response(
                    chunk_dicts,
                    fallback_model=ctx.request.get("model", ""),
                )
                self.forward_step.attach_estimated_cost(ctx, final_response)
            except Exception as exc:
                status = getattr(exc, "status_code", None) or getattr(exc, "status", None)
                status_code = int(status) if status else 502
                message = str(exc)
                logger.warning("Streaming forward failed: %s", message, exc_info=True)
                yield adapter.error_chunk(message, status_code)
                yield adapter.done_chunk()
                final_response = {"error": {"message": message, "type": "upstream_error"}}
            finally:
                elapsed_ms = (time.perf_counter() - started_at) * 1000
                if final_response is None:
                    final_response = adapter.finalize_response(
                        chunk_dicts,
                        fallback_model=ctx.request.get("model", ""),
                    )
                    self.forward_step.attach_estimated_cost(ctx, final_response)
                await on_complete(final_response, status_code, elapsed_ms, first_chunk_ms)

        return _run()
