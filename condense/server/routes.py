"""FastAPI route handlers."""

import copy
import logging
import time
from pathlib import Path
from typing import Optional

import httpx
from fastapi import APIRouter, Header, Request
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    PlainTextResponse,
    RedirectResponse,
    StreamingResponse,
)

from condense.cache.key import compute_cache_key
from condense.config.loader import load_config
from condense.config.schema import CondenseConfig
from condense.metrics.prometheus import render_prometheus_metrics
from condense.metrics.postgres_store import WINDOW_TO_SECONDS
from condense.pipeline import build_pipeline
from condense.pipeline.context import PipelineContext
from condense.pipeline.executor import PipelineExecutor
from condense.pipeline.result import StepResult
from condense.pipeline.steps.forward_step import ForwardStep
from condense.pipeline.stream_forwarder import StreamForwarder
from condense.session.detector import detect_session
from condense.server.streaming import strip_internal_metadata
from condense.server.stream_protocols import infer_stream_protocol, resolve_stream_protocol
from condense.utils.hashing import short_hash

logger = logging.getLogger(__name__)

router = APIRouter()


def _is_stream_request(body: dict, config: CondenseConfig) -> bool:
    return bool(body.get("stream")) and bool(config.deployment.streaming_enabled)


def _stream_sse_headers(*, mode: str, protocol: str) -> dict[str, str]:
    """Headers that identify Condense SSE streaming paths to clients."""
    return {
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",
        "X-Condense-Stream-Transport": "sse",
        "X-Condense-Stream-Mode": mode,
        "X-Condense-Stream-Protocol": protocol,
    }


def _apply_condense_headers(response, ctx: PipelineContext, config: CondenseConfig) -> None:
    if not config.headers.add_savings_headers:
        return
    response.headers["X-Condense-Cache-Hit"] = str(ctx.cache_hit).lower()
    response.headers["X-Condense-Cache-Type"] = ctx.cache_hit_type or "none"
    response.headers["X-Condense-Original-Model"] = ctx.original_model or ""
    response.headers["X-Condense-Routed-Model"] = ctx.routed_model or ctx.original_model or ""
    response.headers["X-Condense-Techniques"] = ",".join(ctx.techniques_applied) if ctx.techniques_applied else "none"
    response.headers["X-Condense-Savings-USD"] = f"{ctx.total_savings_usd:.4f}"
    if ctx.session_id:
        response.headers["X-Condense-Session-ID"] = ctx.session_id
        response.headers["X-Condense-Session-Turn"] = str(ctx.session_turn)


async def _store_cache_and_session(ctx: PipelineContext, result: StepResult) -> None:
    cache_step = ctx.metadata.get("_cache_step")
    if cache_step is not None and not ctx.cache_hit and result.response and result.status_code == 200:
        try:
            await cache_step.store_response(
                ctx.original_request,
                result.response,
                ctx.cache_namespace,
            )
        except Exception as e:
            logger.error(f"Failed to store cache: {e}")

    session_store = ctx.metadata.get("_session_store")
    if session_store is not None and ctx.session_id:
        try:
            request_hash = compute_cache_key(ctx.original_request)
            await session_store.update(
                ctx.session_id,
                cost_usd=ctx.metadata.get("estimated_cost", 0.0),
                request_hash=request_hash,
            )
        except Exception as e:
            logger.error(f"Failed to update session: {e}")


def _record_metrics(
    app,
    ctx: PipelineContext,
    result: StepResult,
    latency_ms: float,
    *,
    ttfb_ms: float = 0.0,
    stream_duration_ms: float = 0.0,
) -> None:
    metrics_store = getattr(app.state, "metrics_store", None)
    if metrics_store is None:
        return
    request_metrics = ctx.build_request_metrics(
        result,
        latency_ms,
        ttfb_ms=ttfb_ms,
        stream_duration_ms=stream_duration_ms,
    )
    try:
        metrics_store.record_request(request_metrics.as_record_kwargs())
    except Exception as exc:
        logger.error("Failed to persist request metrics: %s", exc)


def _enabled_optimization_ids(config: CondenseConfig) -> list[str]:
    """Return enabled optimization IDs from active config."""
    return [entry.id for entry in config.optimizations if entry.enabled]


def _ui_index_file(request: Request) -> Optional[Path]:
    ui_index = getattr(request.app.state, "ui_dist_index", None)
    if isinstance(ui_index, Path) and ui_index.exists():
        return ui_index
    return None


