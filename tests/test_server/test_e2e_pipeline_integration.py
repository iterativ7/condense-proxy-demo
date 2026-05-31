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


def test_e2e_rtk_single_backend_compresses_tool_messages(tmp_path, monkeypatch):
    """RTK as single compression backend compresses tool messages e2e."""
    import subprocess
    from unittest.mock import patch

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
      compressor_type: "rtk"
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
        "id": "chatcmpl-rtk-1",
        "object": "chat.completion",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": "Analyzed the test results"},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 15, "completion_tokens": 5, "total_tokens": 20},
    }

    async def fake_acompletion(**kwargs):
        calls.append(kwargs)
        return response_data

    monkeypatch.setattr(
        "condense.pipeline.steps.forward_step.litellm.acompletion",
        fake_acompletion,
    )

    # Mock RTK binary as available and compressing
    tool_output = (
        "running 3 tests\n"
        "test tests::test_add ... ok\n"
        "test tests::test_sub ... ok\n"
        "test tests::test_mul ... FAILED\n"
        "\n"
        "failures:\n" + "x" * 200 + "\n"
        "test result: FAILED. 2 passed; 1 failed; 0 ignored\n"
    )

    def mock_subprocess_run(*args, **kwargs):
        return subprocess.CompletedProcess(
            args=args[0] if args else ["rtk", "pipe"],
            returncode=0,
            stdout="test result: FAILED. 2 passed; 1 failed",
            stderr="",
        )

    with patch(
        "condense.compression.backends.rtk_backend._rtk_binary_path",
        return_value="/usr/bin/rtk",
    ):
        with patch(
            "condense.compression.backends.rtk_backend.subprocess.run",
            side_effect=mock_subprocess_run,
        ):
            with client:
                resp = client.post(
                    "/v1/chat/completions",
                    json={
                        "model": "gpt-4o",
                        "messages": [
                            {"role": "user", "content": "Run the tests"},
                            {"role": "tool", "content": tool_output},
                            {"role": "user", "content": "What failed?"},
                        ],
                        "temperature": 0,
                    },
                )
                assert resp.status_code == 200
                assert resp.json()["choices"][0]["message"]["content"] == "Analyzed the test results"
                # Verify compression was applied
                assert "compression" in resp.headers.get("x-condense-techniques", "")
                # Verify the tool message was compressed
                assert len(calls) == 1
                forwarded_msgs = calls[0]["messages"]
                assert forwarded_msgs[0]["content"] == "Run the tests"  # user unchanged
                assert forwarded_msgs[1]["content"] == "test result: FAILED. 2 passed; 1 failed"  # tool compressed
                assert forwarded_msgs[2]["content"] == "What failed?"  # user unchanged


