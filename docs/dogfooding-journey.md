# Condense Proxy — Dogfooding Journey

## Overview

This document captures the complete journey of setting up Condense proxy for real-world dogfooding with agentic coding tools (Cursor IDE, OpenAI Codex CLI) through 9Router, using a Cursor subscription as the LLM backend.

**Date:** May 28, 2026
**Goal:** Prove that Condense can sit in front of real coding tools, optimize their LLM traffic, and show measurable savings.

---

## Architecture

```
Coding Tool (Cursor/Codex) → Condense (localhost:8080) → 9Router (localhost:20128) → Cursor Subscription → Claude 4.5 Sonnet
```

- **Condense** — our optimization proxy (caching, compression, routing)
- **9Router** — open-source multi-provider gateway that handles OAuth auth with Cursor subscription
- **Cursor Subscription** — the actual LLM provider (user's existing $100/month subscription)

---

## Phase 1: Setting Up 9Router

### Steps
1. Located 9Router at `/Users/agupta51/personal-projects/9router`
2. Installed dependencies: `cd 9router && npm install`
3. Set password in `.env`: `INITIAL_PASSWORD=condense2026`
4. Started 9Router: `node index.js`
5. Dashboard accessible at: `http://localhost:20128/dashboard`

### Configuring Cursor OAuth
- Logged into 9Router dashboard with password `condense2026`
- Added Cursor as a provider via OAuth flow
- Result: 1 active connection (`google-oauth2|user_01JMY498EJ1QJTYHD9T6P0S6CE`)
- 14 models available (all `cu/` prefixed — e.g., `cu/claude-4.5-sonnet`)

### Verifying 9Router Direct
```bash
# Confirmed models are accessible
curl http://localhost:20128/v1/models -H "Authorization: Bearer test"
# → 14 models listed

# Confirmed LLM requests work
curl http://localhost:20128/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model": "cu/claude-4.5-sonnet", "messages": [{"role": "user", "content": "What is the capital of Japan?"}], "stream": false}'
# → "Tokyo" — real response via Cursor subscription
```

---

## Phase 2: Configuring Condense

### Dogfood Config (`condense.dogfood.yaml`)
Created a dedicated config pointing Condense's upstream at 9Router:
```yaml
upstream:
  url: "http://localhost:20128/v1"  # 9Router
```

Key settings:
- Semantic caching enabled (threshold: 0.82)
- Exact caching enabled (in-memory)
- Routing rules enabled (short_messages, no_tools)
- Compression enabled (FusionEngine for code)
- Budget tracking enabled

### Issues Encountered & Fixes

#### Issue 1: LiteLLM doesn't recognize `cu/` model prefix
**Problem:** `litellm.acompletion(model="cu/claude-4.5-sonnet")` throws `BadRequestError` — litellm can't determine the provider from the `cu/` prefix.

**Fix:** In `forward_step.py`, added logic to prefix unknown models with `openai/` so litellm treats the upstream as an OpenAI-compatible endpoint:
```python
known_prefixes = ("openai/", "anthropic/", "ollama/", ...)
if not any(model_name.startswith(p) for p in known_prefixes):
    model_name = f"openai/{model_name}"
```

**Verdict:** ⚠️ HACK — should be config-driven (`provider_type: "openai_compatible"`)

#### Issue 2: LiteLLM requires API key even for keyless upstreams
**Problem:** `AuthenticationError: No API key provided` even though 9Router uses session-based OAuth, not API keys.

**Fix:** Pass a dummy API key when none is configured:
```python
api_key = os.environ.get(api_key_env, "condense-proxy")
```

**Verdict:** ⚠️ HACK — should be explicit config (`auth_required: false`)

#### Issue 3: Upstream URL missing `/v1`
**Problem:** LiteLLM constructs URL as `http://localhost:20128/chat/completions` (missing `/v1`).

**Fix:** Changed config from `url: "http://localhost:20128"` to `url: "http://localhost:20128/v1"`

**Verdict:** ✅ Proper fix — config was just wrong.

### First Successful Request Through Full Chain
```bash
curl -s http://127.0.0.1:8080/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model": "cu/claude-4.5-sonnet", "messages": [{"role": "user", "content": "What is the capital of Japan?"}]}'
# → "Tokyo" — through Condense → 9Router → Cursor → Claude 4.5 Sonnet!
```

### Cache Verification
| Request | Time | Cache |
|---------|------|-------|
| "What is the capital of Japan?" (1st) | 18.7s | ❌ MISS |
| "What is the capital of Japan?" (2nd) | 2.4ms | ✅ HIT exact (7,600x faster) |
| "Tell me the capital city of Japan please" (semantic) | 10.2ms | ✅ HIT semantic (1,800x faster) |

**Dashboard confirmed:** 66.7% cache hit rate, $0.04 saved, 8,072 tokens saved.

---

## Phase 3: Cursor IDE Integration — FAILED

### Attempt 1: OpenAI BYOK (Bring Your Own Key)
**Steps:**
1. Settings → Models → OpenAI API Key → entered dummy key
2. Override OpenAI Base URL → `http://localhost:8080/v1`
3. Added `/v1/models` pass-through endpoint to Condense (proxies to 9Router)

**Result:** ❌ "Your team has disabled OpenAI API keys. Please disable your API key in Cursor Settings > Models."

**Root cause:** User's Cursor is on a Teams/Enterprise plan — admin has disabled BYOK.

### Attempt 2: Custom Model via Add Model
- Model `cu/claude-4.5-sonnet` was recognized ("already available")
- But no Verify button appeared in the UI

### Key Learnings from Research
- Cursor's coding agent (Composer, inline edit, autocomplete) does NOT work with external OpenAI-compatible endpoints — locked to Cursor's backend
- Only Ask/Plan mode (`Cmd+L`) honors custom API key + base URL
- Team plans can disable BYOK entirely
- This is a fundamental limitation, not something we can work around

**Decision:** Pivot to OpenAI Codex CLI.

---

## Phase 4: Codex CLI Integration — SUCCESS (with caveats)

### Setup
Codex CLI v0.134.0 installed. Configured `~/.codex/config.toml`:

```toml
model = "cu/claude-4.5-sonnet"
openai_base_url = "http://localhost:8080/v1"
```

### Issue 1: `wire_api = "chat"` no longer supported
**Problem:** Codex v0.134 dropped `wire_api = "chat"` support — only `"responses"` works.

**Implication:** Codex CLI sends requests to `POST /v1/responses` (OpenAI Responses API format), NOT `POST /v1/chat/completions`. Our proxy didn't have this endpoint.

### Issue 2: Building `/v1/responses` Endpoint
Built a translation layer:
- **Input translation:** Responses API `input` + `instructions` → Chat Completions `messages`
- **Output translation:** Chat Completions `choices` → Responses API `output` with `type: "message"` objects
- **Usage translation:** `prompt_tokens`/`completion_tokens` → `input_tokens`/`output_tokens`

### Issue 3: Codex sends zstd-compressed request bodies
**Problem:** `'utf-8' codec can't decode byte 0xb5` — Codex compresses request bodies with zstandard.

**Fix:** Added decompression in the `/v1/responses` handler:
```python
content_encoding = request.headers.get("content-encoding", "").lower()
if content_encoding in ("zstd", "zstandard"):
    import zstandard
    dctx = zstandard.ZstdDecompressor()
    reader = dctx.stream_reader(io.BytesIO(raw_body))
    raw_body = reader.read()
```

Required `pip install zstandard`.

**Verdict:** ⚠️ Should be middleware, not per-endpoint.

### Issue 4: Missing `input_tokens` in response
**Problem:** `stream disconnected before completion: failed to parse ResponseCompleted: missing field input_tokens`

**Fix:** Expanded the response object to include all fields Codex expects:
```python
usage = {
    "input_tokens": raw_usage.get("prompt_tokens", 0),
    "output_tokens": raw_usage.get("completion_tokens", 0),
    "total_tokens": raw_usage.get("total_tokens", 0),
    "output_tokens_details": {"reasoning_tokens": 0},
}
```

Plus full response envelope: `error`, `incomplete_details`, `instructions`, `reasoning`, `store`, `temperature`, `text`, `tool_choice`, `tools`, `truncation`, `metadata`.

### Issue 5: Codex expects SSE streaming
**Problem:** Request returned 200 OK (visible in server logs and Cursor usage billing) but Codex showed no output.

**Fix:** Forced all `/v1/responses` responses to stream as SSE:
```
event: response.created
event: response.in_progress
event: response.output_text.delta
event: response.output_text.done
event: response.completed
```

### Issue 6: WebSocket attempts
**Observation:** Codex first tries WebSocket (`GET /v1/responses` → 405 Method Not Allowed), then falls back to HTTPS POST. This is expected behavior — not an error.

### Final Result
```
$ codex "Say hello in 3 words"

⚠ Model metadata for `cu/claude-4.5-sonnet` not found. (expected — using fallback metadata)
⚠ Falling back from WebSockets to HTTPS transport. (expected — we don't support WS)

POST /v1/responses → 200 OK ✅
```

**The full chain works:** Codex CLI → Condense → 9Router → Cursor subscription → Claude 4.5 Sonnet

**BUT:** Response text wasn't rendering in Codex terminal (SSE format may need further refinement for Codex's parser).

