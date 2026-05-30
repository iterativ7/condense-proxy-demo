"""FastAPI route handlers."""

import copy
import json
import logging
import time
from pathlib import Path
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse, StreamingResponse

from condense.cache.key import compute_cache_key
from condense.config.loader import load_config
from condense.config.schema import CondenseConfig
from condense.metrics.prometheus import render_prometheus_metrics
from condense.pipeline import build_pipeline
from condense.pipeline.context import PipelineContext
from condense.pipeline.executor import PipelineExecutor
from condense.session.detector import detect_session
from condense.utils.hashing import short_hash

logger = logging.getLogger(__name__)

router = APIRouter()


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

    # Track if client requested streaming — we always process non-streaming
    # through the pipeline, then convert to SSE if needed.
    client_wants_stream = body.pop("stream", False)
    body["stream"] = False

    config: CondenseConfig = getattr(app.state, "config", load_config())
    cache_config = config.cache_config()

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
        getattr(app.state, "cache_backend", None),
        getattr(app.state, "session_store", None),
        app.state.http_client,
    )

    start_time = time.time()
    result = await pipeline.execute(ctx)
    latency_ms = (time.time() - start_time) * 1000

    # Record metrics
    metrics = getattr(app.state, "metrics", None)
    if metrics:
        request_metrics = ctx.build_request_metrics(result, latency_ms)
        metrics.record_request(**request_metrics.as_record_kwargs())

    # Handle reject
    if result.action == "reject":
        return JSONResponse(
            {"error": {"message": result.error, "type": "condense_error"}},
            status_code=result.status_code,
        )

    # Post-pipeline: store in cache via strategy-based CacheStep
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

    # Post-pipeline: update session state
    session_store = getattr(app.state, "session_store", None)
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

    # Build response with condense headers
    response_data = result.response or {}

    # Clean internal metadata before returning
    clean_response = {k: v for k, v in response_data.items() if not k.startswith("_condense_")}

    condense_headers = {}
    if config.headers.add_savings_headers:
        condense_headers["X-Condense-Cache-Hit"] = str(ctx.cache_hit).lower()
        condense_headers["X-Condense-Cache-Type"] = ctx.cache_hit_type or "none"
        condense_headers["X-Condense-Original-Model"] = ctx.original_model or ""
        condense_headers["X-Condense-Routed-Model"] = ctx.routed_model or ctx.original_model or ""
        condense_headers["X-Condense-Techniques"] = ",".join(ctx.techniques_applied) if ctx.techniques_applied else "none"
        condense_headers["X-Condense-Savings-USD"] = f"{ctx.total_savings_usd:.4f}"
        if ctx.session_id:
            condense_headers["X-Condense-Session-ID"] = ctx.session_id
            condense_headers["X-Condense-Session-Turn"] = str(ctx.session_turn)

    # If client requested streaming, convert the non-streaming response to SSE
    if client_wants_stream and result.status_code == 200:
        return StreamingResponse(
            _to_sse_stream(clean_response),
            status_code=200,
            media_type="text/event-stream",
            headers=condense_headers,
        )

    response = JSONResponse(clean_response, status_code=result.status_code)
    for k, v in condense_headers.items():
        response.headers[k] = v
    return response


async def _to_sse_stream(response: dict):
    """Convert a non-streaming chat completion response to SSE chunks.

    Emulates the OpenAI streaming format so clients like Cursor that
    require ``stream: true`` receive a valid SSE response.
    """
    model = response.get("model", "")
    resp_id = response.get("id", "chatcmpl-condense")
    created = response.get("created", 0)

    for choice in response.get("choices", []):
        msg = choice.get("message", {})
        chunk = {
            "id": resp_id,
            "object": "chat.completion.chunk",
            "created": created,
            "model": model,
            "choices": [
                {
                    "index": choice.get("index", 0),
                    "delta": {"role": msg.get("role", "assistant"), "content": msg.get("content", "")},
                    "finish_reason": None,
                }
            ],
        }
        yield f"data: {json.dumps(chunk)}\n\n"

        # Send finish chunk
        finish_chunk = {
            "id": resp_id,
            "object": "chat.completion.chunk",
            "created": created,
            "model": model,
            "choices": [
                {
                    "index": choice.get("index", 0),
                    "delta": {},
                    "finish_reason": choice.get("finish_reason", "stop"),
                }
            ],
        }
        yield f"data: {json.dumps(finish_chunk)}\n\n"

    yield "data: [DONE]\n\n"


