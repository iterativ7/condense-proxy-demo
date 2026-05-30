"""Shared test fixtures."""

import pytest
import httpx
from unittest.mock import AsyncMock

from condense.cache.memory import InMemoryCache
from condense.config.schema import CondenseConfig
from condense.session.store import SessionStore
from condense.config.loader import reset_config_cache


@pytest.fixture
def default_config():
    """A default CondenseConfig for testing."""
    return CondenseConfig()


@pytest.fixture
def cache_backend():
    """An in-memory cache backend for testing."""
    return InMemoryCache(max_size=100, default_ttl=60)


@pytest.fixture
def session_store():
    """A session store for testing."""
    return SessionStore()


@pytest.fixture
def mock_http_client():
    """A mock httpx.AsyncClient."""
    client = AsyncMock(spec=httpx.AsyncClient)
    return client


@pytest.fixture(autouse=True)
def reset_config():
    """Reset config cache between tests."""
    reset_config_cache()
    yield
    reset_config_cache()


@pytest.fixture(autouse=True)
def reset_strategy_cache():
    """Reset cache strategy instances between tests to prevent state leaks."""
    from condense.pipeline.steps.cache_step import _strategy_cache
    _strategy_cache.clear()
    yield
    _strategy_cache.clear()


@pytest.fixture
def sample_request():
    """A sample OpenAI-compatible chat request."""
    return {
        "model": "gpt-4o",
        "messages": [
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": "Hello, how are you?"},
        ],
        "temperature": 0,
    }


@pytest.fixture
def sample_response():
    """A sample OpenAI-compatible chat response."""
    return {
        "id": "chatcmpl-test123",
        "object": "chat.completion",
        "created": 1700000000,
        "model": "gpt-4o",
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": "Hello! I'm doing well, thank you!",
                },
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": 20,
            "completion_tokens": 10,
            "total_tokens": 30,
        },
    }
