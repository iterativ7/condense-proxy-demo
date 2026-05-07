"""FastAPI dependency injection functions."""

import logging
from typing import Optional

from fastapi import Depends, Header, Request

from condense.config.loader import load_config
from condense.config.schema import CondenseConfig
from condense.session.detector import detect_session
from condense.utils.hashing import short_hash

logger = logging.getLogger(__name__)


async def get_config() -> CondenseConfig:
    """Load (and cache) the Condense configuration."""
    return load_config()


async def get_cache_namespace(
    authorization: Optional[str] = Header(None),
) -> str:
    """Compute cache namespace from API key hash for tenant isolation.

    Returns SHA-256(api_key)[:16] to ensure different API keys
    use different cache partitions.
    """
    if not authorization:
        return "default"

    # Strip "Bearer " prefix
    api_key = authorization
    if api_key.lower().startswith("bearer "):
        api_key = api_key[7:]

    return short_hash(api_key, length=16)


async def get_session_info(
    request: Request,
    namespace: str = Depends(get_cache_namespace),
):
    """Detect session from request body.

    Returns (session_id, turn_number).
    """
    try:
        body = await request.json()
    except Exception:
        return None, 0

    session_id, turn = detect_session(body, namespace)
    return session_id, turn
