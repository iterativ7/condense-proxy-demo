"""Pluggable stream protocol adapters for provider-specific event formats."""

from __future__ import annotations

from typing import Any, AsyncIterator, Callable

from condense.server.streaming import (
    chunk_to_dict,
    completion_from_chunks,
    format_sse_chunk,
    format_sse_done,
    replay_completion_as_sse,
    strip_internal_metadata,
)


class StreamProtocolAdapter:
    """Contract for stream protocol normalization, replay, and finalization."""

    name = "openai_chat_sse"

    def normalize_chunk(self, raw: Any) -> dict[str, Any]:
        return chunk_to_dict(raw)

    def to_client_chunk(self, chunk: dict[str, Any]) -> bytes:
        return format_sse_chunk(chunk)

    def done_chunk(self) -> bytes:
        return format_sse_done()

    def error_chunk(self, message: str, status_code: int) -> bytes:
        return format_sse_chunk(
            {
                "error": {"message": message, "type": "upstream_error"},
                "status_code": status_code,
            }
        )

    def finalize_response(self, chunks: list[dict[str, Any]], *, fallback_model: str = "") -> dict[str, Any]:
        response = completion_from_chunks(chunks, fallback_model=fallback_model)
        response["_condense_stream_protocol"] = self.name
        return response

    async def replay_cached_response(
        self,
        cached_response: dict[str, Any],
    ) -> AsyncIterator[bytes]:
        async for chunk in replay_completion_as_sse(cached_response):
            yield chunk


class GenericJSONSSEAdapter(StreamProtocolAdapter):
    """Fallback adapter for providers that emit non-standard stream chunk shapes."""

    name = "generic_json_sse"

    def _extract_text(self, chunk: dict[str, Any]) -> str:
        for key in ("text", "content"):
            value = chunk.get(key)
            if isinstance(value, str) and value:
                return value
        delta = chunk.get("delta")
        if isinstance(delta, str):
            return delta
        if isinstance(delta, dict):
            content = delta.get("content")
            if isinstance(content, str):
                return content
        return ""

    def finalize_response(self, chunks: list[dict[str, Any]], *, fallback_model: str = "") -> dict[str, Any]:
        # Use OpenAI assembly when possible.
        has_openai_delta = any(bool((chunk.get("choices") or [])) for chunk in chunks)
        if has_openai_delta:
            return super().finalize_response(chunks, fallback_model=fallback_model)

        content = "".join(self._extract_text(chunk) for chunk in chunks)
        usage: dict[str, Any] = {}
        model = fallback_model
        response_id = "chatcmpl-stream"

        for chunk in chunks:
            if isinstance(chunk.get("usage"), dict):
                usage = chunk["usage"]
            if chunk.get("model"):
                model = str(chunk["model"])
            if chunk.get("id"):
                response_id = str(chunk["id"])

        prompt_tokens = int((usage or {}).get("prompt_tokens") or 0)
        completion_tokens = int((usage or {}).get("completion_tokens") or 0)
        total_tokens = int((usage or {}).get("total_tokens") or (prompt_tokens + completion_tokens))
        return {
            "id": response_id,
            "object": "chat.completion",
            "model": model,
            "choices": [{"index": 0, "message": {"role": "assistant", "content": content}, "finish_reason": "stop"}],
            "usage": {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": total_tokens,
            },
            # Keep original chunks so replay can stay provider-shape-compatible.
            "_condense_stream_recorded_chunks": chunks,
            "_condense_stream_protocol": self.name,
        }

    async def replay_cached_response(
        self,
        cached_response: dict[str, Any],
    ) -> AsyncIterator[bytes]:
        recorded = cached_response.get("_condense_stream_recorded_chunks")
        if isinstance(recorded, list) and recorded:
            for chunk in recorded:
                if isinstance(chunk, dict):
                    yield self.to_client_chunk(strip_internal_metadata(chunk))
                else:
                    yield format_sse_chunk({"chunk": chunk})
            yield self.done_chunk()
            return
        async for chunk in replay_completion_as_sse(cached_response):
            yield chunk


_STREAM_PROTOCOL_REGISTRY: dict[str, Callable[[], StreamProtocolAdapter]] = {
    StreamProtocolAdapter.name: StreamProtocolAdapter,
    GenericJSONSSEAdapter.name: GenericJSONSSEAdapter,
}


def register_stream_protocol(name: str, factory: Callable[[], StreamProtocolAdapter]) -> None:
    canonical = name.replace("-", "_").lower().strip()
    if not canonical:
        raise ValueError("Protocol name cannot be empty")
    if canonical in _STREAM_PROTOCOL_REGISTRY:
        raise ValueError(f"Stream protocol {canonical!r} is already registered")
    _STREAM_PROTOCOL_REGISTRY[canonical] = factory


def resolve_stream_protocol(name: str | None) -> StreamProtocolAdapter:
    canonical = (name or StreamProtocolAdapter.name).replace("-", "_").lower().strip()
    factory = _STREAM_PROTOCOL_REGISTRY.get(canonical)
    if factory is None:
        # Unknown protocols gracefully degrade to generic JSON-over-SSE adapter.
        return GenericJSONSSEAdapter()
    return factory()


def infer_stream_protocol(*, requested: str | None, cached_response: dict[str, Any] | None = None) -> str:
    if requested:
        return requested
    if isinstance(cached_response, dict):
        protocol = cached_response.get("_condense_stream_protocol")
        if isinstance(protocol, str) and protocol.strip():
            return protocol
    return StreamProtocolAdapter.name
