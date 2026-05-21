"""Tests for ModelRouter — ML-based model routing with pluggable backends."""

from unittest.mock import MagicMock, patch

import pytest

from condense.routing.model_router import (
    ModelRouter,
    _LLMRouterBackend,
    _RouteLLMBackend,
    _messages_to_query,
)


# -----------------------------------------------------------------------
# _messages_to_query
# -----------------------------------------------------------------------

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


# -----------------------------------------------------------------------
# ModelRouter init and backend selection
# -----------------------------------------------------------------------

class TestModelRouterBackendSelection:
    """Tests that the correct backend is selected based on router_type."""

    def test_bert_selects_routellm_backend(self):
        with patch.object(_RouteLLMBackend, "_load", return_value=None):
            router = ModelRouter(router_type="bert")
        assert isinstance(router._backend, _RouteLLMBackend)

    def test_mf_selects_routellm_backend(self):
        with patch.object(_RouteLLMBackend, "_load", return_value=None):
            router = ModelRouter(router_type="mf")
        assert isinstance(router._backend, _RouteLLMBackend)

    def test_sw_ranking_selects_routellm_backend(self):
        with patch.object(_RouteLLMBackend, "_load", return_value=None):
            router = ModelRouter(router_type="sw_ranking")
        assert isinstance(router._backend, _RouteLLMBackend)

    def test_smallest_llm_selects_llmrouter_backend(self):
        with patch.object(_LLMRouterBackend, "_load", return_value=None):
            router = ModelRouter(router_type="smallest_llm")
        assert isinstance(router._backend, _LLMRouterBackend)

    def test_largest_llm_selects_llmrouter_backend(self):
        with patch.object(_LLMRouterBackend, "_load", return_value=None):
            router = ModelRouter(router_type="largest_llm")
        assert isinstance(router._backend, _LLMRouterBackend)

    def test_unknown_strategy_selects_llmrouter_backend(self):
        with patch.object(_LLMRouterBackend, "_load", return_value=None):
            router = ModelRouter(router_type="custom_trained")
        assert isinstance(router._backend, _LLMRouterBackend)


class TestModelRouterInit:
    """Tests for ModelRouter initialization and graceful degradation."""

    def test_unavailable_when_no_backend(self):
        """Router is unavailable when backend fails to load."""
        with patch.object(_RouteLLMBackend, "_load", return_value=None):
            router = ModelRouter(router_type="bert")
        assert not router.available

    def test_route_returns_none_when_unavailable(self):
        """route() returns None when router is unavailable."""
        with patch.object(_RouteLLMBackend, "_load", return_value=None):
            router = ModelRouter(router_type="bert")
        result = router.route({
            "model": "gpt-4o",
            "messages": [{"role": "user", "content": "Hello"}],
        })
        assert result is None

    def test_default_parameters(self):
        with patch.object(_RouteLLMBackend, "_load", return_value=None):
            router = ModelRouter()
        assert router.strong == "gpt-4o"
        assert router.weak == "gpt-4o-mini"
        assert router.threshold == 0.5
        assert router.router_type == "bert"
        assert router.config_path is None


# -----------------------------------------------------------------------
# RouteLLM backend routing
# -----------------------------------------------------------------------

class TestRouteLLMBackendRouting:
    """Tests for _RouteLLMBackend.route() with mocked Controller."""

    def _make_backend(self, route_return: str) -> _RouteLLMBackend:
        mock_controller = MagicMock()
        mock_controller.route.return_value = route_return
        with patch.object(_RouteLLMBackend, "_load", return_value=mock_controller):
            return _RouteLLMBackend(
                router_type="bert",
                strong="gpt-4o",
                weak="gpt-4o-mini",
                threshold=0.5,
            )

    def test_routes_to_weak(self):
        backend = self._make_backend("gpt-4o-mini")
        assert backend.route("Hi") == "gpt-4o-mini"

    def test_routes_to_strong(self):
        backend = self._make_backend("gpt-4o")
        assert backend.route("Explain quantum physics") == "gpt-4o"

    def test_exception_returns_none(self):
        mock_controller = MagicMock()
        mock_controller.route.side_effect = RuntimeError("boom")
        with patch.object(_RouteLLMBackend, "_load", return_value=mock_controller):
            backend = _RouteLLMBackend("bert", "gpt-4o", "gpt-4o-mini", 0.5)
        assert backend.route("test") is None


# -----------------------------------------------------------------------
# LLMRouter backend routing
# -----------------------------------------------------------------------

class TestLLMRouterBackendRouting:
    """Tests for _LLMRouterBackend.route() with mocked router."""

    def _make_backend(self, route_result: dict) -> _LLMRouterBackend:
        mock_router = MagicMock()
        mock_router.route_single.return_value = route_result
        with patch.object(_LLMRouterBackend, "_load", return_value=mock_router):
            return _LLMRouterBackend(
                router_type="smallest_llm",
                strong="gpt-4o",
                weak="gpt-4o-mini",
                config_path=None,
            )

    def test_routes_to_weak_model(self):
        backend = self._make_backend({"model_name": "weak"})
        assert backend.route("Hi") == "gpt-4o-mini"

    def test_routes_to_strong_model(self):
        backend = self._make_backend({"model_name": "strong"})
        assert backend.route("complex query") == "gpt-4o"

    def test_predicted_llm_key(self):
        backend = self._make_backend({"predicted_llm": "weak"})
        assert backend.route("Hi") == "gpt-4o-mini"

    def test_no_model_in_result(self):
        backend = self._make_backend({"something_else": "value"})
        assert backend.route("Hi") is None

    def test_empty_routing_result(self):
        backend = self._make_backend({})
        assert backend.route("Hi") is None

    def test_unknown_key_returned_as_is(self):
        backend = self._make_backend({"model_name": "custom-xyz"})
        assert backend.route("Hi") == "custom-xyz"

    def test_exception_returns_none(self):
        mock_router = MagicMock()
        mock_router.route_single.side_effect = RuntimeError("fail")
        with patch.object(_LLMRouterBackend, "_load", return_value=mock_router):
            backend = _LLMRouterBackend("smallest_llm", "gpt-4o", "gpt-4o-mini", None)
        assert backend.route("test") is None