async def _to_responses_sse_stream(response_obj: dict):
    """Convert a Responses API result to SSE stream format."""
    import json as _json
    yield f"event: response.created\ndata: {_json.dumps({'type': 'response.created', 'response': response_obj})}\n\n"
    yield f"event: response.in_progress\ndata: {_json.dumps({'type': 'response.in_progress', 'response': response_obj})}\n\n"

    for item in response_obj.get("output", []):
        if item.get("type") == "message":
            for content in item.get("content", []):
                if content.get("type") == "output_text":
                    yield f"event: response.output_text.delta\ndata: {_json.dumps({'type': 'response.output_text.delta', 'delta': content.get('text', '')})}\n\n"
                    yield f"event: response.output_text.done\ndata: {_json.dumps({'type': 'response.output_text.done', 'text': content.get('text', '')})}\n\n"

    yield f"event: response.completed\ndata: {_json.dumps({'type': 'response.completed', 'response': response_obj})}\n\n"


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


@router.post("/v1/responses")
async def responses_api(request: Request, authorization: Optional[str] = Header(None)):
    """OpenAI Responses API endpoint — translates to chat completions pipeline.

    Accepts the Responses API format (``input`` + ``instructions``) and
    converts it to chat completions format internally, runs through the
    full Condense pipeline, then converts the response back.
    """
    try:
        raw_body = await request.body()
        # Handle compressed bodies (gzip, zstd, deflate)
        content_encoding = request.headers.get("content-encoding", "").lower()
        if content_encoding == "gzip":
            import gzip
            raw_body = gzip.decompress(raw_body)
        elif content_encoding == "deflate":
            import zlib
            raw_body = zlib.decompress(raw_body)
        elif content_encoding in ("zstd", "zstandard"):
            try:
                import zstandard
                dctx = zstandard.ZstdDecompressor()
                # Use streaming decompression for frames without content size
                import io
                reader = dctx.stream_reader(io.BytesIO(raw_body))
                raw_body = reader.read()
            except ImportError:
                pass  # Try raw parse
        body = json.loads(raw_body)
    except Exception as e:
        return JSONResponse(
            {"error": {"message": f"Invalid JSON: {str(e)}", "type": "invalid_request_error"}},
            status_code=400,
        )

    # Translate Responses API -> Chat Completions format
    messages = []
    instructions = body.get("instructions")
    if instructions:
        messages.append({"role": "system", "content": instructions})

    raw_input = body.get("input", "")
    if isinstance(raw_input, str):
        messages.append({"role": "user", "content": raw_input})
    elif isinstance(raw_input, list):
        for item in raw_input:
            if isinstance(item, dict):
                role = item.get("role", "user")
                content = item.get("content", "")
                # Handle structured content (input_text blocks)
                if isinstance(content, list):
                    text_parts = [
                        p.get("text", "") for p in content
                        if isinstance(p, dict) and p.get("type") in ("input_text", "text")
                    ]
                    content = " ".join(text_parts)
                messages.append({"role": role, "content": content})
            elif isinstance(item, str):
                messages.append({"role": "user", "content": item})

    # Build a chat completions request body
    cc_body = {
        "model": body.get("model", ""),
        "messages": messages,
        "stream": False,
    }
    if body.get("max_output_tokens"):
        cc_body["max_tokens"] = body["max_output_tokens"]
    if body.get("temperature") is not None:
        cc_body["temperature"] = body["temperature"]

    # Inject the translated body and reuse the chat completions pipeline
    # We create a mutable scope to override request.json()
    original_json = request.json

    async def patched_json():
        return cc_body

    request.json = patched_json  # type: ignore[method-assign]
    request._json = cc_body  # type: ignore[attr-defined]

    # Check streaming preference
    client_wants_stream = body.get("stream", False)

    app = request.app
    config: CondenseConfig = getattr(app.state, "config", load_config())
    tracker = getattr(app.state, "metrics_tracker", None)

    executor = build_pipeline(
        config,
        getattr(app.state, "cache_backend", None),
        getattr(app.state, "session_store", None),
        app.state.http_client,
    )

    ctx = PipelineContext(request=cc_body, config=config, original_request=cc_body.copy())

    try:
        result = await executor.execute(ctx)
    except Exception as e:
        return JSONResponse(
            {"error": {"message": str(e), "type": "pipeline_error"}},
            status_code=502,
        )

    if result.status_code != 200:
        return JSONResponse(
            result.response or {"error": {"message": "Pipeline error"}},
            status_code=result.status_code,
        )

    # Track metrics
    if tracker and result.status_code == 200:
        metrics = ctx.build_request_metrics()
        tracker.record(metrics)

    # Convert chat completions response -> Responses API format
    cc_response = result.response or {}
    resp_id = cc_response.get("id", "resp-condense")
    model = cc_response.get("model", body.get("model", ""))
    created = cc_response.get("created", 0)

    output = []
    for choice in cc_response.get("choices", []):
        msg = choice.get("message", {})
        output.append({
            "id": f"msg_{resp_id}",
            "type": "message",
            "status": "completed",
            "role": msg.get("role", "assistant"),
            "content": [
                {
                    "type": "output_text",
                    "text": msg.get("content", ""),
                    "annotations": [],
                }
            ],
        })

    raw_usage = cc_response.get("usage", {})
    usage = {
        "input_tokens": raw_usage.get("prompt_tokens", 0),
        "output_tokens": raw_usage.get("completion_tokens", 0),
        "total_tokens": raw_usage.get("total_tokens", 0),
        "output_tokens_details": {
            "reasoning_tokens": 0,
        },
    }
    responses_result = {
        "id": resp_id,
        "object": "response",
        "created_at": created,
        "status": "completed",
        "error": None,
        "incomplete_details": None,
        "instructions": None,
        "max_output_tokens": None,
        "model": model,
        "output": output,
        "parallel_tool_calls": True,
        "previous_response_id": None,
        "reasoning": {"effort": None, "summary": None},
        "store": True,
        "temperature": 1.0,
        "text": {"format": {"type": "text"}},
        "tool_choice": "auto",
        "tools": [],
        "top_p": 1.0,
        "truncation": "disabled",
        "usage": usage,
        "user": None,
        "metadata": {},
    }

    condense_headers = {}
    if config.headers.add_savings_headers:
        condense_headers["X-Condense-Cache-Hit"] = str(ctx.cache_hit).lower()
        condense_headers["X-Condense-Cache-Type"] = ctx.cache_hit_type or "none"
        condense_headers["X-Condense-Techniques"] = ",".join(ctx.techniques_applied) if ctx.techniques_applied else "none"

    # Codex CLI always expects SSE streaming for responses API
    return StreamingResponse(
        _to_responses_sse_stream(responses_result),
        status_code=200,
        media_type="text/event-stream",
        headers=condense_headers,
    )