def test_e2e_chain_rtk_plus_fusion(tmp_path, monkeypatch):
    """Chain with RTK (tool msgs) + fusion (user msgs) should both compress."""
    import subprocess
    from unittest.mock import patch, MagicMock
    from condense.compression.base import CompressResult

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
      chain:
        - backend: "rtk"
          apply_to: ["tool"]
        - backend: "fusion"
          apply_to: ["user"]
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
        "id": "chatcmpl-chain-1",
        "object": "chat.completion",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": "Chain compressed response"},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
    }

    async def fake_acompletion(**kwargs):
        calls.append(kwargs)
        return response_data

    monkeypatch.setattr(
        "condense.pipeline.steps.forward_step.litellm.acompletion",
        fake_acompletion,
    )

    tool_output = "running 3 tests\n" + "detailed output " * 20 + "\ntest result: ok. 3 passed"

    def mock_subprocess_run(*args, **kwargs):
        return subprocess.CompletedProcess(
            args=args[0] if args else ["rtk", "pipe"],
            returncode=0,
            stdout="test result: ok. 3 passed",
            stderr="",
        )

    # Mock RTK binary + fusion backend for chain test
    # Fusion is real but may not compress short text, so we mock it
    mock_fusion = MagicMock()
    mock_fusion.available = True
    mock_fusion.compress_messages.side_effect = lambda msgs: CompressResult(
        messages=[{**m, "content": "[FUSION]" + m.get("content", "")[:30]} for m in msgs],
        original_tokens=sum(len(m.get("content", "")) for m in msgs),
        compressed_tokens=sum(len("[FUSION]" + m.get("content", "")[:30]) for m in msgs),
        reduction_pct=40.0,
    )

    from condense.compression.base import compression_registry

    original_get = compression_registry.get

    def patched_get(name):
        if name == "fusion":
            return lambda **kw: mock_fusion
        return original_get(name)

    with patch(
        "condense.compression.backends.rtk_backend._rtk_binary_path",
        return_value="/usr/bin/rtk",
    ):
        with patch(
            "condense.compression.backends.rtk_backend.subprocess.run",
            side_effect=mock_subprocess_run,
        ):
            with patch.object(compression_registry, "get", side_effect=patched_get):
                with client:
                    resp = client.post(
                        "/v1/chat/completions",
                        json={
                            "model": "gpt-4o",
                            "messages": [
                                {"role": "system", "content": "You are a test assistant."},
                                {"role": "user", "content": "Please run the test suite for me now"},
                                {"role": "tool", "content": tool_output},
                                {"role": "user", "content": "Did they pass?"},
                            ],
                            "temperature": 0,
                        },
                    )
                    assert resp.status_code == 200
                    assert resp.json()["choices"][0]["message"]["content"] == "Chain compressed response"
                    assert "compression" in resp.headers.get("x-condense-techniques", "")

                    # Verify chain applied: tool compressed by RTK, user by fusion
                    assert len(calls) == 1
                    forwarded_msgs = calls[0]["messages"]
                    # System unchanged (no backend targets it)
                    assert forwarded_msgs[0]["content"] == "You are a test assistant."
                    # User messages compressed by fusion
                    assert forwarded_msgs[1]["content"].startswith("[FUSION]")
                    # Tool message compressed by RTK
                    assert forwarded_msgs[2]["content"] == "test result: ok. 3 passed"
                    # Second user also compressed by fusion
                    assert forwarded_msgs[3]["content"].startswith("[FUSION]")


def test_e2e_chain_rtk_unavailable_fusion_still_works(tmp_path, monkeypatch):
    """When RTK is unavailable in a chain, other backends still work."""
    from unittest.mock import patch, MagicMock
    from condense.compression.base import CompressResult, compression_registry
    from condense.pipeline.steps.compression_step import _compressor_cache

    _compressor_cache.clear()

    calls: list[dict] = []
    response_data = {
        "id": "chatcmpl-degrade-1",
        "object": "chat.completion",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": "Degraded ok"},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12},
    }

    async def fake_acompletion(**kwargs):
        calls.append(kwargs)
        return response_data

    monkeypatch.setattr(
        "condense.pipeline.steps.forward_step.litellm.acompletion",
        fake_acompletion,
    )

    mock_fusion = MagicMock()
    mock_fusion.available = True
    mock_fusion.compress_messages.side_effect = lambda msgs: CompressResult(
        messages=[{**m, "content": "[FUSED]"} for m in msgs],
        original_tokens=100,
        compressed_tokens=10,
        reduction_pct=90.0,
    )

    original_get = compression_registry.get

    def patched_get(name):
        if name == "fusion":
            return lambda **kw: mock_fusion
        return original_get(name)

    # Patch BEFORE creating the client so the chain is built with mocked backends
    with patch(
        "condense.compression.backends.rtk_backend._rtk_binary_path",
        return_value=None,
    ):
        with patch.object(compression_registry, "get", side_effect=patched_get):
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
      chain:
        - backend: "rtk"
          apply_to: ["tool"]
        - backend: "fusion"
          apply_to: ["user"]
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
            with client:
                resp = client.post(
                    "/v1/chat/completions",
                    json={
                        "model": "gpt-4o",
                        "messages": [
                            {"role": "user", "content": "Run the tests please"},
                            {"role": "tool", "content": "test result: ok. 3 passed"},
                        ],
                        "temperature": 0,
                    },
                )
                assert resp.status_code == 200
                assert len(calls) == 1
                forwarded_msgs = calls[0]["messages"]
                # User message WAS compressed by fusion
                assert forwarded_msgs[0]["content"] == "[FUSED]"
                # Tool message was NOT compressed (RTK unavailable) — passed through
                assert forwarded_msgs[1]["content"] == "test result: ok. 3 passed"

    _compressor_cache.clear()


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
