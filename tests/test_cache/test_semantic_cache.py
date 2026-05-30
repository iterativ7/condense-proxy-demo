"""Tests for semantic cache strategy, embedding backends, and vector stores."""

import asyncio
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from condense.cache.strategies.base import CacheHit, cache_strategy_registry
from condense.cache.semantic.base import embedding_registry, vector_store_registry


# -----------------------------------------------------------------------
# Registry tests
# -----------------------------------------------------------------------

class TestRegistries:
    def test_cache_strategy_registry_has_both(self):
        assert "exact" in cache_strategy_registry
        assert "semantic" in cache_strategy_registry

    def test_embedding_registry_has_builtins(self):
        assert "sentence_transformers" in embedding_registry
        assert "openai" in embedding_registry

    def test_vector_store_registry_has_builtins(self):
        assert "memory" in vector_store_registry
        assert "qdrant" in vector_store_registry


# -----------------------------------------------------------------------
# In-memory vector store
# -----------------------------------------------------------------------

class TestInMemoryVectorStore:
    @pytest.fixture
    def store(self):
        from condense.cache.semantic.vector_stores.memory_backend import InMemoryVectorStore
        return InMemoryVectorStore(max_entries=100, ttl_seconds=3600, dimensions=3)

    @pytest.mark.asyncio
    async def test_upsert_and_search(self, store):
        v1 = np.array([1.0, 0.0, 0.0], dtype=np.float32)
        v2 = np.array([0.0, 1.0, 0.0], dtype=np.float32)
        v3 = np.array([0.99, 0.1, 0.0], dtype=np.float32)  # similar to v1

        await store.upsert("a", v1, {"query": "hello"})
        await store.upsert("b", v2, {"query": "goodbye"})

        # Search for something similar to v1
        results = await store.search(v3, top_k=1)
        assert len(results) == 1
        assert results[0].id == "a"
        assert results[0].score > 0.9

    @pytest.mark.asyncio
    async def test_filter_params(self, store):
        v = np.array([1.0, 0.0, 0.0], dtype=np.float32)
        await store.upsert("a", v, {"namespace": "tenant-1", "query": "hi"})
        await store.upsert("b", v, {"namespace": "tenant-2", "query": "hi"})

        results = await store.search(v, top_k=10, filter_params={"namespace": "tenant-1"})
        assert len(results) == 1
        assert results[0].id == "a"

    @pytest.mark.asyncio
    async def test_eviction(self):
        from condense.cache.semantic.vector_stores.memory_backend import InMemoryVectorStore
        store = InMemoryVectorStore(max_entries=2, ttl_seconds=3600, dimensions=2)

        await store.upsert("a", np.array([1, 0], dtype=np.float32), {})
        await store.upsert("b", np.array([0, 1], dtype=np.float32), {})
        await store.upsert("c", np.array([1, 1], dtype=np.float32), {})

        assert await store.size() == 2

    @pytest.mark.asyncio
    async def test_clear(self, store):
        await store.upsert("a", np.array([1, 0, 0], dtype=np.float32), {})
        await store.clear()
        assert await store.size() == 0

    @pytest.mark.asyncio
    async def test_delete(self, store):
        await store.upsert("a", np.array([1, 0, 0], dtype=np.float32), {})
        await store.delete("a")
        assert await store.size() == 0


# -----------------------------------------------------------------------
# Semantic cache strategy (mocked embedding)
# -----------------------------------------------------------------------