@router.get("/v1/models")
async def list_models(request: Request, authorization: Optional[str] = Header(None)):
    """Proxy /v1/models to upstream so tools can discover available models."""
    config: CondenseConfig = getattr(request.app.state, "config", load_config())
    headers = {}
    if authorization:
        headers["Authorization"] = authorization
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(
                f"{config.upstream.url}/models",
                headers=headers,
            )
            return JSONResponse(resp.json(), status_code=resp.status_code)
    except Exception as e:
        logger.warning("Failed to fetch models from upstream: %s", e)
        return JSONResponse(
            {"object": "list", "data": []},
            status_code=200,
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
    tracker = getattr(request.app.state, "metrics", None)
    if tracker is None:
        return PlainTextResponse("# No metrics available\n")
    return PlainTextResponse(
        render_prometheus_metrics(tracker),
        media_type="text/plain; version=0.0.4",
    )


@router.get("/metrics/summary")
async def metrics_summary(request: Request):
    """Structured metrics summary endpoint for dashboards and UIs."""
    tracker = getattr(request.app.state, "metrics", None)
    if tracker is None:
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
            },
            "uptime_seconds": 0.0,
        }

    snap = tracker.snapshot()
    return {
        "totals": {
            "total_requests": snap.total_requests,
            "cache_hits": snap.cache_hits,
            "cache_misses": snap.cache_misses,
            "total_savings_usd": round(snap.total_savings_usd, 6),
            "total_cost_usd": round(snap.total_cost_usd, 6),
            "total_prompt_tokens": snap.total_prompt_tokens,
            "total_completion_tokens": snap.total_completion_tokens,
            "total_tokens": snap.total_tokens,
            "total_tokens_saved_estimate": snap.total_tokens_saved_estimate,
            "requests_routed": snap.requests_routed,
            "requests_rejected": snap.requests_rejected,
            "pipeline_errors": snap.pipeline_errors,
        },
        "rates": {
            "cache_hit_rate": round(tracker.cache_hit_rate, 2),
            "avg_savings_per_request_usd": round(tracker.avg_savings_per_request_usd, 6),
        },
        "uptime_seconds": round(snap.uptime_seconds, 1),
    }


