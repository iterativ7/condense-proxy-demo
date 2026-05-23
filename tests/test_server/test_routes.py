"""Integration tests for server routes."""

import asyncio
import time

import pytest
from fastapi.testclient import TestClient
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
        assert "Condense Savings Dashboard" in resp.text
        assert "/metrics/summary" in resp.text
