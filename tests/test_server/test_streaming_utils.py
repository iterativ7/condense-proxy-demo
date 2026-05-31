"""Unit tests for SSE streaming utilities."""

import json

import pytest

from condense.server.streaming import (
    completion_from_chunks,
    format_sse_chunk,
    format_sse_done,
    replay_completion_as_sse,
)


def test_format_sse_chunk():
    payload = {"id": "abc", "choices": [{"delta": {"content": "Hi"}}]}
    encoded = format_sse_chunk(payload).decode("utf-8")
    assert encoded.startswith("data: ")
    assert encoded.endswith("\n\n")
    parsed = json.loads(encoded[len("data: ") : -2])
    assert parsed["id"] == "abc"


def test_format_sse_done():
    assert format_sse_done() == b"data: [DONE]\n\n"


def test_completion_from_chunks_reassembles_content_and_usage():
    chunks = [
        {
            "id": "chatcmpl-1",
            "model": "gpt-4o",
            "choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}],
        },
        {
            "id": "chatcmpl-1",
            "model": "gpt-4o",
            "choices": [{"index": 0, "delta": {"content": "Hello"}, "finish_reason": None}],
        },
        {
            "id": "chatcmpl-1",
            "model": "gpt-4o",
            "choices": [{"index": 0, "delta": {"content": " world"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        },
    ]
    completion = completion_from_chunks(chunks, fallback_model="gpt-4o")
    assert completion["id"] == "chatcmpl-1"
    assert completion["choices"][0]["message"]["content"] == "Hello world"
    assert completion["usage"]["total_tokens"] == 15


@pytest.mark.asyncio
async def test_replay_completion_as_sse_emits_done():
    completion = {
        "id": "chatcmpl-cache",
        "model": "gpt-4o",
        "choices": [{"index": 0, "message": {"role": "assistant", "content": "Hello"}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    }
    events = []
    async for chunk in replay_completion_as_sse(completion, chunk_size=2):
        events.append(chunk.decode("utf-8"))
    assert any('"content": "He"' in event for event in events)
    assert events[-1] == "data: [DONE]\n\n"
