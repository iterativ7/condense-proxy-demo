"""Tests for ModelRouter — ML-based model routing."""

from unittest.mock import MagicMock, patch

import pytest

from condense.routing.model_router import ModelRouter, _messages_to_query


class TestMessagesToQuery:
    """Tests for query extraction from chat messages."""

    def test_simple_string_content(self):
        messages = [{"role": "user", "content": "Hello world"}]
        assert _messages_to_query(messages) == "Hello world"

    def test_multiple_messages(self):
        messages = [
            {"role": "system", "content": "Be helpful."},
            {"role": "user", "content": "Hello"},
        ]
        result = _messages_to_query(messages)
        assert "Be helpful." in result
        assert "Hello" in result

    def test_structured_content_blocks(self):
        """Handles OpenAI vision-style content blocks."""
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Describe this image"},
                    {"type": "image_url", "image_url": {"url": "http://example.com/img.png"}},
                ],
            }
        ]
        result = _messages_to_query(messages)
        assert result == "Describe this image"

    def test_empty_messages(self):
        assert _messages_to_query([]) == "."

    def test_empty_content(self):
        messages = [{"role": "user", "content": ""}]
        assert _messages_to_query(messages) == "."

    def test_truncation_at_max_chars(self):
        long_text = "A" * 20_000
        messages = [{"role": "user", "content": long_text}]
        result = _messages_to_query(messages, max_chars=100)
        assert len(result) == 100

    def test_missing_content_key(self):
        messages = [{"role": "user"}]
        assert _messages_to_query(messages) == "."

    def test_mixed_content_types(self):
        """Mix of string and structured content across messages."""
        messages = [
            {"role": "system", "content": "System prompt"},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "User query"},
                ],
            },
        ]
        result = _messages_to_query(messages)
        assert "System prompt" in result
        assert "User query" in result


class TestModelRouterInit:
    """Tests for ModelRouter initialization and graceful degradation."""

    def test_unavailable_when_llmrouter_not_installed(self):
        """Router should be unavailable when llmrouter-lib is not installed."""
        with patch.dict("sys.modules", {"llmrouter": None, "llmrouter.cli": None, "llmrouter.cli.router_inference": None}):
            # Force ImportError by patching the import
            router = ModelRouter.__new__(ModelRouter)
            router.strong = "gpt-4o"
            router.weak = "gpt-4o-mini"
            router.threshold = 0.5
            router.router_type = "smallest_llm"
            router.config_path = None
            router._key_to_litellm = {"weak": "gpt-4o-mini", "strong": "gpt-4o"}
            router._router = router._load_router()
            assert not router.available

    def test_unavailable_when_config_path_missing(self):
        """Router should be unavailable when config_path doesn't exist."""
        with patch(
            "condense.routing.model_router.ModelRouter._load_router",
            return_value=None,
        ):
            router = ModelRouter(
                router_type="trained_strategy",
                config_path="/nonexistent/path.yaml",
            )
            assert not router.available

    def test_route_returns_none_when_unavailable(self):
        """route() should return None when router is unavailable."""
        with patch(
            "condense.routing.model_router.ModelRouter._load_router",
            return_value=None,
        ):
            router = ModelRouter()
            result = router.route({
                "model": "gpt-4o",
                "messages": [{"role": "user", "content": "Hello"}],
            })
            assert result is None

    def test_default_parameters(self):
        """Check default parameter values."""
        with patch(
            "condense.routing.model_router.ModelRouter._load_router",
            return_value=None,
        ):
            router = ModelRouter()
            assert router.strong == "gpt-4o"
            assert router.weak == "gpt-4o-mini"
            assert router.threshold == 0.5
            assert router.router_type == "smallest_llm"
            assert router.config_path is None


