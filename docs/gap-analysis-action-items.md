# Condense Proxy — Complete Gap Analysis & Action Items

## Context

This document captures ALL known gaps, hacks, and issues discovered during development and dogfooding. This is the honest, unfiltered assessment of what needs to be fixed before open-sourcing.

**Total issues found: 50+**

---

## 🔴 P0 — Must Fix Before Open Source

### 1. Streaming is Fake (Not Real Streaming)
**What we do:** Strip `stream: true`, wait for complete non-streaming response (5-20s), then fake SSE chunks.
**What industry does:** Forward each token chunk in real-time (~200ms to first token).
**Impact:** Any real user of Codex/Claude Code will think the tool is frozen.
**Fix:** Dual-path pipeline — optimize request (compress, route), forward stream transparently, buffer in background for caching.
**Effort:** 4-5 hours

### 2. `/v1/responses` Endpoint is Copy-Pasted, Incomplete Logic
**What's wrong:**
- Monkey-patches `request.json` (fragile, breaks FastAPI internals)
- Missing post-pipeline operations (no cache storage, no session update)
- Incomplete context (no session_id, cache_namespace, metadata)
- Incomplete headers (missing model tracking, savings info)
- Metrics use different method than chat_completions
- Always streams even when client doesn't ask
**Fix:** Extract shared `_execute_pipeline()` used by both endpoints. Clean Responses API translation layer.
**Effort:** 2-3 hours

### 3. Forward Step Model Prefix Hack
**What we do:** Blindly prefix unknown models with `openai/` so litellm accepts them.
**What's wrong:** No validation, no config-driven mapping, `cu/claude-4.5-sonnet` → `openai/cu/claude-4.5-sonnet`.
**Fix:** Config-driven `provider_type: "openai_compatible"`. Explicit model mapping.
**Effort:** 2 hours

### 4. Dummy API Key Hack
**What we do:** Silently pass `"condense-proxy"` as API key when none configured.
**What's wrong:** No logging, no config indication this is intentional, violates auth principle.
**Fix:** Explicit config `auth_required: false` or `api_key: "none"`.
**Effort:** 1 hour

### 5. Thread Safety — In-Memory Stores
**What's wrong:**
- `InMemoryCache._store` — `OrderedDict.move_to_end()` + eviction NOT atomic
- `SessionStore._sessions` — plain dict, no locks
- `CircuitBreaker._failure_count` — read/written concurrently, no locks
- `MetricsTracker._latencies` — list slice not atomic
**Fix:** Add `asyncio.Lock` to all mutation paths.
**Effort:** 2 hours

### 6. Decompression in Wrong Layer
**What we do:** gzip/zstd handling only in `/v1/responses`, not in `/v1/chat/completions`.
**Impact:** Any client sending compressed chat completions will fail.
**Fix:** Move to middleware.
**Effort:** 1 hour

### 7. Zero Test Coverage for New Features
| Feature | Tests? |
|---------|--------|
| `/v1/responses` endpoint | ❌ |
| `/v1/models` endpoint | ❌ |
| SSE streaming responses | ❌ |
| gzip/zstd decompression | ❌ |
| Forward step model prefix | ❌ |
| Codex flow E2E | ❌ |
**Fix:** Write comprehensive tests.
**Effort:** 3-4 hours

### 8. Missing CORS Middleware
**What's wrong:** No CORS handling. Browser-based clients will be rejected.
**Fix:** Add `CORSMiddleware` with configurable origins.
**Effort:** 30 min

### 9. Error Responses Leak Internal Details
**What's wrong:** `str(e)` in error responses exposes internal stack traces, config paths.
**Fix:** Sanitize error messages. Log full details server-side, return generic messages to client.
**Effort:** 1 hour

### 10. Request Body Consumed Twice
**What's wrong:** `dependencies.py` and `routes.py` both call `await request.json()`, consuming the stream. Second call fails silently.
**Fix:** Cache parsed body in request scope.
**Effort:** 30 min

### 11. No Rate Limiting
**What's wrong:** Zero throttling. Single client can flood proxy and upstream LLM providers.
**Fix:** Add per-IP or per-key rate limiting middleware.
**Effort:** 2 hours

### 12. Missing `/v1/messages` Endpoint (Anthropic Format)
**What's wrong:** Claude Code speaks Anthropic's Messages API (`/v1/messages`), not OpenAI format. Without this endpoint, Claude Code **cannot use Condense at all**. This was explicitly planned in our architecture doc ("Must build") and the IDE integration plan but was never implemented.
**What's needed:**
- `POST /v1/messages` — accept Anthropic Messages API format (`messages`, `system`, `max_tokens`, `model`)
- Translate to internal chat completions format for pipeline processing
- Translate response back to Anthropic format (`content`, `stop_reason`, `usage.input_tokens`)
- Anthropic-style SSE streaming (`event: content_block_delta`, `event: message_stop`)
- Handle Anthropic-specific headers (`anthropic-version`, `x-api-key`)
**Fix:** Build proper `/v1/messages` endpoint with bidirectional format translation, matching how we built `/v1/responses`.
**Effort:** 3-4 hours

### 13. CLI Startup Bug
**What's wrong:** `condense start --config ...` silently exits with code 0.
**Fix:** Debug and fix CLI entry point.
**Effort:** 1 hour

---

## 🟡 P1 — Should Fix Before Open Source

### 14. PipelineContext is a God Object
20+ fields mixing request state, metrics, tracking, metadata. `original_request` is shallow copy (nested dicts can be mutated).
**Fix:** Split into `RequestState`, `MetricsState`, `CacheState`. Use `copy.deepcopy`.
**Effort:** 3-4 hours

