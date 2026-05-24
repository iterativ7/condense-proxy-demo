"""Semantic cache strategy.

Embeds the last user message and searches for semantically similar
cached queries.  Uses the TrueFoundry pattern: only the last user
message is compared semantically — model, system prompt, temperature,
and prior messages are hashed exactly as a "context guard".

This prevents multi-turn confusion, cross-model contamination, and
tool-dependent false positives.
"""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from typing import Any, Optional

import numpy as np

from condense.cache.semantic.base import (
    EmbeddingBackend,
    VectorStoreBackend,
    embedding_registry,
    vector_store_registry,
)
from condense.cache.strategies.base import CacheHit, CacheStrategy, cache_strategy_registry

# Trigger auto-registration of built-in backends.
import condense.cache.semantic.embeddings  # noqa: F401
import condense.cache.semantic.vector_stores  # noqa: F401

logger = logging.getLogger(__name__)


def _extract_last_user_message(messages: list[dict]) -> str:
    """Extract the text of the last user message."""
    for msg in reversed(messages):
        if msg.get("role") == "user":
            content = msg.get("content", "")
            if isinstance(content, list):
                parts = [
                    b.get("text", "")
                    for b in content
                    if isinstance(b, dict) and b.get("type") == "text"
                ]
                return " ".join(parts).strip()
            return str(content).strip()
    return ""


def _compute_context_hash(request: dict, messages: list[dict]) -> str:
    """Compute a deterministic hash of the 'context' — everything
    except the last user message.

    Includes: model, temperature, system prompt, prior messages, tools.
    If any of these differ, the semantic match is rejected even if the
    user's query is identical.
    """
    parts = []

    # Model
    parts.append(f"model:{request.get('model', '')}")

    # Temperature
    parts.append(f"temperature:{request.get('temperature', 0)}")

    # System prompt (first system message)
    for msg in messages:
        if msg.get("role") == "system":
            content = msg.get("content", "")
            if isinstance(content, str):
                parts.append(f"system:{content}")
            break

    # Prior messages (everything except the last user message)
    prior = []
    last_user_idx = None
    for i in range(len(messages) - 1, -1, -1):
        if messages[i].get("role") == "user":
            last_user_idx = i
            break
    if last_user_idx is not None:
        for i, msg in enumerate(messages):
            if i != last_user_idx and msg.get("role") != "system":
                prior.append(f"{msg.get('role', '')}:{msg.get('content', '')}")
    if prior:
        parts.append(f"prior:{json.dumps(prior, sort_keys=True)}")

    # Tools
    tools = request.get("tools")
    if tools:
        parts.append(f"tools:{json.dumps(tools, sort_keys=True)}")

    raw = "|".join(parts)
    return hashlib.sha256(raw.encode()).hexdigest()


