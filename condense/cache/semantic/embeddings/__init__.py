"""Embedding backend implementations.

Importing this package auto-registers all built-in backends.
"""

from condense.cache.semantic.embeddings import sentence_transformers_backend  # noqa: F401
from condense.cache.semantic.embeddings import openai_backend  # noqa: F401
