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


def test_e2e_model_routing_with_cache(tmp_path, monkeypatch):
    """Model routing should route, forward, and cache — second request is a cache hit."""
    from unittest.mock import MagicMock, patch

    # Create a mock ModelRouter that always routes to weak model
    mock_router = MagicMock()
    mock_router.available = True
    mock_router.route.return_value = "gpt-4o-mini"

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
      model_routing:
        enabled: true
        strong: "gpt-4o"
        weak: "gpt-4o-mini"
        router_type: "smallest_llm"
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
        "id": "chatcmpl-mr-1",
        "object": "chat.completion",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": "Model routed response"},
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

    with patch(
        "condense.pipeline.steps.routing_step._get_or_create_model_router",
        return_value=mock_router,
    ):
        with client:
            # First request — model routing + forward
            first = client.post("/v1/chat/completions", json=request_body)
            assert first.status_code == 200
            assert first.json()["choices"][0]["message"]["content"] == "Model routed response"
            assert first.headers.get("x-condense-routed-model") == "gpt-4o-mini"
            assert first.headers.get("x-condense-cache-hit") == "false"
            assert len(calls) == 1
            assert calls[0]["model"] == "gpt-4o-mini"

            # Second request — cache hit, no forward call
            second = client.post("/v1/chat/completions", json=request_body)
            assert second.status_code == 200
            assert second.headers.get("x-condense-cache-hit") == "true"
            assert len(calls) == 1  # no additional forward call


def test_e2e_cache_token_savings_surface_in_dashboard_payload(tmp_path, monkeypatch):
    """Cache hit token savings should be reflected in the UI summary payload."""
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
    enabled: false
    config:
      rules: []
  - id: "budget"
    type: "budget"
    enabled: false
    config: {}
deployment:
  port: 8080
""",
    )

    response_data = {
        "id": "chatcmpl-e2e-cache-ui-1",
        "object": "chat.completion",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": "Cached dashboard response"},
                "finish_reason": "stop",
            }
        ],
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
        "messages": [{"role": "user", "content": "Cache token savings into UI"}],
        "temperature": 0,
    }

    with client:
        warm = client.post("/v1/chat/completions", json=request_body)
        assert warm.status_code == 200
        cached = client.post("/v1/chat/completions", json=request_body)
        assert cached.status_code == 200
        assert cached.headers.get("x-condense-cache-hit") == "true"

        summary = client.get("/metrics/summary/v2?window=all_time")
        assert summary.status_code == 200
        payload = summary.json()

        # These are the exact fields the modular UI reads and displays.
        assert payload["overall"]["total_tokens_saved_estimate"] == 15
        assert payload["overall"]["total_requests"] == 2
        assert payload["window"] == "all_time"
        assert "series" in payload
        assert "optimization_series" in payload

        cache_entry = next(
            (entry for entry in payload["optimizations"] if entry["optimization_id"] == "cache"),
            None,
        )
        assert cache_entry is not None
        assert cache_entry["total_tokens_saved"] == 15
        assert cache_entry["events"] >= 2


def test_e2e_model_routing_fallback_to_rules(tmp_path, monkeypatch):
    """When model routing returns None, rule-based routing serves as fallback."""
    from unittest.mock import MagicMock, patch

    mock_router = MagicMock()
    mock_router.available = True
    mock_router.route.return_value = None  # No ML routing decision

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
  - id: "routing"
    type: "routing"
    enabled: true
    config:
      model_routing:
        enabled: true
        strong: "gpt-4o"
        weak: "gpt-4o-mini"
        router_type: "smallest_llm"
      rules:
        - condition: "short_messages"
          max_chars: 200
          model: "gpt-4o-mini"
deployment:
  port: 8080
""",
    )

    calls: list[dict] = []
    response_data = {
        "id": "chatcmpl-fb-1",
        "object": "chat.completion",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": "Fallback routed"},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8},
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
        "messages": [{"role": "user", "content": "Short msg"}],
        "temperature": 0,
    }

    with patch(
        "condense.pipeline.steps.routing_step._get_or_create_model_router",
        return_value=mock_router,
    ):
        with client:
            resp = client.post("/v1/chat/completions", json=request_body)
            assert resp.status_code == 200
            # ML routing returned None, so rule-based routing kicked in
            assert resp.headers.get("x-condense-routed-model") == "gpt-4o-mini"
            assert len(calls) == 1
            assert calls[0]["model"] == "gpt-4o-mini"


