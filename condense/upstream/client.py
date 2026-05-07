"""Async HTTP client pool for upstream LLM providers."""

import logging
import os
from typing import Optional

import httpx

from condense.config.schema import UpstreamConfig

logger = logging.getLogger(__name__)


class UpstreamClient:
    """Manages an async httpx client pool for upstream requests."""

    def __init__(self, config: UpstreamConfig):
        self.config = config
        self._client: Optional[httpx.AsyncClient] = None

    async def get_client(self) -> httpx.AsyncClient:
        """Get or create the async HTTP client."""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self.config.url,
                timeout=httpx.Timeout(self.config.timeout_seconds),
                limits=httpx.Limits(
                    max_connections=100,
                    max_keepalive_connections=20,
                ),
                follow_redirects=True,
            )
        return self._client

    def get_api_key(self) -> Optional[str]:
        """Get API key from configured environment variable."""
        if self.config.api_key_env:
            return os.environ.get(self.config.api_key_env)
        return None

    async def close(self) -> None:
        """Close the HTTP client."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None
