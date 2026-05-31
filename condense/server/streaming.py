"""Utilities for OpenAI-compatible SSE streaming."""

from __future__ import annotations

import json
from collections import defaultdict
from typing import Any, AsyncIterator


def chunk_to_dict(raw: Any) -> dict[str, Any]:
    """Normalize LiteLLM/OpenAI chunk objects to plain dict."""
    if isinstance(raw, dict):
        return raw
    if hasattr(raw, "model_dump"):
        return raw.model_dump()
    if hasattr(raw, "dict"):
        return raw.dict()
    return dict(raw)


def format_sse_chunk(payload: dict[str, Any]) -> bytes:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n".encode("utf-8")


def format_sse_done() -> bytes:
    return b"data: [DONE]\n\n"


def strip_internal_metadata(payload: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in payload.items() if not str(k).startswith("_condense_")}


def completion_from_chunks(
    chunks: list[dict[str, Any]],
    *,
    fallback_model: str = "",
) -> dict[str, Any]:
    """Assemble a final chat.completion payload from streamed chunks."""
    if not chunks:
        return {
            "id": "chatcmpl-stream",
            "object": "chat.completion",
            "model": fallback_model,
            "choices": [{"index": 0, "message": {"role": "assistant", "content": ""}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        }

    content_by_idx: dict[int, list[str]] = defaultdict(list)
    role_by_idx: dict[int, str] = defaultdict(lambda: "assistant")
    finish_reason_by_idx: dict[int, Any] = defaultdict(lambda: "stop")
    usage: dict[str, Any] = {}
    model = fallback_model
    response_id = "chatcmpl-stream"
    created = None

    for chunk in chunks:
        if chunk.get("id"):
            response_id = chunk["id"]
        if chunk.get("model"):
            model = chunk["model"]
        if created is None and chunk.get("created") is not None:
            created = chunk.get("created")

        chunk_usage = chunk.get("usage")
        if isinstance(chunk_usage, dict):
            usage = chunk_usage

        choices = chunk.get("choices") or []
        for choice in choices:
            idx = int(choice.get("index", 0))
            delta = choice.get("delta") or {}
            if isinstance(delta, dict):
                if delta.get("role"):
                    role_by_idx[idx] = str(delta["role"])
                content = delta.get("content")
                if isinstance(content, str) and content:
                    content_by_idx[idx].append(content)
            if choice.get("finish_reason") is not None:
                finish_reason_by_idx[idx] = choice.get("finish_reason")

    max_index = max(
        set(content_by_idx.keys()).union(role_by_idx.keys()).union(finish_reason_by_idx.keys()) or {0}
    )
    assembled_choices = []
    for idx in range(max_index + 1):
        assembled_choices.append(
            {
                "index": idx,
                "message": {
                    "role": role_by_idx[idx],
                    "content": "".join(content_by_idx[idx]),
                },
                "finish_reason": finish_reason_by_idx[idx],
            }
        )

    prompt_tokens = int((usage or {}).get("prompt_tokens") or 0)
    completion_tokens = int((usage or {}).get("completion_tokens") or 0)
    total_tokens = (usage or {}).get("total_tokens")
    if total_tokens is None:
        total_tokens = prompt_tokens + completion_tokens

    return {
        "id": response_id,
        "object": "chat.completion",
        "created": created,
        "model": model,
        "choices": assembled_choices,
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": int(total_tokens),
        },
    }


async def replay_completion_as_sse(cached: dict[str, Any], chunk_size: int = 32) -> AsyncIterator[bytes]:
    """Replay a cached chat.completion object as OpenAI-style SSE chunks."""
    clean = strip_internal_metadata(cached)
    response_id = clean.get("id", "chatcmpl-cache")
    created = clean.get("created")
    model = clean.get("model")
    choices = clean.get("choices") or []

    for choice in choices:
        idx = int(choice.get("index", 0))
        msg = choice.get("message") or {}
        role = msg.get("role", "assistant")
        content = str(msg.get("content") or "")

        yield format_sse_chunk(
            {
                "id": response_id,
                "object": "chat.completion.chunk",
                "created": created,
                "model": model,
                "choices": [{"index": idx, "delta": {"role": role}, "finish_reason": None}],
            }
        )

        for i in range(0, len(content), chunk_size):
            token_like = content[i : i + chunk_size]
            yield format_sse_chunk(
                {
                    "id": response_id,
                    "object": "chat.completion.chunk",
                    "created": created,
                    "model": model,
                    "choices": [{"index": idx, "delta": {"content": token_like}, "finish_reason": None}],
                }
            )

        yield format_sse_chunk(
            {
                "id": response_id,
                "object": "chat.completion.chunk",
                "created": created,
                "model": model,
                "choices": [{"index": idx, "delta": {}, "finish_reason": choice.get("finish_reason", "stop")}],
            }
        )

    usage = clean.get("usage")
    if isinstance(usage, dict):
        yield format_sse_chunk(
            {
                "id": response_id,
                "object": "chat.completion.chunk",
                "created": created,
                "model": model,
                "choices": [],
                "usage": usage,
            }
        )

    yield format_sse_done()
