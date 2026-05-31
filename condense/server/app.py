"""FastAPI application factory."""

import logging
import time
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from condense import __version__
from condense.cache.memory import InMemoryCache
from condense.config.loader import load_config
from condense.metrics.postgres_store import PostgresMetricsStore
from condense.server.middleware import TimingMiddleware, RequestLoggingMiddleware
from condense.server.routes import router
from condense.session.store import SessionStore

logger = logging.getLogger(__name__)


class CircuitBreaker:
    """Simple circuit breaker for pipeline failures."""

    def __init__(self, threshold: int = 5, recovery_seconds: int = 30):
        self.threshold = threshold
        self.recovery_seconds = recovery_seconds
        self._failure_count = 0
        self._last_failure_time = 0.0

    @property
    def is_open(self) -> bool:
        if self._failure_count >= self.threshold:
            # Check if recovery period has passed
            if time.time() - self._last_failure_time > self.recovery_seconds:
                self._failure_count = 0
                return False
            return True
        return False

    def record_failure(self):
        self._failure_count += 1
        self._last_failure_time = time.time()

    def record_success(self):
        self._failure_count = 0


def create_app(config_path: str = None) -> FastAPI:
    """Create and configure the FastAPI application."""

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        """Manage application lifecycle — startup and shutdown."""
        config = load_config(config_path)
        enabled_optimizations = {entry.type for entry in config.optimizations if entry.enabled}
        cache_enabled = "cache" in enabled_optimizations
        budget_enabled = "budget" in enabled_optimizations
        cache_config = config.cache_config()
        logger.info(f"Condense v{__version__} starting")
        logger.info(f"Upstream: {config.upstream.url}")
        logger.info(f"Mode: {config.deployment.mode}")

        # Initialize shared state
        app.state.config = config

        # Cache backend (only when cache optimization is enabled)
        app.state.cache_backend = None
        if cache_enabled:
            if config.redis.enabled:
                try:
                    import redis.asyncio as aioredis
                    redis_client = aioredis.from_url(
                        config.redis.url,
                        decode_responses=True,
                    )
                    from condense.cache.redis_backend import RedisCache

                    app.state.cache_backend = RedisCache(
                        redis_client,
                        default_ttl=cache_config.exact.ttl_seconds,
                    )
                    logger.info("Using Redis cache backend")
                except ImportError:
                    logger.warning("Redis not available, falling back to in-memory cache")
                    app.state.cache_backend = InMemoryCache(
                        max_size=cache_config.exact.max_size,
                        default_ttl=cache_config.exact.ttl_seconds,
                    )
            else:
                app.state.cache_backend = InMemoryCache(
                    max_size=cache_config.exact.max_size,
                    default_ttl=cache_config.exact.ttl_seconds,
                )
                logger.info("Using in-memory cache backend")
        else:
            logger.info("Cache optimization disabled; skipping cache backend initialization")

        # Session store (only when budget optimization is enabled)
        app.state.session_store = SessionStore() if budget_enabled else None
        if not budget_enabled:
            logger.info("Budget optimization disabled; skipping session store initialization")

        # HTTP client for upstream
        app.state.http_client = httpx.AsyncClient(
            timeout=httpx.Timeout(config.upstream.timeout_seconds),
            limits=httpx.Limits(max_connections=100, max_keepalive_connections=20),
            follow_redirects=True,
        )

        app.state.metrics_store = None
        if config.metrics.enabled:
            app.state.metrics_store = PostgresMetricsStore(config.metrics.postgres_dsn)
            logger.info("Using Postgres metrics store")
        else:
            logger.info("Metrics disabled")

        # Circuit breaker
        app.state.circuit_breaker = CircuitBreaker(
            threshold=config.failsafe.circuit_breaker.threshold,
            recovery_seconds=config.failsafe.circuit_breaker.recovery_seconds,
        )

        logger.info("Condense proxy ready")
        yield

        # Shutdown
        logger.info("Shutting down Condense proxy")
        await app.state.http_client.aclose()
        cache_backend = getattr(app.state, "cache_backend", None)
        if cache_backend is not None and hasattr(cache_backend, "_redis"):
            try:
                await cache_backend._redis.aclose()
            except Exception:
                pass
        metrics_store = getattr(app.state, "metrics_store", None)
        if metrics_store is not None:
            try:
                metrics_store.close()
            except Exception:
                pass

    app = FastAPI(
        title="Condense",
        description="LLM cost optimization proxy",
        version=__version__,
        lifespan=lifespan,
    )

    # Mount built UI assets when available.
    project_root = Path(__file__).resolve().parents[2]
    ui_dist_assets = project_root / "ui" / "dist" / "assets"
    app.state.ui_dist_index = project_root / "ui" / "dist" / "index.html"
    if ui_dist_assets.exists():
        app.mount("/_ui/assets", StaticFiles(directory=str(ui_dist_assets)), name="ui-assets")

    # Add middleware
    app.add_middleware(TimingMiddleware)
    app.add_middleware(RequestLoggingMiddleware)

    # Include routes
    app.include_router(router)

    return app
