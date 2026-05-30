"""Sentence-Transformers embedding backend.

Runs fully offline with a local model (~90MB for MiniLM).
No API keys needed.

Install: ``pip install sentence-transformers``
"""

from __future__ import annotations

import logging
from typing import Any

import numpy as np

from condense.cache.semantic.base import EmbeddingBackend, embedding_registry

logger = logging.getLogger(__name__)


@embedding_registry.register("sentence_transformers")
class SentenceTransformersEmbedding(EmbeddingBackend):
    """Local embedding via sentence-transformers.

    Parameters
    ----------
    model : str
        HuggingFace model name (default: ``all-MiniLM-L6-v2``).
    device : str
        Device to run on (default: ``cpu``).
    """

    def __init__(self, *, model: str = "all-MiniLM-L6-v2", device: str = "cpu", **kwargs):
        self._model_name = model
        self._device = device
        self._model = self._load()
        self._dimensions = self._detect_dimensions()

    @property
    def available(self) -> bool:
        return self._model is not None

    @property
    def dimensions(self) -> int:
        return self._dimensions

    def _load(self) -> Any:
        try:
            from sentence_transformers import SentenceTransformer  # type: ignore[import-untyped]

            model = SentenceTransformer(self._model_name, device=self._device)
            logger.info(
                "SentenceTransformers loaded (model=%s, device=%s)",
                self._model_name,
                self._device,
            )
            return model
        except ImportError:
            logger.debug("sentence-transformers not installed")
            return None
        except Exception as exc:
            logger.warning("SentenceTransformers load failed: %s", exc)
            return None

    def _detect_dimensions(self) -> int:
        if self._model is None:
            return 0
        try:
            test = self._model.encode("test", convert_to_numpy=True)
            return int(test.shape[0])
        except Exception:
            return 384  # default for MiniLM

    def embed(self, text: str) -> np.ndarray:
        if self._model is None:
            raise RuntimeError("SentenceTransformers model not loaded")
        vec = self._model.encode(text, convert_to_numpy=True)
        # Normalize for cosine similarity
        norm = np.linalg.norm(vec)
        if norm > 0:
            vec = vec / norm
        return vec.astype(np.float32)
