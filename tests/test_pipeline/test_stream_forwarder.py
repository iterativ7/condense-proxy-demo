"""Tests for real-time stream forwarder."""

import asyncio

import httpx
import pytest
from condense.config.schema import CondenseConfig
from condense.pipeline.context import PipelineContext
from condense.pipeline.steps.forward_step import ForwardStep
from condense.pipeline.stream_forwarder import StreamForwarder


class _FakeChunk:
    def __init__(self, payload):
        self._payload = payload

    def model_dump(self):
        return self._payload


class _FakeAsyncStream:
    def __init__(self, payloads):
        self._payloads = payloads

    def __aiter__(self):
        self._iter = iter(self._payloads)
        return self

    async def __anext__(self):
        await asyncio.sleep(0.001)
        try:
            payload = next(self._iter)
        except StopIteration as exc:
            raise StopAsyncIteration from exc
        return _FakeChunk(payload)


def _ctx(stream_protocol: str | None = None) -> PipelineContext:
    req = {"model": "gpt-4o", "messages": [{"role": "user", "content": "Hello"}], "stream": True}
    if stream_protocol:
        req["stream_protocol"] = stream_protocol
    return PipelineContext(
        original_request=req.copy(),
        request=req,
        config=CondenseConfig(),
        metadata={},
    )


@pytest.mark.asyncio
async def test_stream_forwarder_passthrough_and_completion(monkeypatch):
    chunks = [
        {
            "id": "chatcmpl-1",
            "model": "gpt-4o",
            "choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}],
        },
        {
            "id": "chatcmpl-1",
            "model": "gpt-4o",
            "choices": [{"index": 0, "delta": {"content": "Hi"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 3, "completion_tokens": 1, "total_tokens": 4},
        },
    ]

    async def fake_acompletion(**kwargs):
        assert kwargs.get("stream") is True
        return _FakeAsyncStream(chunks)

    monkeypatch.setattr("condense.pipeline.stream_forwarder.litellm.acompletion", fake_acompletion)

    async with httpx.AsyncClient() as client:
        step = ForwardStep({"url": "https://api.openai.com/v1", "timeout_seconds": 30}, client)
        forwarder = StreamForwarder(step)
        captured = {}

        async def on_complete(response, status_code, elapsed_ms, first_chunk_ms):
            captured["response"] = response
            captured["status_code"] = status_code
            captured["elapsed_ms"] = elapsed_ms
            captured["first_chunk_ms"] = first_chunk_ms

        output = []
        async for part in forwarder.sse_iterator(_ctx(), on_complete=on_complete):
            output.append(part.decode("utf-8"))

    assert any("data: " in part for part in output)
    assert output[-1] == "data: [DONE]\n\n"
    assert captured["status_code"] == 200
    assert captured["response"]["choices"][0]["message"]["content"] == "Hi"
    assert captured["response"]["usage"]["total_tokens"] == 4
    assert captured["first_chunk_ms"] > 0


@pytest.mark.asyncio
async def test_stream_forwarder_generic_protocol_fallback(monkeypatch):
    chunks = [
        {"id": "generic-1", "model": "future-provider/v1", "content": "Hello "},
        {"id": "generic-1", "model": "future-provider/v1", "content": "world"},
    ]

    async def fake_acompletion(**kwargs):
        assert kwargs.get("stream") is True
        return _FakeAsyncStream(chunks)

    monkeypatch.setattr("condense.pipeline.stream_forwarder.litellm.acompletion", fake_acompletion)

    async with httpx.AsyncClient() as client:
        step = ForwardStep({"url": "https://api.openai.com/v1", "timeout_seconds": 30}, client)
        forwarder = StreamForwarder(step)
        captured = {}

        async def on_complete(response, status_code, elapsed_ms, first_chunk_ms):
            captured["response"] = response
            captured["status_code"] = status_code
            captured["elapsed_ms"] = elapsed_ms
            captured["first_chunk_ms"] = first_chunk_ms

        output = []
        async for part in forwarder.sse_iterator(_ctx("future_vendor_stream"), on_complete=on_complete):
            output.append(part.decode("utf-8"))

    assert output[-1] == "data: [DONE]\n\n"
    assert captured["status_code"] == 200
    assert captured["response"]["choices"][0]["message"]["content"] == "Hello world"
    assert captured["response"]["_condense_stream_protocol"] == "generic_json_sse"