# -----------------------------------------------------------------------
# ModelRouter.route() end-to-end with mocked backend
# -----------------------------------------------------------------------

class TestModelRouterRoute:
    """Tests for ModelRouter.route() with mocked backends."""

    def _make_router(self, backend_route_return):
        mock_backend = MagicMock()
        mock_backend.available = backend_route_return is not None
        mock_backend.route.return_value = backend_route_return
        with patch.object(_RouteLLMBackend, "__init__", return_value=None):
            router = ModelRouter.__new__(ModelRouter)
        router.strong = "gpt-4o"
        router.weak = "gpt-4o-mini"
        router.threshold = 0.5
        router.router_type = "bert"
        router.config_path = None
        router._backend = mock_backend
        return router

    def test_route_returns_chosen_model(self):
        router = self._make_router("gpt-4o-mini")
        result = router.route({
            "model": "gpt-4o",
            "messages": [{"role": "user", "content": "Hi"}],
        })
        assert result == "gpt-4o-mini"

    def test_route_with_empty_messages(self):
        router = self._make_router("gpt-4o-mini")
        result = router.route({"model": "gpt-4o", "messages": []})
        assert result == "gpt-4o-mini"

    def test_route_returns_none_when_backend_unavailable(self):
        router = self._make_router(None)
        result = router.route({
            "model": "gpt-4o",
            "messages": [{"role": "user", "content": "Hi"}],
        })
        assert result is None


# -----------------------------------------------------------------------
# Real ML classification tests (require routellm installed)
# -----------------------------------------------------------------------

class TestRouteLLMBertClassification:
    """Integration tests using real RouteLLM BERT router.

    These tests verify that the BERT router genuinely classifies query
    complexity — not just that the plumbing works. The BERT model runs
    fully offline (no API keys needed).

    Skipped if routellm is not installed.
    """

    @pytest.fixture(autouse=True)
    def _check_routellm(self):
        try:
            import os
            os.environ.setdefault("OPENAI_API_KEY", "sk-placeholder")
            import routellm  # noqa: F401
        except ImportError:
            pytest.skip("routellm not installed")

    @pytest.fixture(scope="class")
    def bert_router(self):
        """Create a real BERT-backed ModelRouter (cached across tests in class)."""
        import os
        os.environ.setdefault("OPENAI_API_KEY", "sk-placeholder")
        return ModelRouter(
            strong="gpt-4o",
            weak="gpt-4o-mini",
            threshold=0.5,
            router_type="bert",
        )

    def test_router_is_available(self, bert_router):
        """BERT router should load successfully."""
        assert bert_router.available

    def test_simple_queries_route_to_weak(self, bert_router):
        """Simple greetings should be routed to the weak (cheap) model."""
        simple_queries = [
            "Hi",
            "What is 2+2?",
            "Hello, how are you?",
            "What color is the sky?",
        ]
        results = []
        for q in simple_queries:
            result = bert_router.route({
                "model": "gpt-4o",
                "messages": [{"role": "user", "content": q}],
            })
            results.append(result)

        weak_count = sum(1 for r in results if r == "gpt-4o-mini")
        # At threshold 0.5, at least 3 out of 4 simple queries should go to weak
        assert weak_count >= 3, (
            f"Expected at least 3/4 simple queries to route to weak model, "
            f"got {weak_count}/4. Results: {list(zip(simple_queries, results))}"
        )

    def test_complex_query_scores_higher_than_simple(self, bert_router):
        """The BERT router should assign a higher strong-win-rate to complex
        queries than to simple ones, proving real ML classification."""
        import os
        os.environ.setdefault("OPENAI_API_KEY", "sk-placeholder")
        from routellm.controller import Controller

        controller = Controller(
            routers=["bert"],
            strong_model="gpt-4o",
            weak_model="gpt-4o-mini",
        )
        bert = controller.routers["bert"]

        simple_score = bert.calculate_strong_win_rate("Hi")
        complex_score = bert.calculate_strong_win_rate(
            "Derive the Navier-Stokes equations from first principles "
            "and explain each step in detail"
        )

        assert complex_score > simple_score, (
            f"Complex query should score higher than simple. "
            f"Simple={simple_score:.3f}, Complex={complex_score:.3f}"
        )

    def test_threshold_affects_routing(self, bert_router):
        """Lower threshold should route more queries to the strong model."""
        query = {
            "model": "gpt-4o",
            "messages": [{"role": "user", "content": "Tell me a joke"}],
        }

        # High threshold — more likely to go to weak
        high_t_router = ModelRouter(
            strong="gpt-4o", weak="gpt-4o-mini",
            threshold=0.9, router_type="bert",
        )
        # Low threshold — more likely to go to strong
        low_t_router = ModelRouter(
            strong="gpt-4o", weak="gpt-4o-mini",
            threshold=0.1, router_type="bert",
        )

        high_result = high_t_router.route(query)
        low_result = low_t_router.route(query)

        # With threshold=0.9, "Tell me a joke" should go to weak
        assert high_result == "gpt-4o-mini", f"High threshold should route to weak, got {high_result}"
        # With threshold=0.1, "Tell me a joke" should go to strong
        assert low_result == "gpt-4o", f"Low threshold should route to strong, got {low_result}"