class TestModelRouterRouting:
    """Tests for ModelRouter.route() with mocked LLMRouter backend."""

    def _make_router_with_mock(self, route_result: dict) -> ModelRouter:
        """Create a ModelRouter with a mocked LLMRouter backend."""
        mock_llm_router = MagicMock()
        mock_llm_router.route_single.return_value = route_result

        with patch(
            "condense.routing.model_router.ModelRouter._load_router",
            return_value=mock_llm_router,
        ):
            router = ModelRouter(strong="gpt-4o", weak="gpt-4o-mini")
        return router

    def test_routes_to_weak_model(self):
        """Router returns weak model key → resolved to gpt-4o-mini."""
        router = self._make_router_with_mock({"model_name": "weak"})
        result = router.route({
            "model": "gpt-4o",
            "messages": [{"role": "user", "content": "Hi"}],
        })
        assert result == "gpt-4o-mini"

    def test_routes_to_strong_model(self):
        """Router returns strong model key → resolved to gpt-4o."""
        router = self._make_router_with_mock({"model_name": "strong"})
        result = router.route({
            "model": "gpt-4o",
            "messages": [{"role": "user", "content": "Explain quantum physics"}],
        })
        assert result == "gpt-4o"

    def test_predicted_llm_key(self):
        """Router uses predicted_llm field when model_name is absent."""
        router = self._make_router_with_mock({"predicted_llm": "weak"})
        result = router.route({
            "model": "gpt-4o",
            "messages": [{"role": "user", "content": "Hi"}],
        })
        assert result == "gpt-4o-mini"

    def test_predicted_llm_name_key(self):
        """Router uses predicted_llm_name field as last fallback."""
        router = self._make_router_with_mock({"predicted_llm_name": "strong"})
        result = router.route({
            "model": "gpt-4o",
            "messages": [{"role": "user", "content": "Hi"}],
        })
        assert result == "gpt-4o"

    def test_no_model_in_result(self):
        """Returns None when routing result has no model key."""
        router = self._make_router_with_mock({"something_else": "value"})
        result = router.route({
            "model": "gpt-4o",
            "messages": [{"role": "user", "content": "Hi"}],
        })
        assert result is None

    def test_empty_routing_result(self):
        """Returns None for empty routing result."""
        router = self._make_router_with_mock({})
        result = router.route({
            "model": "gpt-4o",
            "messages": [{"role": "user", "content": "Hi"}],
        })
        assert result is None

    def test_unknown_key_returned_as_is(self):
        """Unknown model keys are returned verbatim."""
        router = self._make_router_with_mock({"model_name": "custom-model-xyz"})
        result = router.route({
            "model": "gpt-4o",
            "messages": [{"role": "user", "content": "Hi"}],
        })
        assert result == "custom-model-xyz"

    def test_route_exception_returns_none(self):
        """Exceptions during routing return None gracefully."""
        mock_llm_router = MagicMock()
        mock_llm_router.route_single.side_effect = RuntimeError("routing failed")

        with patch(
            "condense.routing.model_router.ModelRouter._load_router",
            return_value=mock_llm_router,
        ):
            router = ModelRouter()

        result = router.route({
            "model": "gpt-4o",
            "messages": [{"role": "user", "content": "Hi"}],
        })
        assert result is None

    def test_route_with_empty_messages(self):
        """Routing with empty messages list should not crash."""
        router = self._make_router_with_mock({"model_name": "weak"})
        result = router.route({"model": "gpt-4o", "messages": []})
        assert result == "gpt-4o-mini"


class TestModelResolution:
    """Tests for _resolve_litellm_model."""

    def test_resolve_weak_key(self):
        with patch(
            "condense.routing.model_router.ModelRouter._load_router",
            return_value=None,
        ):
            router = ModelRouter(strong="claude-3-opus", weak="claude-3-haiku")
        assert router._resolve_litellm_model("weak") == "claude-3-haiku"
        assert router._resolve_litellm_model("strong") == "claude-3-opus"

    def test_resolve_unknown_key_passthrough(self):
        with patch(
            "condense.routing.model_router.ModelRouter._load_router",
            return_value=None,
        ):
            router = ModelRouter()
        assert router._resolve_litellm_model("some-custom-model") == "some-custom-model"

    def test_resolve_from_llm_data(self):
        """Resolves model from router's llm_data attribute."""
        mock_router = MagicMock()
        mock_router.llm_data = {
            "medium": {"model": "gpt-4o-2024-05-13", "size": "30B"}
        }
        with patch(
            "condense.routing.model_router.ModelRouter._load_router",
            return_value=mock_router,
        ):
            router = ModelRouter()
        assert router._resolve_litellm_model("medium") == "gpt-4o-2024-05-13"