def test_e2e_compression_reduces_tokens(tmp_path, monkeypatch):
    """Compression step should compress messages before forwarding."""
    from unittest.mock import MagicMock, patch
    from condense.compression.base import CompressResult

    mock_compressor = MagicMock()
    mock_compressor.available = True
    mock_compressor.compress_messages.return_value = CompressResult(
        messages=[{"role": "user", "content": "compressed msg"}],
        original_tokens=50,
        compressed_tokens=30,
        reduction_pct=40.0,
    )

    client = _make_client(
        tmp_path,
        """
upstream:
  url: "https://api.openai.com/v1"
  timeout_seconds: 30
optimizations:
  - id: "compression"
    type: "compression"
    enabled: true
    config:
      compressor_type: "fusion"
  - id: "cache"
    type: "cache"
    enabled: false
    config: {}
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
        "id": "chatcmpl-comp-1",
        "object": "chat.completion",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": "Compressed response"},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 30, "completion_tokens": 5, "total_tokens": 35},
    }

    async def fake_acompletion(**kwargs):
        calls.append(kwargs)
        return response_data

    monkeypatch.setattr(
        "condense.pipeline.steps.forward_step.litellm.acompletion",
        fake_acompletion,
    )

    with patch(
        "condense.pipeline.steps.compression_step._get_or_create_compressor",
        return_value=mock_compressor,
    ):
        with client:
            resp = client.post(
                "/v1/chat/completions",
                json={
                    "model": "gpt-4o",
                    "messages": [{"role": "user", "content": "A long verbose message here"}],
                    "temperature": 0,
                },
            )
            assert resp.status_code == 200
            assert resp.json()["choices"][0]["message"]["content"] == "Compressed response"
            # Verify compression technique was applied
            assert "compression" in resp.headers.get("x-condense-techniques", "")
            # Verify the forwarded messages were compressed
            assert len(calls) == 1
            assert calls[0]["messages"] == [{"role": "user", "content": "compressed msg"}]


def test_e2e_compression_with_routing(tmp_path, monkeypatch):
    """Compression + routing should both apply in pipeline order."""
    from unittest.mock import MagicMock, patch
    from condense.compression.base import CompressResult

    mock_compressor = MagicMock()
    mock_compressor.available = True
    mock_compressor.compress_messages.return_value = CompressResult(
        messages=[{"role": "user", "content": "compressed"}],
        original_tokens=100,
        compressed_tokens=50,
        reduction_pct=50.0,
    )

    client = _make_client(
        tmp_path,
        """
upstream:
  url: "https://api.openai.com/v1"
  timeout_seconds: 30
optimizations:
  - id: "compression"
    type: "compression"
    enabled: true
    config:
      compressor_type: "fusion"
  - id: "routing"
    type: "routing"
    enabled: true
    depends_on: ["compression"]
    config:
      rules:
        - condition: "short_messages"
          max_chars: 500
          model: "gpt-4o-mini"
  - id: "cache"
    type: "cache"
    enabled: false
    config: {}
deployment:
  port: 8080
""",
    )

    calls: list[dict] = []
    response_data = {
        "id": "chatcmpl-cr-1",
        "object": "chat.completion",
        "choices": [{"index": 0, "message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 1, "total_tokens": 11},
    }

    async def fake_acompletion(**kwargs):
        calls.append(kwargs)
        return response_data

    monkeypatch.setattr(
        "condense.pipeline.steps.forward_step.litellm.acompletion",
        fake_acompletion,
    )

    with patch(
        "condense.pipeline.steps.compression_step._get_or_create_compressor",
        return_value=mock_compressor,
    ):
        with client:
            resp = client.post(
                "/v1/chat/completions",
                json={"model": "gpt-4o", "messages": [{"role": "user", "content": "Hi"}], "temperature": 0},
            )
            assert resp.status_code == 200
            techniques = resp.headers.get("x-condense-techniques", "")
            assert "compression" in techniques
            assert "routing" in techniques
            assert calls[0]["model"] == "gpt-4o-mini"


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
