"""Qdrant vector store backend.

Production-scale vector search with HNSW indexing, metadata filtering,
and persistence.

Install: ``pip install qdrant-client``
"""

from __future__ import annotations

import logging
from typing import Any, Optional

import numpy as np

from condense.cache.semantic.base import VectorSearchResult, VectorStoreBackend, vector_store_registry

logger = logging.getLogger(__name__)


@vector_store_registry.register("qdrant")
class QdrantVectorStore(VectorStoreBackend):
    """Qdrant-backed vector similarity search.

    Parameters
    ----------
    url : str
        Qdrant server URL (default: ``http://localhost:6333``).
    collection : str
        Collection name (default: ``condense_semantic_cache``).
    dimensions : int
        Vector dimensions — must match the embedding model.
    api_key : str or None
        Qdrant API key for cloud deployments.
    """

    def __init__(
        self,
        *,
        url: str = "http://localhost:6333",
        collection: str = "condense_semantic_cache",
        dimensions: int = 384,
        api_key: str | None = None,
        **kwargs,
    ):
        self._url = url
        self._collection = collection
        self._dimensions = dimensions
        self._api_key = api_key
        self._client = self._load()

    @property
    def available(self) -> bool:
        return self._client is not None

    def _load(self) -> Any:
        try:
            from qdrant_client import QdrantClient  # type: ignore[import-untyped]
            from qdrant_client.http.models import (  # type: ignore[import-untyped]
                Distance,
                VectorParams,
            )

            client = QdrantClient(url=self._url, api_key=self._api_key)

            # Ensure collection exists
            collections = [c.name for c in client.get_collections().collections]
            if self._collection not in collections:
                client.create_collection(
                    collection_name=self._collection,
                    vectors_config=VectorParams(
                        size=self._dimensions,
                        distance=Distance.COSINE,
                    ),
                )
                logger.info(
                    "Created Qdrant collection %r (%d dims)",
                    self._collection,
                    self._dimensions,
                )

            logger.info("Qdrant vector store loaded (url=%s)", self._url)
            return client
        except ImportError:
            logger.debug("qdrant-client not installed")
            return None
        except Exception as exc:
            logger.warning("Qdrant load failed: %s", exc)
            return None

    async def search(
        self,
        vector: np.ndarray,
        top_k: int = 1,
        filter_params: Optional[dict[str, Any]] = None,
    ) -> list[VectorSearchResult]:
        if self._client is None:
            return []

        try:
            from qdrant_client.http.models import (  # type: ignore[import-untyped]
                FieldCondition,
                Filter,
                MatchValue,
            )

            query_filter = None
            if filter_params:
                conditions = [
                    FieldCondition(key=k, match=MatchValue(value=v))
                    for k, v in filter_params.items()
                ]
                query_filter = Filter(must=conditions)

            results = self._client.search(
                collection_name=self._collection,
                query_vector=vector.tolist(),
                limit=top_k,
                query_filter=query_filter,
            )

            return [
                VectorSearchResult(
                    id=str(hit.id),
                    score=float(hit.score),
                    payload=hit.payload or {},
                )
                for hit in results
            ]
        except Exception as exc:
            logger.warning("Qdrant search failed: %s", exc)
            return []

    async def upsert(
        self,
        id: str,
        vector: np.ndarray,
        payload: dict[str, Any],
    ) -> None:
        if self._client is None:
            return

        try:
            from qdrant_client.http.models import PointStruct  # type: ignore[import-untyped]

            # Qdrant accepts both string and int IDs; use string UUIDs
            self._client.upsert(
                collection_name=self._collection,
                points=[
                    PointStruct(
                        id=str(id),
                        vector=vector.tolist(),
                        payload=payload,
                    )
                ],
            )
        except Exception as exc:
            logger.warning("Qdrant upsert failed: %s", exc)

    async def delete(self, id: str) -> None:
        if self._client is None:
            return
        try:
            from qdrant_client.http.models import PointIdsList  # type: ignore[import-untyped]

            self._client.delete(
                collection_name=self._collection,
                points_selector=PointIdsList(points=[str(id)]),
            )
        except Exception as exc:
            logger.warning("Qdrant delete failed: %s", exc)

    async def clear(self) -> None:
        if self._client is None:
            return
        try:
            from qdrant_client.http.models import (  # type: ignore[import-untyped]
                Distance,
                VectorParams,
            )

            self._client.delete_collection(self._collection)
            self._client.create_collection(
                collection_name=self._collection,
                vectors_config=VectorParams(
                    size=self._dimensions,
                    distance=Distance.COSINE,
                ),
            )
        except Exception as exc:
            logger.warning("Qdrant clear failed: %s", exc)

    async def size(self) -> int:
        if self._client is None:
            return 0
        try:
            info = self._client.get_collection(self._collection)
            return int(info.points_count)
        except Exception:
            return 0
