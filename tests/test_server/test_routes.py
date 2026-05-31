"""Integration tests for server routes."""

import asyncio
import time

import pytest
from fastapi.testclient import TestClient
from fastapi.responses import StreamingResponse
from condense.server.app import create_app
from condense.config.loader import reset_config_cache


@pytest.fixture
def client(tmp_path):
    reset_config_cache()
    config_file = tmp_path / "condense.yaml"
    config_file.write_text("""
upstream:
  url: "https://api.openai.com/v1"
  timeout_seconds: 30
optimizations:
  - id: "cache"
    type: "cache"
    enabled: true
    config:
      exact:
        backend: "memory"
        max_size: 100
        ttl_seconds: 60
      non_deterministic: "skip"
  - id: "provider_cache"
    type: "provider_cache"
    enabled: false
    config: {}
  - id: "routing"
    type: "routing"
    enabled: false
    config:
      rules: []
  - id: "budget"
    type: "budget"
    enabled: false
    config: {}
deployment:
  port: 8080
""")
    app = create_app(str(config_file))
    with TestClient(app) as c:
        yield c


class TestChatCompletionsRoute:
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
            return TestChatCompletionsRoute._FakeChunk(payload)

    def test_passthrough(self, client, monkeypatch):
        """Request is forwarded to upstream and response returned."""
        response_data = {
            "id": "chatcmpl-123",
            "object": "chat.completion",
            "choices": [{"message": {"role": "assistant", "content": "Hello!"}, "index": 0, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        }

        async def fake_acompletion(**kwargs):
            return response_data

        monkeypatch.setattr(
            "condense.pipeline.steps.forward_step.litellm.acompletion",
            fake_acompletion,
        )

        resp = client.post(
            "/v1/chat/completions",
            json={
                "model": "gpt-4o",
                "messages": [{"role": "user", "content": "Hello"}],
                "temperature": 0,
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == "chatcmpl-123"

    def test_streaming_response_passthrough(self, client, monkeypatch):
        """stream=true returns SSE chunks with [DONE] trailer."""
        stream_chunks = [
            {
                "id": "chatcmpl-stream-1",
                "model": "gpt-4o",
                "choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}],
            },
            {
                "id": "chatcmpl-stream-1",
                "model": "gpt-4o",
                "choices": [{"index": 0, "delta": {"content": "Hello"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            },
        ]

        async def fake_acompletion(**kwargs):
            if kwargs.get("stream"):
                return TestChatCompletionsRoute._FakeAsyncStream(stream_chunks)
            return {
                "id": "chatcmpl-123",
                "choices": [{"message": {"role": "assistant", "content": "fallback"}}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            }

        monkeypatch.setattr("condense.pipeline.stream_forwarder.litellm.acompletion", fake_acompletion)
        monkeypatch.setattr("condense.pipeline.steps.forward_step.litellm.acompletion", fake_acompletion)

        with client.stream(
            "POST",
            "/v1/chat/completions",
            json={
                "model": "gpt-4o",
                "messages": [{"role": "user", "content": "Hello"}],
                "stream": True,
                "temperature": 0,
            },
        ) as resp:
            assert resp.status_code == 200
            assert "text/event-stream" in resp.headers.get("content-type", "")
            assert resp.headers.get("x-condense-stream-mode") == "live_upstream"
            assert resp.headers.get("x-condense-stream-protocol") == "openai_chat_sse"
            body = "".join(part for part in resp.iter_text())

        assert "data: [DONE]" in body
        assert '"content": "Hello"' in body

    def test_streaming_cache_hit_replays_sse(self, client, monkeypatch):
        """Cached response can be replayed as SSE for stream=true requests."""
        response_data = {
            "id": "chatcmpl-cache-1",
            "choices": [{"message": {"role": "assistant", "content": "Cached hello"}, "finish_reason": "stop", "index": 0}],
            "usage": {"prompt_tokens": 4, "completion_tokens": 2, "total_tokens": 6},
        }

        async def fake_acompletion(**kwargs):
            return response_data

        monkeypatch.setattr("condense.pipeline.steps.forward_step.litellm.acompletion", fake_acompletion)

        request_body = {
            "model": "gpt-4o",
            "messages": [{"role": "user", "content": "Replay this from cache"}],
            "temperature": 0,
        }
        warm = client.post("/v1/chat/completions", json=request_body)
        assert warm.status_code == 200

        with client.stream("POST", "/v1/chat/completions", json={**request_body, "stream": True}) as resp:
            assert resp.status_code == 200
            assert resp.headers.get("x-condense-stream-mode") == "cache_replay"
            assert resp.headers.get("x-condense-stream-protocol") == "openai_chat_sse"
            body = "".join(part for part in resp.iter_text())

        assert "data: [DONE]" in body
        assert '"content": "Cached hello"' in body

    def test_streaming_budget_reject_returns_json(self, tmp_path):
        """Reject path remains JSON for stream=true requests."""
        reset_config_cache()
        config_file = tmp_path / "condense.yaml"
        config_file.write_text("""
upstream:
  url: "https://api.openai.com/v1"
  timeout_seconds: 30
optimizations:
  - id: "cache"
    type: "cache"
    enabled: false
    config: {}
  - id: "budget"
    type: "budget"
    enabled: true
    config:
      max_turns_per_session: 0
deployment:
  port: 8080
""")
        app = create_app(str(config_file))
        with TestClient(app) as local_client:
            resp = local_client.post(
                "/v1/chat/completions",
                json={
                    "model": "gpt-4o",
                    "messages": [{"role": "user", "content": "Hello"}],
                    "stream": True,
                },
            )
        assert resp.status_code == 429
        assert "application/json" in resp.headers.get("content-type", "")
        data = resp.json()
        assert "error" in data

    def test_streaming_with_compression_enabled_still_streams(self, tmp_path, monkeypatch):
        """Pre-forward compression step should remain compatible with streaming."""
        reset_config_cache()
        config_file = tmp_path / "condense.yaml"
        config_file.write_text("""
upstream:
  url: "https://api.openai.com/v1"
  timeout_seconds: 30
optimizations:
  - id: "cache"
    type: "cache"
    enabled: false
    config: {}
  - id: "compression"
    type: "compression"
    enabled: true
    config:
      compressor_type: "fusion"
deployment:
  port: 8080
""")
        stream_chunks = [
            {
                "id": "chatcmpl-stream-compression",
                "model": "gpt-4o",
                "choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}],
            },
            {
                "id": "chatcmpl-stream-compression",
                "model": "gpt-4o",
                "choices": [{"index": 0, "delta": {"content": "OK"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 8, "completion_tokens": 2, "total_tokens": 10},
            },
        ]

        async def fake_acompletion(**kwargs):
            return TestChatCompletionsRoute._FakeAsyncStream(stream_chunks)

        monkeypatch.setattr("condense.pipeline.stream_forwarder.litellm.acompletion", fake_acompletion)

        app = create_app(str(config_file))
        with TestClient(app) as local_client:
            with local_client.stream(
                "POST",
                "/v1/chat/completions",
                json={
                    "model": "gpt-4o",
                    "messages": [{"role": "user", "content": "Summarize this text"}],
                    "stream": True,
                },
            ) as resp:
                assert resp.status_code == 200
                body = "".join(part for part in resp.iter_text())
        assert "data: [DONE]" in body

    def test_streaming_uses_circuit_breaker_bypass(self, client, monkeypatch):
        """When breaker is open, stream requests use direct streaming path."""
        called = {"value": False}

        async def fake_direct_forward_stream(body, config, authorization):
            called["value"] = True

            async def _gen():
                yield b"data: [DONE]\n\n"

            return StreamingResponse(_gen(), media_type="text/event-stream")

        monkeypatch.setattr("condense.server.routes._direct_forward_stream", fake_direct_forward_stream)
        breaker = client.app.state.circuit_breaker
        breaker._failure_count = breaker.threshold
        breaker._last_failure_time = time.time()

        with client.stream(
            "POST",
            "/v1/chat/completions",
            json={
                "model": "gpt-4o",
                "messages": [{"role": "user", "content": "Hello"}],
                "stream": True,
            },
        ) as resp:
            assert resp.status_code == 200
            body = "".join(part for part in resp.iter_text())

        assert called["value"] is True
        assert "data: [DONE]" in body

    def test_streaming_unknown_protocol_uses_generic_adapter(self, client, monkeypatch):
        """Unknown stream protocol should gracefully fallback to generic adapter."""
        stream_chunks = [
            {"id": "future-1", "model": "future/provider", "content": "hello "},
            {"id": "future-1", "model": "future/provider", "content": "world"},
        ]

        async def fake_acompletion(**kwargs):
            if kwargs.get("stream"):
                return TestChatCompletionsRoute._FakeAsyncStream(stream_chunks)
            return {
                "id": "chatcmpl-123",
                "choices": [{"message": {"role": "assistant", "content": "fallback"}}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            }

        monkeypatch.setattr("condense.pipeline.stream_forwarder.litellm.acompletion", fake_acompletion)

        with client.stream(
            "POST",
            "/v1/chat/completions",
            json={
                "model": "gpt-4o",
                "messages": [{"role": "user", "content": "Hello"}],
                "stream": True,
                "stream_protocol": "future_vendor_stream",
            },
        ) as resp:
            assert resp.status_code == 200
            assert resp.headers.get("x-condense-stream-protocol") == "generic_json_sse"
            body = "".join(part for part in resp.iter_text())

        assert "data: [DONE]" in body
        assert '"content": "hello "' in body

    def test_condense_headers(self, client, monkeypatch):
        """Response includes X-Condense-* headers."""
        response_data = {
            "id": "chatcmpl-123",
            "choices": [{"message": {"content": "Hi"}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        }

        async def fake_acompletion(**kwargs):
            return response_data

        monkeypatch.setattr(
            "condense.pipeline.steps.forward_step.litellm.acompletion",
            fake_acompletion,
        )

        resp = client.post(
            "/v1/chat/completions",
            json={
                "model": "gpt-4o",
                "messages": [{"role": "user", "content": "Hello"}],
                "temperature": 0,
            },
        )
        assert "x-condense-cache-hit" in resp.headers
        assert "x-condense-original-model" in resp.headers

    def test_cache_hit_on_second_request(self, client, monkeypatch):
        """Second identical request returns cache hit with single-digit-ms latency."""
        response_data = {
            "id": "chatcmpl-123",
            "choices": [{"message": {"content": "Hi"}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        }
        call_count = {"n": 0}

        async def fake_acompletion(**kwargs):
            call_count["n"] += 1
            # Simulate real upstream delay; cache hit should bypass this path.
            await asyncio.sleep(0.03)
            return response_data

        monkeypatch.setattr(
            "condense.pipeline.steps.forward_step.litellm.acompletion",
            fake_acompletion,
        )

        request_body = {
            "model": "gpt-4o",
            "messages": [{"role": "user", "content": "Unique cache test message 12345"}],
            "temperature": 0,
        }

        # First request — cache miss
        start = time.perf_counter()
        resp1 = client.post("/v1/chat/completions", json=request_body)
        miss_latency_ms = (time.perf_counter() - start) * 1000
        assert resp1.status_code == 200
        assert resp1.headers.get("x-condense-cache-hit") == "false"

        # Second request — cache hit
        start = time.perf_counter()
        resp2 = client.post("/v1/chat/completions", json=request_body)
        hit_latency_ms = (time.perf_counter() - start) * 1000
        assert resp2.status_code == 200
        assert resp2.headers.get("x-condense-cache-hit") == "true"
        assert call_count["n"] == 1
        assert hit_latency_ms < 10, (
            f"Expected cache-hit latency to be single-digit milliseconds, got {hit_latency_ms:.2f}ms "
            f"(cache miss was {miss_latency_ms:.2f}ms)"
        )

    def test_invalid_json(self, client):
        """Invalid JSON body returns 400."""
        resp = client.post(
            "/v1/chat/completions",
            content=b"not json",
            headers={"Content-Type": "application/json"},
        )
        assert resp.status_code == 400

    def test_metrics_endpoint(self, client):
        """Metrics endpoint returns Prometheus format."""
        resp = client.get("/metrics")
        assert resp.status_code == 200
        assert "condense_requests_total" in resp.text

    def test_metrics_summary_endpoint_shape(self, client):
        """Summary endpoint returns structured JSON fields."""
        resp = client.get("/metrics/summary")
        assert resp.status_code == 200
        data = resp.json()
        assert "totals" in data
        assert "rates" in data
        assert "uptime_seconds" in data

        totals = data["totals"]
        assert "total_savings_usd" in totals
        assert "total_tokens_saved_estimate" in totals
        assert "total_prompt_tokens" in totals
        assert "total_completion_tokens" in totals
        assert "total_tokens" in totals

    def test_metrics_summary_counters_increase(self, client, monkeypatch):
        """Summary counters increase after successful chat request."""
        response_data = {
            "id": "chatcmpl-123",
            "object": "chat.completion",
            "choices": [{"message": {"role": "assistant", "content": "Hello!"}, "index": 0, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 12, "completion_tokens": 8, "total_tokens": 20},
        }

        async def fake_acompletion(**kwargs):
            return response_data

        monkeypatch.setattr(
            "condense.pipeline.steps.forward_step.litellm.acompletion",
            fake_acompletion,
        )

        before = client.get("/metrics/summary").json()
        assert before["totals"]["total_requests"] == 0

        resp = client.post(
            "/v1/chat/completions",
            json={
                "model": "gpt-4o",
                "messages": [{"role": "user", "content": "Hello"}],
                "temperature": 0,
            },
        )
        assert resp.status_code == 200

        after = client.get("/metrics/summary").json()
        assert after["totals"]["total_requests"] == 1
        assert after["totals"]["total_prompt_tokens"] == 12
        assert after["totals"]["total_completion_tokens"] == 8
        assert after["totals"]["total_tokens"] == 20

    def test_metrics_summary_token_savings_increase_on_cache_hit(self, client, monkeypatch):
        """Token savings estimate grows when a request is served from cache."""
        response_data = {
            "id": "chatcmpl-123",
            "object": "chat.completion",
            "choices": [{"message": {"role": "assistant", "content": "Cached reply"}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        }

        async def fake_acompletion(**kwargs):
            return response_data

        monkeypatch.setattr(
            "condense.pipeline.steps.forward_step.litellm.acompletion",
            fake_acompletion,
        )

        request_body = {
            "model": "gpt-4o",
            "messages": [{"role": "user", "content": "Token savings cache test"}],
            "temperature": 0,
        }

        # First call warms cache; second call should be cache hit.
        first = client.post("/v1/chat/completions", json=request_body)
        second = client.post("/v1/chat/completions", json=request_body)
        assert first.status_code == 200
        assert second.status_code == 200
        assert second.headers.get("x-condense-cache-hit") == "true"

        summary = client.get("/metrics/summary").json()
        assert summary["totals"]["total_requests"] == 2
        assert summary["totals"]["total_tokens_saved_estimate"] == 15

    def test_dashboard_endpoint_returns_html(self, client):
        """Dashboard endpoint serves HTML that polls summary endpoint."""
        resp = client.get("/dashboard")
        assert resp.status_code == 200
        assert "text/html" in resp.headers.get("content-type", "")
        assert ("Condense Savings Dashboard" in resp.text) or ("<div id=\"root\"></div>" in resp.text)

    def test_metrics_summary_v2_shape(self, client):
        """V2 summary endpoint includes overall + tabs + optimization details."""
        resp = client.get("/metrics/summary/v2")
        assert resp.status_code == 200
        data = resp.json()
        assert "overall" in data
        assert "window" in data
        assert "enabled_tabs" in data
        assert "optimizations" in data
        assert "series" in data
        assert "optimization_series" in data
        assert "cache" in data["enabled_tabs"]

    def test_metrics_summary_v2_window_selector(self, client):
        """V2 summary accepts a time window selector."""
        resp = client.get("/metrics/summary/v2?window=24h")
        assert resp.status_code == 200
        data = resp.json()
        assert data["window"] == "24h"

    def test_metrics_summary_v2_optimization_totals(self, client, monkeypatch):
        """V2 summary aggregates per-optimization updates."""
        response_data = {
            "id": "chatcmpl-123",
            "object": "chat.completion",
            "choices": [{"message": {"role": "assistant", "content": "Hello!"}, "index": 0, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 12, "completion_tokens": 8, "total_tokens": 20},
        }

        async def fake_acompletion(**kwargs):
            return response_data

        monkeypatch.setattr(
            "condense.pipeline.steps.forward_step.litellm.acompletion",
            fake_acompletion,
        )

        request_body = {
            "model": "gpt-4o",
            "messages": [{"role": "user", "content": "Optimization breakdown test"}],
            "temperature": 0,
        }
        client.post("/v1/chat/completions", json=request_body)
        client.post("/v1/chat/completions", json=request_body)

        data = client.get("/metrics/summary/v2").json()
        cache_entry = next((entry for entry in data["optimizations"] if entry["optimization_id"] == "cache"), None)
        assert cache_entry is not None
        assert cache_entry["events"] >= 2
        assert "tokens_saved" in cache_entry

    def test_ui_root_serves_index_or_explicit_missing_message(self, client):
        """UI route either serves built index or explicit missing-build message."""
        resp = client.get("/_ui")
        assert resp.status_code in {200, 503}
        if resp.status_code == 503:
            assert "UI build not found" in resp.text

    def test_metrics_summary_persists_across_restart(self, tmp_path, monkeypatch):
        """SQL-backed summary totals survive app restarts."""
        reset_config_cache()
        config_file = tmp_path / "condense.yaml"
        config_file.write_text(f"""
upstream:
  url: "https://api.openai.com/v1"
  timeout_seconds: 30
optimizations:
  - id: "cache"
    type: "cache"
    enabled: true
    config:
      exact:
        backend: "memory"
        max_size: 100
        ttl_seconds: 60
      non_deterministic: "skip"
deployment:
  port: 8080
metrics:
  endpoint: "/metrics"
  postgres_dsn: "postgresql://condense:condense@localhost:5432/condense"
""")
        response_data = {
            "id": "chatcmpl-persist-1",
            "object": "chat.completion",
            "choices": [{"message": {"role": "assistant", "content": "persist me"}, "index": 0, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 9, "completion_tokens": 3, "total_tokens": 12},
        }

        async def fake_acompletion(**kwargs):
            return response_data

        monkeypatch.setattr(
            "condense.pipeline.steps.forward_step.litellm.acompletion",
            fake_acompletion,
        )

        app_one = create_app(str(config_file))
        with TestClient(app_one) as first_client:
            resp = first_client.post(
                "/v1/chat/completions",
                json={
                    "model": "gpt-4o",
                    "messages": [{"role": "user", "content": "Persist this request"}],
                    "temperature": 0,
                },
            )
            assert resp.status_code == 200

        app_two = create_app(str(config_file))
        with TestClient(app_two) as second_client:
            summary = second_client.get("/metrics/summary").json()
            assert summary["totals"]["total_requests"] == 1
            assert summary["totals"]["total_prompt_tokens"] == 9
            assert summary["totals"]["total_completion_tokens"] == 3
            assert summary["totals"]["total_tokens"] == 12
