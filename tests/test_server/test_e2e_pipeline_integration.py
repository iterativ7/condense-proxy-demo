"""End-to-end integration tests for pipeline behavior through HTTP routes."""

from fastapi.testclient import TestClient

from condense.config.loader import reset_config_cache
from condense.server.app import create_app


def _make_client(tmp_path, config_yaml: str) -> TestClient:
    reset_config_cache()
    config_file = tmp_path / "condense.yaml"
    config_file.write_text(config_yaml)
    app = create_app(str(config_file))
    return TestClient(app)


def test_e2e_routing_then_cache_hit(tmp_path, monkeypatch):
    """A real chat request should route, forward, then be served from cache."""
    client = _make_client(
        tmp_path,
        """
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
        ttl_seconds: 120
      non_deterministic: "skip"
  - id: "routing"
    type: "routing"
    enabled: true
    depends_on: ["cache"]
    config:
      rules:
        - condition: "short_messages"
          max_chars: 120
          model: "gpt-4o-mini"
  - id: "budget"
    type: "budget"
    enabled: false
    config: {}
deployment:
  port: 8080
""",
    )

    calls: list[dict] = []
    response_data = {
        "id": "chatcmpl-e2e-1",
        "object": "chat.completion",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": "Routed response"},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 8, "completion_tokens": 5, "total_tokens": 13},
    }

    async def fake_acompletion(**kwargs):
        calls.append(kwargs)
        return response_data

    monkeypatch.setattr(
        "condense.pipeline.steps.forward_step.litellm.acompletion",
        fake_acompletion,
    )

    request_body = {
        "model": "gpt-4o",
        "messages": [{"role": "user", "content": "Quick hello"}],
        "temperature": 0,
    }

    with client:
        first = client.post("/v1/chat/completions", json=request_body)
        assert first.status_code == 200
        assert first.json()["choices"][0]["message"]["content"] == "Routed response"
        assert first.headers.get("x-condense-routed-model") == "gpt-4o-mini"
        assert first.headers.get("x-condense-cache-hit") == "false"
        assert len(calls) == 1
        forwarded_payload = {
            "model": calls[0]["model"],
            "messages": calls[0]["messages"],
        }
        assert forwarded_payload["model"] == "gpt-4o-mini"

        second = client.post("/v1/chat/completions", json=request_body)
        assert second.status_code == 200
        assert second.json()["choices"][0]["message"]["content"] == "Routed response"
        assert second.headers.get("x-condense-cache-hit") == "true"
        assert len(calls) == 1


def test_e2e_budget_rejects_after_turn_limit(tmp_path, monkeypatch):
    """Budget optimization should reject requests after session turn cap."""
    client = _make_client(
        tmp_path,
        """
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
      max_session_cost_usd: 999.0
      max_turns_per_session: 1
      loop_detection_window: 0
deployment:
  port: 8080
""",
    )

    calls = {"n": 0}
    response_data = {
        "id": "chatcmpl-e2e-2",
        "object": "chat.completion",
        "choices": [{"index": 0, "message": {"role": "assistant", "content": "First ok"}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 4, "total_tokens": 14},
    }

    async def fake_acompletion(**kwargs):
        calls["n"] += 1
        return response_data

    monkeypatch.setattr(
        "condense.pipeline.steps.forward_step.litellm.acompletion",
        fake_acompletion,
    )

    request_body = {
        "model": "gpt-4o",
        "messages": [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "Track my budgeted conversation"},
        ],
        "temperature": 0,
    }

    with client:
        first = client.post("/v1/chat/completions", json=request_body)
        assert first.status_code == 200
        assert first.json()["choices"][0]["message"]["content"] == "First ok"
        assert calls["n"] == 1

        second = client.post("/v1/chat/completions", json=request_body)
        assert second.status_code == 429
        assert "Session turn limit exceeded" in second.json()["error"]["message"]
        assert calls["n"] == 1
