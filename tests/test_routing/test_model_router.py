"""Tests for ModelRouter and the routing backend registry."""

from unittest.mock import MagicMock, patch

import pytest

from condense.backends.registry import BackendRegistry
from condense.routing.base import RoutingBackend, routing_registry
from condense.routing.model_router import ModelRouter, _messages_to_query


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
        messages = [
            {"role": "system", "content": "System prompt"},
            {"role": "user", "content": [{"type": "text", "text": "User query"}]},
        ]
        result = _messages_to_query(messages)
        assert "System prompt" in result
        assert "User query" in result


# -----------------------------------------------------------------------
# Backend registry
# -----------------------------------------------------------------------

class TestBackendRegistry:
    """Tests for the generic BackendRegistry."""

    def test_register_and_get(self):
        reg = BackendRegistry("test")

        @reg.register("foo")
        class Foo:
            pass

        assert reg.get("foo") is Foo
        assert "foo" in reg
        assert len(reg) == 1

    def test_get_normalizes_name(self):
        reg = BackendRegistry("test")

        @reg.register("my_backend")
        class MyBackend:
            pass

        assert reg.get("my-backend") is MyBackend
        assert reg.get("MY_BACKEND") is MyBackend

    def test_duplicate_raises(self):
        reg = BackendRegistry("test")

        @reg.register("dup")
        class First:
            pass

        with pytest.raises(ValueError, match="already registered"):
            @reg.register("dup")
            class Second:
                pass

    def test_get_or_raise_unknown(self):
        reg = BackendRegistry("test")
        with pytest.raises(KeyError, match="unknown backend"):
            reg.get_or_raise("nonexistent")

    def test_available_names(self):
        reg = BackendRegistry("test")

        @reg.register("b")
        class B:
            pass

        @reg.register("a")
        class A:
            pass

        assert reg.available_names() == ["a", "b"]


class TestRoutingRegistry:
    """Tests for the routing-specific registry."""

    def test_builtin_backends_registered(self):
        """All built-in routing backends should be auto-registered."""
        expected = {"bert", "mf", "causal_llm", "sw_ranking", "random",
                    "smallest_llm", "largest_llm"}
        registered = set(routing_registry.available_names())
        assert expected.issubset(registered), (
            f"Missing backends: {expected - registered}"
        )

    def test_all_backends_are_routing_backends(self):
        """Every registered backend should be a RoutingBackend subclass."""
        for name in routing_registry.available_names():
            cls = routing_registry.get(name)
            assert issubclass(cls, RoutingBackend), (
                f"{name} → {cls} is not a RoutingBackend subclass"
            )


# -----------------------------------------------------------------------
# ModelRouter with mocked backend
# -----------------------------------------------------------------------

class TestModelRouterInit:
    """Tests for ModelRouter initialization and graceful degradation."""

    def test_unknown_router_type_is_unavailable(self):
        router = ModelRouter(router_type="totally_unknown_xyz")
        assert not router.available

    def test_route_returns_none_when_unavailable(self):
        router = ModelRouter(router_type="totally_unknown_xyz")
        result = router.route({
            "model": "gpt-4o",
            "messages": [{"role": "user", "content": "Hello"}],
        })
        assert result is None

    def test_default_parameters(self):
        # Create with a mock to avoid loading real backend
        with patch.object(routing_registry, "get", return_value=None):
            router = ModelRouter()
        assert router.strong == "gpt-4o"
        assert router.weak == "gpt-4o-mini"
        assert router.threshold == 0.5
        assert router.router_type == "bert"


class TestModelRouterRoute:
    """Tests for ModelRouter.route() with mocked backend."""

    def _make_router(self, route_return):
        mock_backend = MagicMock(spec=RoutingBackend)
        mock_backend.available = route_return is not None
        mock_backend.route.return_value = route_return

        mock_cls = MagicMock(return_value=mock_backend)
        with patch.object(routing_registry, "get", return_value=mock_cls):
            router = ModelRouter(router_type="mock")
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

    def test_route_returns_none_on_exception(self):
        mock_backend = MagicMock(spec=RoutingBackend)
        mock_backend.available = True
        mock_backend.route.side_effect = RuntimeError("boom")
        mock_cls = MagicMock(return_value=mock_backend)
        with patch.object(routing_registry, "get", return_value=mock_cls):
            router = ModelRouter(router_type="mock")
        result = router.route({
            "model": "gpt-4o",
            "messages": [{"role": "user", "content": "Hi"}],
        })
        assert result is None


# -----------------------------------------------------------------------
# Real ML classification tests (require routellm installed)
# -----------------------------------------------------------------------

class TestRouteLLMBertClassification:
    """Integration tests using real RouteLLM BERT router."""

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
        import os
        os.environ.setdefault("OPENAI_API_KEY", "sk-placeholder")
        return ModelRouter(
            strong="gpt-4o", weak="gpt-4o-mini",
            threshold=0.5, router_type="bert",
        )

    def test_router_is_available(self, bert_router):
        assert bert_router.available

    def test_simple_queries_route_to_weak(self, bert_router):
        simple_queries = ["Hi", "What is 2+2?", "Hello, how are you?", "What color is the sky?"]
        results = []
        for q in simple_queries:
            result = bert_router.route({
                "model": "gpt-4o",
                "messages": [{"role": "user", "content": q}],
            })
            results.append(result)
        weak_count = sum(1 for r in results if r == "gpt-4o-mini")
        assert weak_count >= 3, (
            f"Expected at least 3/4 simple queries to route to weak, "
            f"got {weak_count}/4. Results: {list(zip(simple_queries, results))}"
        )

    def test_complex_query_scores_higher_than_simple(self, bert_router):
        import os
        os.environ.setdefault("OPENAI_API_KEY", "sk-placeholder")
        from routellm.controller import Controller
        controller = Controller(routers=["bert"], strong_model="gpt-4o", weak_model="gpt-4o-mini")
        bert = controller.routers["bert"]
        simple_score = bert.calculate_strong_win_rate("Hi")
        complex_score = bert.calculate_strong_win_rate(
            "Derive the Navier-Stokes equations from first principles"
        )
        assert complex_score > simple_score

    def test_threshold_affects_routing(self, bert_router):
        query = {"model": "gpt-4o", "messages": [{"role": "user", "content": "Tell me a joke"}]}
        high_t_router = ModelRouter(strong="gpt-4o", weak="gpt-4o-mini", threshold=0.9, router_type="bert")
        low_t_router = ModelRouter(strong="gpt-4o", weak="gpt-4o-mini", threshold=0.1, router_type="bert")
        high_result = high_t_router.route(query)
        low_result = low_t_router.route(query)
        assert high_result == "gpt-4o-mini"
        assert low_result == "gpt-4o"