def _build_dashboard_html() -> str:
    """Build a lightweight dashboard page that reads /metrics/summary."""
    return """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Condense Savings Dashboard</title>
  <style>
    :root {
      color-scheme: light dark;
      --bg: #0b1020;
      --card: #151c33;
      --text: #f4f6ff;
      --muted: #a3acc4;
      --accent: #4f8cff;
      --ok: #27c080;
    }
    body {
      margin: 0;
      padding: 24px;
      font-family: Inter, system-ui, -apple-system, Segoe UI, Roboto, sans-serif;
      background: linear-gradient(180deg, #090e1c, #0f172a);
      color: var(--text);
    }
    .container {
      max-width: 1100px;
      margin: 0 auto;
    }
    h1 {
      margin: 0 0 6px;
      font-size: 30px;
      font-weight: 700;
    }
    .subtitle {
      color: var(--muted);
      margin-bottom: 20px;
    }
    .grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
      gap: 12px;
    }
    .card {
      background: var(--card);
      border-radius: 14px;
      padding: 16px;
      box-shadow: 0 8px 25px rgba(0, 0, 0, 0.22);
      border: 1px solid rgba(255, 255, 255, 0.08);
    }
    .label {
      font-size: 13px;
      color: var(--muted);
      margin-bottom: 8px;
    }
    .value {
      font-size: 32px;
      font-weight: 700;
      letter-spacing: 0.4px;
    }
    .value.large {
      font-size: 36px;
    }
    .value.accent {
      color: var(--accent);
    }
    .value.ok {
      color: var(--ok);
    }
    .footnote {
      margin-top: 18px;
      font-size: 12px;
      color: var(--muted);
    }
    .status {
      margin-top: 12px;
      font-size: 12px;
      color: var(--muted);
    }
  </style>
</head>
<body>
  <main class="container">
    <h1>Condense Savings Dashboard</h1>
    <div class="subtitle">Live savings and usage overview (auto-refresh every 5 seconds)</div>
    <section class="grid">
      <article class="card">
        <div class="label">Total USD Saved</div>
        <div id="totalSavingsUsd" class="value large ok">$0.0000</div>
      </article>
      <article class="card">
        <div class="label">Total Tokens Saved (Estimate)</div>
        <div id="totalTokensSaved" class="value large accent">0</div>
      </article>
      <article class="card">
        <div class="label">Total Requests</div>
        <div id="totalRequests" class="value">0</div>
      </article>
      <article class="card">
        <div class="label">Cache Hit Rate</div>
        <div id="cacheHitRate" class="value">0%</div>
      </article>
      <article class="card">
        <div class="label">Total Prompt Tokens</div>
        <div id="totalPromptTokens" class="value">0</div>
      </article>
      <article class="card">
        <div class="label">Total Completion Tokens</div>
        <div id="totalCompletionTokens" class="value">0</div>
      </article>
      <article class="card">
        <div class="label">Total Tokens Processed</div>
        <div id="totalTokens" class="value">0</div>
      </article>
      <article class="card">
        <div class="label">Uptime (seconds)</div>
        <div id="uptimeSeconds" class="value">0</div>
      </article>
    </section>
    <div id="status" class="status">Loading...</div>
    <div class="footnote">Data source: <code>/metrics/summary</code></div>
  </main>
  <script>
    function formatNumber(value) {
      return new Intl.NumberFormat("en-US").format(value);
    }

    function setText(id, text) {
      const node = document.getElementById(id);
      if (node) node.textContent = text;
    }

    async function refreshSummary() {
      const status = document.getElementById("status");
      try {
        const res = await fetch("/metrics/summary", { cache: "no-store" });
        if (!res.ok) {
          throw new Error("HTTP " + res.status);
        }
        const data = await res.json();
        const totals = data.totals || {};
        const rates = data.rates || {};

        setText("totalSavingsUsd", "$" + (Number(totals.total_savings_usd || 0)).toFixed(4));
        setText("totalTokensSaved", formatNumber(Number(totals.total_tokens_saved_estimate || 0)));
        setText("totalRequests", formatNumber(Number(totals.total_requests || 0)));
        setText("cacheHitRate", (Number(rates.cache_hit_rate || 0)).toFixed(2) + "%");
        setText("totalPromptTokens", formatNumber(Number(totals.total_prompt_tokens || 0)));
        setText("totalCompletionTokens", formatNumber(Number(totals.total_completion_tokens || 0)));
        setText("totalTokens", formatNumber(Number(totals.total_tokens || 0)));
        setText("uptimeSeconds", formatNumber(Number(data.uptime_seconds || 0)));
        status.textContent = "Last updated: " + new Date().toLocaleTimeString();
      } catch (err) {
        status.textContent = "Failed to refresh metrics: " + err.message;
      }
    }

    refreshSummary();
    setInterval(refreshSummary, 5000);
  </script>
</body>
</html>
"""


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

    config: CondenseConfig = getattr(app.state, "config", load_config())
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
            "_session_store": getattr(app.state, "session_store", None),
        },
    )
    stream_request = _is_stream_request(body, config)
    requested_stream_protocol = infer_stream_protocol(
        requested=body.get("stream_protocol") or config.upstream.stream_protocol,
    )
    resolved_live_protocol = resolve_stream_protocol(requested_stream_protocol).name
    ctx.request.setdefault("stream_protocol", resolved_live_protocol)

    # Check circuit breaker
    circuit_breaker = getattr(app.state, "circuit_breaker", None)
    if circuit_breaker and circuit_breaker.is_open:
        # Bypass pipeline, forward directly
        logger.warning("Circuit breaker OPEN — bypassing optimization pipeline")
        if stream_request:
            return await _direct_forward_stream(body, config, authorization)
        result = await _direct_forward(body, config, authorization)
        return JSONResponse(result["response"], status_code=result["status_code"])

    # Execute pipeline
    pipeline = build_pipeline(
        config,
        getattr(app.state, "cache_backend", None),
        getattr(app.state, "session_store", None),
        app.state.http_client,
    )

    request_started = time.perf_counter()

    if stream_request:
        forward_step = next((step for step in pipeline.steps if isinstance(step, ForwardStep)), None)
        pre_forward_steps = [step for step in pipeline.steps if not isinstance(step, ForwardStep)]
        pre_forward_pipeline = PipelineExecutor(pre_forward_steps, allow_terminal_next=True)

        pre_result = await pre_forward_pipeline.execute(ctx)
        pre_latency_ms = (time.perf_counter() - request_started) * 1000

        if pre_result.action == "reject":
            _record_metrics(app, ctx, pre_result, pre_latency_ms)
            return JSONResponse(
                {"error": {"message": pre_result.error, "type": "condense_error"}},
                status_code=pre_result.status_code,
            )

        if pre_result.action == "short_circuit":
            _record_metrics(
                app,
                ctx,
                pre_result,
                pre_latency_ms,
                ttfb_ms=pre_latency_ms,
                stream_duration_ms=pre_latency_ms,
            )
            cached_payload = pre_result.response or {}
            cache_replay_protocol = infer_stream_protocol(
                requested=requested_stream_protocol,
                cached_response=cached_payload,
            )
            replay_adapter = resolve_stream_protocol(cache_replay_protocol)
            stream_response = StreamingResponse(
                replay_adapter.replay_cached_response(cached_payload),
                media_type="text/event-stream",
                headers=_stream_sse_headers(mode="cache_replay", protocol=replay_adapter.name),
            )
            _apply_condense_headers(stream_response, ctx, config)
            return stream_response

        if forward_step is None:
            return JSONResponse(
                {"error": {"message": "Forward step missing for stream request", "type": "condense_error"}},
                status_code=500,
            )

        async def _on_stream_complete(
            final_response: dict,
            status_code: int,
            stream_elapsed_ms: float,
            forward_ttfb_ms: float,
        ) -> None:
            result = StepResult(
                action="short_circuit",
                response=final_response,
                status_code=status_code,
                technique="forward",
            )
            if status_code == 200:
                await _store_cache_and_session(ctx, result)
            total_latency_ms = (time.perf_counter() - request_started) * 1000
            _record_metrics(
                app,
                ctx,
                result,
                total_latency_ms,
                ttfb_ms=pre_latency_ms + forward_ttfb_ms if forward_ttfb_ms > 0 else pre_latency_ms,
                stream_duration_ms=stream_elapsed_ms,
            )

        stream_forwarder = StreamForwarder(forward_step)
        stream_response = StreamingResponse(
            stream_forwarder.sse_iterator(ctx, on_complete=_on_stream_complete),
            media_type="text/event-stream",
            headers=_stream_sse_headers(mode="live_upstream", protocol=resolved_live_protocol),
        )
        _apply_condense_headers(stream_response, ctx, config)
        return stream_response

    start_time = time.time()
    result = await pipeline.execute(ctx)
    latency_ms = (time.time() - start_time) * 1000

    # Record metrics
    _record_metrics(app, ctx, result, latency_ms)

    # Handle reject
    if result.action == "reject":
        return JSONResponse(
            {"error": {"message": result.error, "type": "condense_error"}},
            status_code=result.status_code,
        )

    await _store_cache_and_session(ctx, result)

    # Build response with condense headers
    response_data = result.response or {}

    # Clean internal metadata before returning
    clean_response = strip_internal_metadata(response_data)

    response = JSONResponse(clean_response, status_code=result.status_code)

    # Add X-Condense-* headers
    _apply_condense_headers(response, ctx, config)

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


