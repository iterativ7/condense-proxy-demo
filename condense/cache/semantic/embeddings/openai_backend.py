"""OpenAI embedding backend.

Uses the OpenAI Embeddings API. Requires an API key.

Install: ``pip install openai``
"""

from __future__ import annotations

import logging
import os
from typing import Any

import numpy as np

from condense.cache.semantic.base import EmbeddingBackend, embedding_registry

logger = logging.getLogger(__name__)


@embedding_registry.register("openai")
class OpenAIEmbedding(EmbeddingBackend):
    """Remote embedding via OpenAI API.

    Parameters
    ----------
    model : str
        OpenAI model name (default: ``text-embedding-3-small``).
    api_key : str or None
        Explicit API key. Falls back to ``OPENAI_API_KEY`` env var.
    """

    # Known dimensions for common models
    _KNOWN_DIMS = {
        "text-embedding-3-small": 1536,
        "text-embedding-3-large": 3072,
        "text-embedding-ada-002": 1536,
    }

    def __init__(
        self,
        *,
        model: str = "text-embedding-3-small",
        api_key: str | None = None,
        **kwargs,
    ):
        self._model_name = model
        self._api_key = api_key or os.environ.get("OPENAI_API_KEY")
        self._client = self._load()
        self._dimensions = self._KNOWN_DIMS.get(model, 1536)

    @property
    def available(self) -> bool:
        return self._client is not None and self._api_key is not None

    @property
    def dimensions(self) -> int:
        return self._dimensions

    def _load(self) -> Any:
        if not self._api_key:
            logger.debug("No OpenAI API key — OpenAI embedding unavailable")
            return None
        try:
            from openai import OpenAI  # type: ignore[import-untyped]

            client = OpenAI(api_key=self._api_key)
            logger.info("OpenAI embedding client loaded (model=%s)", self._model_name)
            return client
        except ImportError:
            logger.debug("openai package not installed")
            return None
        except Exception as exc:
            logger.warning("OpenAI embedding load failed: %s", exc)
            return None

    def embed(self, text: str) -> np.ndarray:
        if self._client is None:
            raise RuntimeError("OpenAI client not loaded")
        try:
            resp = self._client.embeddings.create(
                input=text,
                model=self._model_name,
                timeout=30,
            )
        except Exception as exc:
            raise RuntimeError(f"OpenAI embedding API call failed: {exc}") from exc
        vec = np.array(resp.data[0].embedding, dtype=np.float32)
        # Normalize
        norm = np.linalg.norm(vec)
        if norm > 0:
            vec = vec / norm
        return vec