---

## Phase 5: CLI Startup Issue

### Problem
`condense start --config condense.dogfood.yaml` exits silently with code 0.

### Workaround
Started server directly via Python:
```python
import uvicorn
from condense.server.app import create_app
app = create_app('condense.dogfood.yaml')
uvicorn.run(app, host='127.0.0.1', port=8080)
```

### Root Cause
Not investigated — likely an issue in `cli.py`'s `start` command not calling `uvicorn.run()` correctly.

---

## Streaming Support Added

### For `/v1/chat/completions`
- Client sends `stream: true` → we strip it, run pipeline non-streaming, convert response to SSE
- Format: OpenAI chat completion chunks (`delta.content` + `[DONE]`)
- **Trade-off:** Adds latency (full response before any tokens sent) but allows all pipeline optimizations to work

### For `/v1/responses`
- Always streams (Codex requires it)
- Format: OpenAI Responses API events (`response.created` → `response.output_text.delta` → `response.completed`)

---

## Files Modified During Dogfooding

| File | Changes |
|------|---------|
| `condense.dogfood.yaml` | New config pointing upstream at 9Router |
| `condense/server/routes.py` | Added `/v1/responses`, `/v1/models`, SSE streaming, zstd decompression |
| `condense/pipeline/steps/forward_step.py` | Model prefix hack (`openai/`), dummy API key fallback |