class TestSemanticCacheStrategyMocked:
    """Tests with mocked embedding backend to avoid model downloads."""

    def _make_strategy(self, threshold=0.95):
        from condense.cache.strategies.semantic import SemanticCacheStrategy

        mock_embedder = MagicMock()
        mock_embedder.available = True
        mock_embedder.dimensions = 3

        # Return a deterministic embedding based on content
        def fake_embed(text):
            if "hello" in text.lower():
                v = np.array([1.0, 0.0, 0.0], dtype=np.float32)
            elif "goodbye" in text.lower():
                v = np.array([0.0, 1.0, 0.0], dtype=np.float32)
            elif "hi" in text.lower():
                # Similar to "hello"
                v = np.array([0.98, 0.2, 0.0], dtype=np.float32)
            else:
                v = np.array([0.5, 0.5, 0.5], dtype=np.float32)
            norm = np.linalg.norm(v)
            return v / norm if norm > 0 else v

        mock_embedder.embed = fake_embed

        config = {
            "similarity_threshold": threshold,
            "max_entries": 100,
            "ttl_seconds": 3600,
            "max_conversation_turns": 3,
            "skip_tool_requests": True,
            "embedding": {"backend": "sentence_transformers"},
            "vector_store": {"backend": "memory"},
        }

        with patch.object(embedding_registry, "get", return_value=lambda **kw: mock_embedder):
            strategy = SemanticCacheStrategy(config=config)

        return strategy

    @pytest.mark.asyncio
    async def test_store_and_lookup_hit(self):
        strategy = self._make_strategy(threshold=0.9)

        request = {
            "model": "gpt-4o",
            "messages": [{"role": "user", "content": "Hello world"}],
            "temperature": 0,
        }
        response = {
            "choices": [{"message": {"content": "Hi there!"}}],
            "usage": {"total_tokens": 15},
        }

        await strategy.store(request, response)

        # Lookup with semantically similar query
        similar_request = {
            "model": "gpt-4o",
            "messages": [{"role": "user", "content": "Hi there"}],
            "temperature": 0,
        }
        hit = await strategy.lookup(similar_request)
        assert hit is not None
        assert hit.strategy_name == "semantic"
        assert hit.similarity_score > 0.9
        assert hit.response == response

    @pytest.mark.asyncio
    async def test_dissimilar_query_misses(self):
        strategy = self._make_strategy(threshold=0.9)

        request = {
            "model": "gpt-4o",
            "messages": [{"role": "user", "content": "Hello world"}],
            "temperature": 0,
        }
        response = {"choices": [{"message": {"content": "Hi!"}}]}
        await strategy.store(request, response)

        # Completely different query
        diff_request = {
            "model": "gpt-4o",
            "messages": [{"role": "user", "content": "Goodbye world"}],
            "temperature": 0,
        }
        hit = await strategy.lookup(diff_request)
        assert hit is None

    @pytest.mark.asyncio
    async def test_different_model_misses(self):
        """Same query but different model should miss (context hash differs)."""
        strategy = self._make_strategy(threshold=0.9)

        request = {
            "model": "gpt-4o",
            "messages": [{"role": "user", "content": "Hello world"}],
            "temperature": 0,
        }
        await strategy.store(request, {"choices": []})

        diff_model = {
            "model": "gpt-4o-mini",
            "messages": [{"role": "user", "content": "Hello world"}],
            "temperature": 0,
        }
        hit = await strategy.lookup(diff_model)
        assert hit is None

    @pytest.mark.asyncio
    async def test_skips_when_tools_present(self):
        strategy = self._make_strategy()

        request = {
            "model": "gpt-4o",
            "messages": [{"role": "user", "content": "Hello"}],
            "temperature": 0,
            "tools": [{"type": "function", "function": {"name": "search"}}],
        }
        hit = await strategy.lookup(request)
        assert hit is None

    @pytest.mark.asyncio
    async def test_skips_long_conversations(self):
        strategy = self._make_strategy()

        request = {
            "model": "gpt-4o",
            "messages": [
                {"role": "user", "content": "msg1"},
                {"role": "assistant", "content": "reply1"},
                {"role": "user", "content": "msg2"},
                {"role": "assistant", "content": "reply2"},
                {"role": "user", "content": "msg3"},
                {"role": "assistant", "content": "reply3"},
                {"role": "user", "content": "msg4"},
            ],
            "temperature": 0,
        }
        hit = await strategy.lookup(request)
        assert hit is None

    @pytest.mark.asyncio
    async def test_skips_non_deterministic(self):
        strategy = self._make_strategy()

        request = {
            "model": "gpt-4o",
            "messages": [{"role": "user", "content": "Hello"}],
            "temperature": 0.7,
        }
        hit = await strategy.lookup(request)
        assert hit is None

    @pytest.mark.asyncio
    async def test_namespace_isolation(self):
        strategy = self._make_strategy(threshold=0.9)

        request = {
            "model": "gpt-4o",
            "messages": [{"role": "user", "content": "Hello"}],
            "temperature": 0,
        }
        await strategy.store(request, {"choices": []}, namespace="tenant-a")

        # Same query, different namespace
        hit = await strategy.lookup(request, namespace="tenant-b")
        assert hit is None

        # Same namespace
        hit = await strategy.lookup(request, namespace="tenant-a")
        assert hit is not None


# -----------------------------------------------------------------------
# Real sentence-transformers tests (skipped if not installed)
# -----------------------------------------------------------------------

class TestSentenceTransformersReal:
    @pytest.fixture(autouse=True)
    def _check_st(self):
        try:
            from sentence_transformers import SentenceTransformer  # noqa: F401
        except ImportError:
            pytest.skip("sentence-transformers not installed")

    def test_embedding_available(self):
        from condense.cache.semantic.embeddings.sentence_transformers_backend import (
            SentenceTransformersEmbedding,
        )
        emb = SentenceTransformersEmbedding(model="all-MiniLM-L6-v2")
        assert emb.available
        assert emb.dimensions == 384

    def test_embedding_produces_normalized_vector(self):
        from condense.cache.semantic.embeddings.sentence_transformers_backend import (
            SentenceTransformersEmbedding,
        )
        emb = SentenceTransformersEmbedding(model="all-MiniLM-L6-v2")
        vec = emb.embed("Hello world")
        assert vec.shape == (384,)
        assert abs(np.linalg.norm(vec) - 1.0) < 1e-5

    def test_similar_queries_have_higher_similarity_than_unrelated(self):
        """Related queries should score higher than unrelated ones."""
        from condense.cache.semantic.embeddings.sentence_transformers_backend import (
            SentenceTransformersEmbedding,
        )
        emb = SentenceTransformersEmbedding(model="all-MiniLM-L6-v2")
        v1 = emb.embed("What is the return policy?")
        v2 = emb.embed("How do I return an item?")
        v3 = emb.embed("What programming language is Python?")

        sim_related = float(np.dot(v1, v2))
        sim_unrelated = float(np.dot(v1, v3))

        # Related queries should score notably higher than unrelated
        assert sim_related > sim_unrelated, (
            f"Related ({sim_related:.4f}) should be > unrelated ({sim_unrelated:.4f})"
        )
        # The gap should be meaningful (at least 0.2 difference)
        assert sim_related - sim_unrelated > 0.2, (
            f"Gap too small: {sim_related:.4f} - {sim_unrelated:.4f} = {sim_related - sim_unrelated:.4f}"
        )