async def _direct_forward_stream(
    body: dict,
    config: CondenseConfig,
    authorization: Optional[str],
) -> StreamingResponse:
    """Direct streaming forward to upstream (circuit breaker bypass)."""
    url = f"{config.upstream.url.rstrip('/')}/chat/completions"
    headers = {"Content-Type": "application/json"}
    if authorization:
        headers["Authorization"] = authorization

    client = httpx.AsyncClient(timeout=config.upstream.timeout_seconds)
    request = client.build_request("POST", url, json=body, headers=headers)
    upstream_response = await client.send(request, stream=True)

    passthrough_headers = _stream_sse_headers(mode="bypass_passthrough", protocol="raw_passthrough")

    async def _passthrough():
        try:
            async for chunk in upstream_response.aiter_raw():
                if chunk:
                    yield chunk
        finally:
            await upstream_response.aclose()
            await client.aclose()

    return StreamingResponse(
        _passthrough(),
        status_code=upstream_response.status_code,
        media_type=upstream_response.headers.get("content-type", "text/event-stream"),
        headers=passthrough_headers,
    )


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
    metrics_store = getattr(request.app.state, "metrics_store", None)
    if metrics_store is None:
        return PlainTextResponse("# No metrics available\n")
    summary = metrics_store.summary()
    return PlainTextResponse(
        render_prometheus_metrics(summary),
        media_type="text/plain; version=0.0.4",
    )


