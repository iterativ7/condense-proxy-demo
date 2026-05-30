"""In-memory vector store using NumPy brute-force cosine similarity.

Zero external dependencies. Suitable for development and small-scale
deployments (< 50K entries).
"""

from __future__ import annotations

import logging
import time
import uuid
from typing import Any, Optional

import numpy as np

from condense.cache.semantic.base import VectorSearchResult, VectorStoreBackend, vector_store_registry

logger = logging.getLogger(__name__)


@vector_store_registry.register("memory")
class InMemoryVectorStore(VectorStoreBackend):
    """Brute-force cosine similarity search in memory.

    Parameters
    ----------
    max_entries : int
        Maximum vectors to store. Oldest entries evicted on overflow.
    ttl_seconds : int
        Time-to-live. Entries older than this are excluded from search.
    """

    def __init__(
        self,
        *,
        max_entries: int = 10000,
        ttl_seconds: int = 3600,
        dimensions: int = 384,
        **kwargs,
    ):
        self._max_entries = max_entries
        self._ttl_seconds = ttl_seconds
        self._dimensions = dimensions

        # Parallel arrays for efficient NumPy operations
        self._ids: list[str] = []
        self._vectors: list[np.ndarray] = []
        self._payloads: list[dict[str, Any]] = []
        self._timestamps: list[float] = []

    @property
    def available(self) -> bool:
        return True

    async def search(
        self,
        vector: np.ndarray,
        top_k: int = 1,
        filter_params: Optional[dict[str, Any]] = None,
    ) -> list[VectorSearchResult]:
        if not self._vectors:
            return []

        now = time.time()

        # Build candidate mask
        mask = np.ones(len(self._vectors), dtype=bool)

        # TTL filter
        for i, ts in enumerate(self._timestamps):
            if self._ttl_seconds > 0 and (now - ts) > self._ttl_seconds:
                mask[i] = False

        # Metadata filter
        if filter_params:
            for i, payload in enumerate(self._payloads):
                if not mask[i]:
                    continue
                for fk, fv in filter_params.items():
                    if payload.get(fk) != fv:
                        mask[i] = False
                        break

        valid_indices = np.where(mask)[0]
        if len(valid_indices) == 0:
            return []

        # Cosine similarity (vectors are pre-normalized)
        matrix = np.stack([self._vectors[i] for i in valid_indices])
        query = vector.reshape(1, -1)
        similarities = (matrix @ query.T).flatten()

        # Top-k
        k = min(top_k, len(similarities))
        if k == 0:
            return []
        if k >= len(similarities):
            # All results fit — just sort
            top_indices = np.argsort(-similarities)[:k]
        else:
            top_indices = np.argpartition(-similarities, k)[:k]
            top_indices = top_indices[np.argsort(-similarities[top_indices])]

        results = []
        for idx in top_indices:
            orig_idx = int(valid_indices[idx])
            results.append(
                VectorSearchResult(
                    id=self._ids[orig_idx],
                    score=float(similarities[idx]),
                    payload=self._payloads[orig_idx],
                )
            )
        return results

    async def upsert(
        self,
        id: str,
        vector: np.ndarray,
        payload: dict[str, Any],
    ) -> None:
        # Check if ID exists — update in place
        for i, existing_id in enumerate(self._ids):
            if existing_id == id:
                self._vectors[i] = vector.astype(np.float32)
                self._payloads[i] = payload
                self._timestamps[i] = time.time()
                return

        # Evict oldest if at capacity
        while len(self._ids) >= self._max_entries:
            self._ids.pop(0)
            self._vectors.pop(0)
            self._payloads.pop(0)
            self._timestamps.pop(0)

        self._ids.append(id)
        self._vectors.append(vector.astype(np.float32))
        self._payloads.append(payload)
        self._timestamps.append(time.time())

    async def delete(self, id: str) -> None:
        for i, existing_id in enumerate(self._ids):
            if existing_id == id:
                self._ids.pop(i)
                self._vectors.pop(i)
                self._payloads.pop(i)
                self._timestamps.pop(i)
                return

    async def clear(self) -> None:
        self._ids.clear()
        self._vectors.clear()
        self._payloads.clear()
        self._timestamps.clear()

    async def size(self) -> int:
        return len(self._ids)