@cache_strategy_registry.register("semantic")
class SemanticCacheStrategy(CacheStrategy):
    """Similarity-based cache using embeddings and vector search.

    Parameters (from config dict)
    ----------
    similarity_threshold : float
        Minimum cosine similarity for a cache hit (default: 0.95).
    max_entries : int
        Maximum vectors in the store (default: 10000).
    ttl_seconds : int
        Time-to-live for cached entries (default: 3600).
    max_conversation_turns : int
        Skip semantic cache when conversation has more messages than this
        (default: 3). Set to 0 to disable this guard.
    skip_tool_requests : bool
        Skip semantic cache when tools are present (default: True).
    embedding : dict
        Embedding backend config (backend, model, etc.).
    vector_store : dict
        Vector store backend config (backend, url, etc.).
    """

    def __init__(self, *, config: dict, **kwargs):
        self._config = config
        self._threshold = config.get("similarity_threshold", 0.95)
        self._max_turns = config.get("max_conversation_turns", 3)
        self._skip_tools = config.get("skip_tool_requests", True)
        self._ttl_seconds = config.get("ttl_seconds", 3600)

        # Load embedding backend
        emb_config = config.get("embedding", {})
        emb_name = emb_config.get("backend", "sentence_transformers")
        emb_cls = embedding_registry.get(emb_name)
        if emb_cls is None:
            logger.warning(
                "Unknown embedding backend %r. Available: %s",
                emb_name,
                ", ".join(embedding_registry.available_names()),
            )
            self._embedder: Optional[EmbeddingBackend] = None
        else:
            self._embedder = emb_cls(**{k: v for k, v in emb_config.items() if k != "backend"})

        # Load vector store backend
        vs_config = config.get("vector_store", {})
        vs_name = vs_config.get("backend", "memory")
        vs_cls = vector_store_registry.get(vs_name)
        if vs_cls is None:
            logger.warning(
                "Unknown vector store backend %r. Available: %s",
                vs_name,
                ", ".join(vector_store_registry.available_names()),
            )
            self._store: Optional[VectorStoreBackend] = None
        else:
            # Use embedding dimensions when available; fail loudly if not
            if self._embedder and self._embedder.available and self._embedder.dimensions > 0:
                dims = self._embedder.dimensions
            else:
                dims = vs_config.get("dimensions", 384)
                logger.warning(
                    "Embedding dimensions unknown; using %d (may mismatch model)", dims
                )
            vs_kwargs = {k: v for k, v in vs_config.items() if k != "backend"}
            vs_kwargs["dimensions"] = dims
            vs_kwargs.setdefault("max_entries", config.get("max_entries", 10000))
            vs_kwargs.setdefault("ttl_seconds", self._ttl_seconds)
            self._store = vs_cls(**vs_kwargs)

    @property
    def strategy_name(self) -> str:
        return "semantic"

    @property
    def available(self) -> bool:
        return (
            self._embedder is not None
            and self._embedder.available
            and self._store is not None
            and self._store.available
        )

    def _should_skip(self, request: dict) -> Optional[str]:
        """Check guards. Returns reason string if skipping, None to proceed."""
        messages = request.get("messages", [])

        # Multi-turn guard
        if self._max_turns > 0:
            user_count = sum(1 for m in messages if m.get("role") == "user")
            if user_count > self._max_turns:
                return "conversation_too_long"

        # Tool guard
        if self._skip_tools and request.get("tools"):
            return "tools_present"

        # Non-deterministic guard
        temp = request.get("temperature")
        if temp is not None and temp > 0:
            return "non_deterministic"

        return None

    async def lookup(
        self,
        request: dict[str, Any],
        namespace: str = "",
    ) -> Optional[CacheHit]:
        if not self.available:
            return None

        skip_reason = self._should_skip(request)
        if skip_reason:
            logger.debug("Semantic cache skipped: %s", skip_reason)
            return None

        messages = request.get("messages", [])
        last_user_msg = _extract_last_user_message(messages)
        if not last_user_msg or last_user_msg == ".":
            return None

        # Compute context hash (exact guard)
        context_hash = _compute_context_hash(request, messages)
        filter_params = {"context_hash": context_hash}
        if namespace:
            filter_params["namespace"] = namespace

        # Embed and search
        try:
            vector = self._embedder.embed(last_user_msg)
            results = await self._store.search(
                vector=vector,
                top_k=1,
                filter_params=filter_params,
            )
        except Exception as exc:
            logger.warning("Semantic cache lookup failed: %s", exc)
            return None

        if not results:
            logger.debug("Semantic cache MISS (no candidates)")
            return None

        best = results[0]
        if best.score < self._threshold:
            logger.debug(
                "Semantic cache MISS (score=%.4f < threshold=%.4f)",
                best.score,
                self._threshold,
            )
            return None

        # Cache hit!
        cached_response = best.payload.get("response")
        if not cached_response:
            return None

        estimated_cost = float(best.payload.get("estimated_cost", 0.0))
        tokens_saved = int(best.payload.get("tokens_saved", 0))

        logger.info(
            "Semantic cache HIT (score=%.4f, query=%s...)",
            best.score,
            last_user_msg[:40],
        )

        return CacheHit(
            response=cached_response,
            strategy_name="semantic",
            similarity_score=best.score,
            estimated_cost=estimated_cost,
            tokens_saved=tokens_saved,
            metadata={
                "similarity_score": best.score,
                "matched_query": best.payload.get("query", ""),
                "context_hash_prefix": context_hash[:16],
            },
        )

    async def store(
        self,
        request: dict[str, Any],
        response: dict[str, Any],
        namespace: str = "",
    ) -> None:
        if not self.available:
            return

        skip_reason = self._should_skip(request)
        if skip_reason:
            return

        messages = request.get("messages", [])
        last_user_msg = _extract_last_user_message(messages)
        if not last_user_msg or last_user_msg == ".":
            return

        context_hash = _compute_context_hash(request, messages)

        try:
            vector = self._embedder.embed(last_user_msg)

            # Extract token info for savings tracking
            usage = response.get("usage", {}) if isinstance(response, dict) else {}
            tokens_saved = 0
            if isinstance(usage, dict):
                tokens_saved = int(usage.get("total_tokens", 0))
                if tokens_saved == 0:
                    tokens_saved = int(usage.get("prompt_tokens", 0)) + int(
                        usage.get("completion_tokens", 0)
                    )

            entry_id = str(uuid.uuid4())
            payload = {
                "query": last_user_msg,
                "response": response,
                "context_hash": context_hash,
                "namespace": namespace,
                "estimated_cost": float(response.get("_condense_estimated_cost", 0.0)),
                "tokens_saved": tokens_saved,
                "model": request.get("model", ""),
            }

            await self._store.upsert(id=entry_id, vector=vector, payload=payload)
            logger.debug(
                "Semantic cache STORE (query=%s..., context=%s...)",
                last_user_msg[:40],
                context_hash[:16],
            )
        except Exception as exc:
            logger.warning("Semantic cache store failed: %s", exc)

    async def clear(self) -> None:
        if self._store:
            await self._store.clear()

    async def size(self) -> int:
        if self._store:
            return await self._store.size()
        return 0