---

## Known Gaps & Technical Debt

> **Full analysis with 37 issues and sprint plan:** See `docs/gap-analysis-action-items.md`

### 🔴 Critical (Must fix before open source)
1. **Streaming is FAKE** — we strip `stream: true`, wait for full response (5-20s), then fake SSE chunks. Real proxies forward tokens in real-time (~200ms to first token). **This is the #1 gap** — any real user will think the tool is frozen. Should have been flagged from the start.
2. **Missing `/v1/messages` endpoint (Anthropic format)** — Claude Code CANNOT use Condense without this. Was explicitly in our architecture plan ("Must build") but never implemented. Needs Anthropic-specific request/response translation and Anthropic-style SSE streaming.
3. **`/v1/responses` is duplicated logic** — should share pipeline execution with `/v1/chat/completions`
3. **Monkey-patching `request.json`** — fragile, breaks FastAPI internals
4. **Missing post-pipeline logic in responses_api** — no cache storage, no session update
5. **Model prefix hack** — `openai/` prefix is not config-driven
6. **Dummy API key** — should be explicit in config, not a silent fallback
7. **Decompression in wrong layer** — should be middleware, not per-endpoint
8. **Zero tests** for `/v1/responses`, `/v1/models`, streaming, decompression
9. **Thread safety** — InMemoryCache, SessionStore, CircuitBreaker, MetricsTracker all need locks
10. **Missing CORS middleware** — browser clients rejected
11. **Error responses leak internals** — `str(e)` exposes stack traces to clients
12. **No rate limiting** — single client can flood proxy and upstream

### 🟡 Important (Should fix)
13. **CLI startup bug** — `condense start` silently exits
14. **PipelineContext god object** — 20+ fields, shallow copy of original_request
15. **Health check doesn't verify dependencies** — returns 200 even if Redis/upstream down
16. **No request ID tracking** — makes debugging impossible
17. **No structured logging** — string-based logging, not production-ready
18. **Graceful shutdown incomplete** — doesn't cancel in-flight requests or flush metrics
19. **Session store no TTL** — old sessions accumulate forever
20. **Semantic cache no tenant isolation** — different API keys pollute each other

---

## Key Learnings

1. **Cursor Teams plans block BYOK** — can't use Condense with managed Cursor subscriptions without admin approval
2. **Codex CLI dropped `wire_api = "chat"`** — must support OpenAI Responses API (`/v1/responses`) for modern Codex
3. **Codex sends zstd-compressed bodies** — need decompression middleware
4. **Real coding tools need streaming** — non-streaming proxy won't work for interactive use
5. **9Router as upstream works beautifully** — OAuth handling + multi-provider routing complements our optimization layer
6. **Cache hits are dramatic** — 2.4ms vs 18.7s (7,600x speedup) proves the value proposition

---

## Metrics from Dogfooding Session

| Metric | Value |
|--------|-------|
| Total requests | ~10 |
| Cache hit rate | 66.7% |
| Tokens saved | 8,072 |
| USD saved | $0.04 |
| Fastest cache hit | 2.4ms (exact) |
| Slowest cache miss | 18.7s (real LLM call) |
| Pipeline errors | 0 |

---

## Next Steps

See `docs/gap-analysis-action-items.md` for the prioritized list of fixes needed before open source release.
