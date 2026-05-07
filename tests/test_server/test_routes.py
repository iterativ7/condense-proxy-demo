"""Integration tests for server routes."""

import pytest
import respx
import httpx
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
  cache:
    enabled: true
    exact:
      backend: "memory"
      max_size: 100
      ttl_seconds: 60
    non_deterministic: "skip"
  provider_cache:
    enabled: false
  routing:
    enabled: false
  budget:
    enabled: false
deployment:
  port: 8080
""")
    app = create_app(str(config_file))
    with TestClient(app) as c:
        yield c


class TestChatCompletionsRoute:
    @respx.mock
    def test_passthrough(self, client):
        """Request is forwarded to upstream and response returned."""
        response_data = {
            "id": "chatcmpl-123",
            "object": "chat.completion",
            "choices": [{"message": {"role": "assistant", "content": "Hello!"}, "index": 0, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        }
        respx.post("https://api.openai.com/v1/chat/completions").mock(
            return_value=httpx.Response(200, json=response_data)
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

    @respx.mock
    def test_condense_headers(self, client):
        """Response includes X-Condense-* headers."""
        response_data = {
            "id": "chatcmpl-123",
            "choices": [{"message": {"content": "Hi"}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        }
        respx.post("https://api.openai.com/v1/chat/completions").mock(
            return_value=httpx.Response(200, json=response_data)
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

    @respx.mock
    def test_cache_hit_on_second_request(self, client):
        """Second identical request returns cache hit."""
        response_data = {
            "id": "chatcmpl-123",
            "choices": [{"message": {"content": "Hi"}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        }
        respx.post("https://api.openai.com/v1/chat/completions").mock(
            return_value=httpx.Response(200, json=response_data)
        )

        request_body = {
            "model": "gpt-4o",
            "messages": [{"role": "user", "content": "Unique cache test message 12345"}],
            "temperature": 0,
        }

        # First request — cache miss
        resp1 = client.post("/v1/chat/completions", json=request_body)
        assert resp1.status_code == 200
        assert resp1.headers.get("x-condense-cache-hit") == "false"

        # Second request — cache hit
        resp2 = client.post("/v1/chat/completions", json=request_body)
        assert resp2.status_code == 200
        assert resp2.headers.get("x-condense-cache-hit") == "true"

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