### 14. Health Check Doesn't Verify Dependencies
`/health/ready` returns 200 even if Redis/upstream are unreachable.
**Fix:** Actually check connectivity to Redis and upstream.
**Effort:** 1 hour

### 15. Session Store No Expiration
Old sessions accumulate in memory indefinitely. No TTL.
**Fix:** Add TTL-based eviction.
**Effort:** 1 hour

### 16. No Request ID / Correlation ID
No request ID generation or `X-Request-ID` propagation. Makes debugging impossible.
**Fix:** Generate UUID per request, include in logs and response headers.
**Effort:** 1 hour

### 17. No Structured Logging
All logging is string-based. Production needs JSON logging with fields (request_id, model, latency, cache_hit).
**Fix:** Use `structlog` or JSON formatter.
**Effort:** 2 hours

### 18. Graceful Shutdown Incomplete
Only closes `http_client` and Redis. Does NOT: cancel in-flight requests, wait for pending cache writes, flush metrics, close session store.
**Fix:** Proper shutdown hooks.
**Effort:** 2 hours

### 19. Redis Client Cleanup Unsafe
`app.py` accesses `cache_backend._redis.aclose()` — private attribute that may not exist or may be already closed.
**Fix:** Add proper `close()` method to cache backend interface.
**Effort:** 30 min

### 20. Provider Detection Fragile
`provider_detect.py` uses case-sensitive substring matching. Custom model names could false-positive. No version suffix handling.
**Fix:** More robust matching with regex patterns.
**Effort:** 1 hour

### 21. Request Logging Exposes Sensitive Data
`middleware.py` logs full request paths which may contain API keys in query parameters.
**Fix:** Sanitize sensitive data before logging.
**Effort:** 30 min

### 22. Semantic Cache No Tenant Isolation
Different API keys' embeddings can pollute each other's vector space.
**Fix:** Namespace isolation in vector store.
**Effort:** 2 hours

### 23. Metrics Snapshot Not Atomic
`tracker.py` shallow-copies `optimization_totals` — caller can modify and corrupt live metrics.
**Fix:** Deep copy in snapshot.
**Effort:** 30 min

### 24. Prometheus Metrics Missing Dimensions
No labels for model, provider, customer. Can't partition dashboards.
**Fix:** Add label dimensions.
**Effort:** 2 hours

---

## 🟢 P2 — Polish / Nice to Have

### 25. Config Hot-Reload Partial
Pipeline doesn't restart on config change. Old pipeline keeps old config. Cache backends not reinitialized.

### 26. No Dependency Injection Container
`app.state` accessed directly in routes. Hard to test/mock.

### 27. Upstream Client Timeout Not Validated
Zero or negative timeout values accepted without validation.

### 28. UI Asset Mounting No 404 Fallback
Refreshing non-root UI paths returns 404.

### 29. No SLA/Alerting Integration
No p99 latency metrics, upstream error rate tracking, cache size growth monitoring.

### 30. Streaming SSE Error Handling
If pipeline fails mid-stream, client receives malformed SSE.

---

## 📦 Deployment & Documentation Gaps

### 31. pyproject.toml — Missing Dependencies
- `openai` not in optional extras (needed for semantic-cache-openai)
- `zstandard` not listed (needed for Codex decompression)
- `routellm` package name needs verification

### 32. Dockerfile — Missing Optional Dependencies
Semantic cache with OpenAI embeddings won't work in Docker.

### 33. docker-compose.yml — Missing Health Check
No healthcheck for condense service. No `REDIS_PASSWORD` env var.

### 34. README.md — Incomplete
- `/v1/responses` endpoint not documented
- `/v1/models` endpoint not documented
- Streaming format not documented
- Health check endpoints not in API reference

### 35. DEVELOPMENT.md — Outdated
Doesn't mention new endpoints, new dependencies, or dogfooding setup.

### 36. .gitignore — Missing Entries
Missing: `*.log`, `.env.local`, model cache directories, `__pycache__` variations.

### 37. Config Presets — Undocumented
`aggressive.yaml`, `rag.yaml` exist but not referenced anywhere.

---

## Effort Estimate

| Priority | Count | Est. Hours |
|----------|-------|------------|
| 🔴 P0 | 13 | ~24 hours |
| 🟡 P1 | 12 | ~18 hours |
| 🟢 P2 | 6 | ~8 hours |
| 📦 Deploy/Docs | 7 | ~6 hours |
| **Total** | **38** | **~56 hours** |

**Realistic timeline:** 2-3 focused sprints (1 week each) to be open-source ready.

---

## Recommended Sprint Plan

### Sprint 1: Core Fixes (Week 1)
- [ ] Real streaming (dual-path pipeline)
- [ ] `/v1/messages` endpoint (Anthropic format — unlocks Claude Code)
- [ ] Shared pipeline execution (DRY routes — `/v1/chat/completions` + `/v1/responses` + `/v1/messages`)
- [ ] Thread safety (asyncio.Lock everywhere)
- [ ] Decompression middleware
- [ ] Forward step config-driven provider type
- [ ] CORS middleware
- [ ] Error response sanitization
- [ ] CLI startup fix

### Sprint 2: Tests & Safety (Week 2)
- [ ] Full test coverage for new endpoints
- [ ] Streaming tests
- [ ] Decompression tests
- [ ] Rate limiting
- [ ] Request ID tracking
- [ ] Structured logging
- [ ] Health check verification
- [ ] Graceful shutdown

### Sprint 3: Polish & Docs (Week 3)
- [ ] PipelineContext refactor
- [ ] README overhaul
- [ ] DEVELOPMENT.md update
- [ ] pyproject.toml cleanup
- [ ] Docker production readiness
- [ ] Config presets documentation
- [ ] Session TTL
- [ ] Tenant isolation in semantic cache