@router.get("/metrics/summary")
async def metrics_summary(request: Request):
    """Structured metrics summary endpoint for dashboards and UIs."""
    metrics_store = getattr(request.app.state, "metrics_store", None)
    if metrics_store is None:
        return {
            "totals": {
                "total_requests": 0,
                "cache_hits": 0,
                "cache_misses": 0,
                "total_savings_usd": 0.0,
                "total_cost_usd": 0.0,
                "total_prompt_tokens": 0,
                "total_completion_tokens": 0,
                "total_tokens": 0,
                "total_tokens_saved_estimate": 0,
                "requests_routed": 0,
                "requests_rejected": 0,
                "pipeline_errors": 0,
            },
            "rates": {
                "cache_hit_rate": 0.0,
                "avg_savings_per_request_usd": 0.0,
                "avg_ttfb_ms": 0.0,
                "avg_stream_duration_ms": 0.0,
            },
            "uptime_seconds": 0.0,
        }
    return metrics_store.summary()


@router.get("/metrics/summary/v2")
async def metrics_summary_v2(request: Request, window: str = "7d"):
    """UI-focused summary with per-optimization breakdown and dynamic tabs."""
    config: CondenseConfig = getattr(request.app.state, "config", load_config())
    enabled_tabs = _enabled_optimization_ids(config)
    selected_window = window if window in WINDOW_TO_SECONDS else "7d"
    metrics_store = getattr(request.app.state, "metrics_store", None)
    if metrics_store is None:
        return {
            "overall": {
                "total_savings_usd": 0.0,
                "total_tokens_saved_estimate": 0,
                "total_requests": 0,
                "uptime_seconds": 0.0,
            },
            "window": selected_window,
            "enabled_tabs": enabled_tabs,
            "optimizations": [],
            "series": [],
            "optimization_series": [],
        }
    return metrics_store.summary_v2(enabled_tabs=enabled_tabs, window=selected_window)


@router.get("/dashboard")
async def dashboard(request: Request):
    """Backward-compatible dashboard route."""
    if _ui_index_file(request):
        return RedirectResponse(url="/_ui")
    return HTMLResponse(_build_dashboard_html())


@router.get("/_ui")
async def ui_root(request: Request):
    """Serve modular UI entrypoint if built assets are available."""
    ui_index = _ui_index_file(request)
    if ui_index:
        return FileResponse(ui_index)
    return HTMLResponse(
        "<h1>UI build not found</h1><p>Run <code>make ui-build</code> to build the modular UI.</p>",
        status_code=503,
    )


@router.get("/_ui/{path:path}")
async def ui_spa_path(path: str, request: Request):
    """SPA fallback for client-side UI routes."""
    ui_index = _ui_index_file(request)
    if ui_index:
        return FileResponse(ui_index)
    return HTMLResponse(
        "<h1>UI build not found</h1><p>Run <code>make ui-build</code> to build the modular UI.</p>",
        status_code=503,
    )