@router.get("/metrics/summary/v2")
async def metrics_summary_v2(request: Request):
    """UI-focused summary with per-optimization breakdown and dynamic tabs."""
    config: CondenseConfig = getattr(request.app.state, "config", load_config())
    enabled_tabs = _enabled_optimization_ids(config)
    tracker = getattr(request.app.state, "metrics", None)

    if tracker is None:
        return {
            "overall": {
                "total_savings_usd": 0.0,
                "total_tokens_saved_estimate": 0,
                "total_requests": 0,
                "uptime_seconds": 0.0,
            },
            "enabled_tabs": enabled_tabs,
            "optimizations": [],
        }

    snap = tracker.snapshot()
    optimizations = []
    observed = dict(snap.optimization_totals)
    for optimization_id in enabled_tabs:
        observed.setdefault(
            optimization_id,
            {
                "optimization_id": optimization_id,
                "events": 0,
                "total_savings_usd": 0.0,
                "total_tokens_saved": 0,
                "tokens_saved": 0,
                "last_technique": None,
                "last_action": None,
                "last_details": {},
            },
        )

    for optimization_id, aggregate in observed.items():
        if optimization_id == "forward":
            continue
        optimizations.append(
            {
                "optimization_id": optimization_id,
                "events": int(aggregate.get("events", 0)),
                "total_savings_usd": round(float(aggregate.get("total_savings_usd", 0.0)), 6),
                "total_tokens_saved": int(aggregate.get("total_tokens_saved", 0)),
                "tokens_saved": int(aggregate.get("tokens_saved", aggregate.get("total_tokens_saved", 0))),
                "last_technique": aggregate.get("last_technique"),
                "last_action": aggregate.get("last_action"),
                "last_details": aggregate.get("last_details") or {},
            }
        )

    optimizations.sort(key=lambda entry: entry["optimization_id"])
    return {
        "overall": {
            "total_savings_usd": round(snap.total_savings_usd, 6),
            "total_tokens_saved_estimate": snap.total_tokens_saved_estimate,
            "total_requests": snap.total_requests,
            "uptime_seconds": round(snap.uptime_seconds, 1),
        },
        "enabled_tabs": enabled_tabs,
        "optimizations": optimizations,
    }


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
