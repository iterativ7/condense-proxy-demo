"""ASGI middleware for request timing and metrics."""

import logging
import time

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

logger = logging.getLogger(__name__)


class TimingMiddleware(BaseHTTPMiddleware):
    """Add request timing to response headers."""

    async def dispatch(self, request: Request, call_next):
        start = time.time()
        response = await call_next(request)
        duration_ms = (time.time() - start) * 1000
        response.headers["X-Condense-Latency-Ms"] = f"{duration_ms:.1f}"
        return response


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """Log incoming requests."""

    async def dispatch(self, request: Request, call_next):
        logger.info(f"{request.method} {request.url.path}")
        response = await call_next(request)
        logger.info(f"{request.method} {request.url.path} → {response.status_code}")
        return response
