"""Abstract base classes for semantic cache components.

Two pluggable components:

1. **EmbeddingBackend** — converts text to vector embeddings.
2. **VectorStoreBackend** — stores and searches vector embeddings.

Each has its own registry so backends can be added without
editing any core files.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Optional

import numpy as np

from condense.backends.registry import BackendRegistry


@dataclass
class VectorSearchResult:
    """A single result from a vector similarity search.

    Attributes
    ----------
    id : str
        Unique identifier of the stored entry.
    score : float
        Similarity score (0.0–1.0 for cosine, higher = more similar).
    payload : dict
        Stored metadata associated with this entry.
    """

    id: str
    score: float
    payload: dict[str, Any] = field(default_factory=dict)


class EmbeddingBackend(ABC):
    """Contract for embedding backends."""

    @property
    @abstractmethod
    def available(self) -> bool:
        """Whether the embedding model loaded successfully."""
        ...

    @property
    @abstractmethod
    def dimensions(self) -> int:
        """Dimensionality of the embedding vectors produced."""
        ...

    @abstractmethod
    def embed(self, text: str) -> np.ndarray:
        """Embed a single text string into a vector.

        Returns a 1-D numpy array of shape ``(dimensions,)``.
        """
        ...


class VectorStoreBackend(ABC):
    """Contract for vector store backends."""

    @property
    @abstractmethod
    def available(self) -> bool:
        """Whether the vector store is ready."""
        ...

    @abstractmethod
    async def search(
        self,
        vector: np.ndarray,
        top_k: int = 1,
        filter_params: Optional[dict[str, Any]] = None,
    ) -> list[VectorSearchResult]:
        """Search for the closest vectors.

        Parameters
        ----------
        vector :
            Query vector to compare against stored entries.
        top_k :
            Maximum number of results to return.
        filter_params :
            Optional metadata filters (e.g. namespace, context_hash).

        Returns
        -------
        list[VectorSearchResult]
            Results sorted by descending similarity score.
        """
        ...

    @abstractmethod
    async def upsert(
        self,
        id: str,
        vector: np.ndarray,
        payload: dict[str, Any],
    ) -> None:
        """Insert or update a vector entry with associated payload."""
        ...

    async def delete(self, id: str) -> None:
        """Delete a vector entry by ID. Override if supported."""
        pass

    async def clear(self) -> None:
        """Clear all entries. Override if supported."""
        pass

    async def size(self) -> int:
        """Return number of stored entries. Override if supported."""
        return 0


# Singleton registries
embedding_registry: BackendRegistry[EmbeddingBackend] = BackendRegistry("embedding")
vector_store_registry: BackendRegistry[VectorStoreBackend] = BackendRegistry("vector_store")
