"""FastAPI route handlers."""

import copy
import logging
import time
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import JSONResponse, PlainTextResponse

from condense.cache.key import compute_cache_key
from condense.config.loader import load_config
from condense.config.schema import CondenseConfig
from condense.metrics.prometheus import render_prometheus_metrics
from condense.pipeline import build_pipeline
from condense.pipeline.context import PipelineContext
from condense.session.detector import detect_session
from condense.utils.hashing import short_hash

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/v1/chat/completions")
async def chat_completions(
    request: Request,
    authorization: Optional[str] = Header(None),
):
    """Main proxy endpoint — OpenAI-compatible chat completions."""
    app = request.app

    # Parse request body
    try:
        body = await request.json()
    except Exception as e:
        return JSONResponse(
            {"error": {"message": f"Invalid JSON: {str(e)}", "type": "invalid_request_error"}},
            status_code=400,
        )

    config: CondenseConfig = load_config()

    # Compute cache namespace (tenant isolation)
    api_key = ""
    if authorization:
        api_key = authorization[7:] if authorization.lower().startswith("bearer ") else authorization
    namespace = short_hash(api_key, length=16) if api_key else "default"

    # Detect session
    session_id, session_turn = detect_session(body, namespace)

    # Build pipeline context
    ctx = PipelineContext(
        original_request=copy.deepcopy(body),
        request=body,
        config=config,
        session_id=session_id,
        session_turn=session_turn,
        cache_namespace=namespace,
        original_model=body.get("model"),
        metadata={
            "authorization_header": authorization,
        },
    )

    # Check circuit breaker
    circuit_breaker = getattr(app.state, "circuit_breaker", None)
    if circuit_breaker and circuit_breaker.is_open:
        # Bypass pipeline, forward directly
        logger.warning("Circuit breaker OPEN — bypassing optimization pipeline")
        result = await _direct_forward(body, config, authorization)
        return JSONResponse(result["response"], status_code=result["status_code"])

    # Execute pipeline
    pipeline = build_pipeline(
        config,
        app.state.cache_backend,
        app.state.session_store,
        app.state.http_client,
    )

    start_time = time.time()
    result = await pipeline.execute(ctx)
    latency_ms = (time.time() - start_time) * 1000

    # Record metrics
    metrics = getattr(app.state, "metrics", None)
    if metrics:
        metrics.record_request(
            cache_hit=ctx.cache_hit,
            savings_usd=ctx.total_savings_usd,
            cost_usd=ctx.metadata.get("estimated_cost", 0.0),
            routed=ctx.routed_model is not None,
            rejected=result.action == "reject",
            latency_ms=latency_ms,
        )

    # Handle reject
    if result.action == "reject":
        return JSONResponse(
            {"error": {"message": result.error, "type": "condense_error"}},
            status_code=result.status_code,
        )

    # Post-pipeline: store in cache (background)
    if not ctx.cache_hit and result.response and result.status_code == 200:
        cache_key = ctx.metadata.get("cache_key")
        if cache_key:
            try:
                await app.state.cache_backend.set(
                    cache_key,
                    result.response,
                    ttl=config.optimizations.cache.exact.ttl_seconds,
                )
            except Exception as e:
                logger.error(f"Failed to store cache: {e}")

    # Post-pipeline: update session state
    if ctx.session_id:
        try:
            request_hash = compute_cache_key(ctx.original_request)
            await app.state.session_store.update(
                ctx.session_id,
                cost_usd=ctx.metadata.get("estimated_cost", 0.0),
                request_hash=request_hash,
            )
        except Exception as e:
            logger.error(f"Failed to update session: {e}")

    # Build response with condense headers
    response_data = result.response or {}

    # Clean internal metadata before returning
    clean_response = {k: v for k, v in response_data.items() if not k.startswith("_condense_")}

    response = JSONResponse(clean_response, status_code=result.status_code)

    # Add X-Condense-* headers
    if config.headers.add_savings_headers:
        response.headers["X-Condense-Cache-Hit"] = str(ctx.cache_hit).lower()
        response.headers["X-Condense-Cache-Type"] = ctx.cache_hit_type or "none"
        response.headers["X-Condense-Original-Model"] = ctx.original_model or ""
        response.headers["X-Condense-Routed-Model"] = ctx.routed_model or ctx.original_model or ""
        response.headers["X-Condense-Techniques"] = ",".join(ctx.techniques_applied) if ctx.techniques_applied else "none"
        response.headers["X-Condense-Savings-USD"] = f"{ctx.total_savings_usd:.4f}"
        if ctx.session_id:
            response.headers["X-Condense-Session-ID"] = ctx.session_id
            response.headers["X-Condense-Session-Turn"] = str(ctx.session_turn)

    return response


async def _direct_forward(body: dict, config: CondenseConfig, authorization: Optional[str]) -> dict:
    """Direct forward to upstream (circuit breaker bypass)."""
    url = f"{config.upstream.url.rstrip('/')}/chat/completions"
    headers = {"Content-Type": "application/json"}
    if authorization:
        headers["Authorization"] = authorization

    async with httpx.AsyncClient(timeout=config.upstream.timeout_seconds) as client:
        try:
            resp = await client.post(url, json=body, headers=headers)
            return {"response": resp.json(), "status_code": resp.status_code}
        except Exception as e:
            return {
                "response": {"error": {"message": str(e), "type": "proxy_error"}},
                "status_code": 502,
            }


@router.get("/health")
async def health():
    """Health check endpoint."""
    return {"status": "healthy", "service": "condense"}


@router.get("/health/ready")
async def health_ready(request: Request):
    """Readiness check — verifies config is loaded and pipeline can be built."""
    try:
        config = load_config()
        return {
            "status": "ready",
            "config_loaded": True,
            "upstream": config.upstream.url,
        }
    except Exception as e:
        return JSONResponse(
            {"status": "not_ready", "error": str(e)},
            status_code=503,
        )


@router.get("/metrics")
async def metrics(request: Request):
    """Prometheus-compatible metrics endpoint."""
    tracker = getattr(request.app.state, "metrics", None)
    if tracker is None:
        return PlainTextResponse("# No metrics available\n")
    return PlainTextResponse(
        render_prometheus_metrics(tracker),
        media_type="text/plain; version=0.0.4",
    )
