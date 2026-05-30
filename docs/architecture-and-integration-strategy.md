# Condense — Architecture & Integration Strategy

> This is the definitive document on how Condense integrates with AI coding tools
> and the broader LLM ecosystem. All other docs should reference this.

---

## What Condense Is

Condense is an **LLM cost-optimization proxy**. It reduces the cost of every LLM call
through six techniques:

1. **Exact caching** — identical requests return cached responses instantly
2. **Semantic caching** — rephrased questions hit cache via embedding similarity
3. **Prompt compression** — FusionEngine (code), LLMLingua (natural language)
4. **ML model routing** — BERT classifier routes simple queries to cheaper models
5. **Session budget enforcement** — cost/turn caps per session
6. **Provider prompt cache injection** — Anthropic/OpenAI cache_control hints

It does NOT manage providers, OAuth tokens, format translation between providers,
or multi-provider fallback. Those are separate concerns.

---

## How Tools Connect to LLM Providers (The Reality)

### Two Authentication Models

**API Key (pay-per-token):**
```
Tool → Provider API (api.openai.com / api.anthropic.com)
Auth: Bearer sk-xxx / x-api-key: sk-ant-xxx
Billing: Per token consumed
User controls: base_url (can point anywhere)
```

**Subscription (credit pool):**
```
Tool → Provider's own endpoint (hardcoded or OAuth-managed)
Auth: OAuth session token / cookie / protobuf auth
Billing: Monthly subscription ($20-$200) with token-based credit depletion
User controls: NOTHING — tool manages the connection
```

### Critical Insight

Subscription tools (Cursor Pro, Claude Code Max) use **token-based credit pools**
that deplete based on actual token consumption. A Cursor Pro user with $20/month
gets ~225 Claude Sonnet requests. Every token we save extends their credit pool.

BUT — we cannot directly intercept subscription traffic because the tool controls
the endpoint and authentication.

---

## Condense Integration Architecture

### Dual-Mode Design

Condense operates in two modes via a single config change (`upstream.url`):

```
                     ┌─────── Mode A: Standalone ───────┐
                     │                                   │
Tool (BYOK/API key) ─┤                                   ├─→ Provider API
                     │         Condense Proxy             │
                     │  • Cache (exact + semantic)        │
Tool (via 9Router) ──┤  • Compress (code/NL/tool output) │
                     │  • Route (ML classifier)           ├─→ 9Router → Providers
                     │  • Budget enforcement              │
                     │  • Metrics + savings dashboard     │
                     └─────── Mode B: Before 9Router ────┘
```

**Mode A — Standalone (API key users):**
```yaml
upstream:
  url: "https://api.openai.com/v1"  # or api.anthropic.com
```
User sets their tool's base URL to Condense. Condense forwards directly to the
provider. Works for anyone with an API key.

**Mode B — Before 9Router (subscription + free tier users):**
```yaml
upstream:
  url: "http://localhost:20128"  # 9Router
```
User sets their tool's base URL to Condense. Condense optimizes the request,
then forwards to 9Router. 9Router handles provider selection, OAuth, format
translation, and fallback. This reaches subscription users because 9Router
manages their authentication.

**Zero code changes between modes.** Same binary, same pipeline, different config.

---

## Who Can Use Condense

### Mode A: Standalone

| Tool | How to Connect | What Condense Optimizes |
|------|---------------|------------------------|
| **Cursor (BYOK)** | Settings → Models → Base URL → `http://localhost:8080/v1` | Caching, compression, routing, budget |
| **Claude Code (API key)** | `ANTHROPIC_BASE_URL=http://localhost:8080` | Caching, compression, routing, budget |
| **Codex CLI** | `OPENAI_BASE_URL=http://localhost:8080/v1` | Caching, compression, routing, budget |
| **Aider** | `--openai-api-base http://localhost:8080/v1` | Caching, compression, routing, budget |
| **Cline / Continue** | Settings → Base URL → `http://localhost:8080/v1` | Caching, compression, routing, budget |
| **LangChain / LiteLLM** | `base_url="http://localhost:8080/v1"` | Caching, compression, routing, budget |
| **Enterprise gateway** | DNS / load balancer → Condense cluster | All optimizations at scale |

### Mode B: Before 9Router

| Tool | How to Connect | What Condense + 9Router Optimize |
|------|---------------|----------------------------------|
| **Cursor (subscription)** | Base URL → Condense → 9Router | Cache + compress + route (Condense) + fallback + quota tracking (9Router) |
| **Claude Code Max** | Base URL → Condense → 9Router | Same |
| **Any tool via 9Router** | Base URL → Condense → 9Router | Same |

### Cannot Reach (Today)

| Tool | Why | Future Path |
|------|-----|-------------|
| Cursor subscription (direct) | OAuth + hardcoded endpoint | 9Router solves this (Mode B) |
| GitHub Copilot subscription | VS Code extension, no base URL | IDE extension (Approach 5, speculative) |
| Tool-specific features (Cursor Tab, Apply) | Uses Cursor's own models | No known path |

---

## Why This Architecture (Approaches Considered)

| # | Approach | Verdict | Reason |
|---|----------|---------|--------|
| 1 | **Standalone proxy (base URL override)** | ✅ **Mode A** | Works today for API key users. Simple, clean. |
| 2 | **Optimization layer before 9Router** | ✅ **Mode B** | Config change only. Reaches subscription users via 9Router. |
| 3 | Build our own provider replacement (like 9Router) | ❌ Rejected | 94K LOC of provider wrangling. Not our competency. Months of work. |
| 4 | SDK/middleware (monkey-patch LLM SDKs) | ⏳ Future | Good for app developers, can't help coding tools. |
| 5 | IDE extension (intercept context inside IDE) | ⏳ Speculative | Could reach subscription users, but uncertain feasibility. |
| 6 | Network-level MITM proxy | ❌ Rejected | Security nightmare. TLS interception. Users won't trust it. |

---

## Edge Cases & How We Handle Them

### Streaming
- **Cache miss:** SSE events from upstream pass through transparently
- **Cache hit:** Condense synthesizes SSE events from cached response
- **Implementation needed:** Must add streaming support to ForwardStep

### Model String Preservation
- 9Router uses `provider/model` format (e.g., `kr/claude-sonnet-4.5`)
- Condense MUST preserve model strings as-is when forwarding to 9Router
- ML routing must only rewrite model when running standalone (Mode A)
- Cache keys include the full model string, so `kr/claude-sonnet-4.5` and `claude-sonnet-4.5` are separate entries

### Compression Overlap with RTK
- Condense compression: FusionEngine (code), LLMLingua (natural language), future RTK (tool outputs)
- 9Router's RTK: compresses tool_result blocks
- **No conflict:** If both run, RTK's safety check ensures it never makes output bigger.
  Slight redundancy but zero harm.
- **Recommended config for Mode B:** Disable tool output compression in Condense,
  let 9Router's RTK handle it. Enable FusionEngine + LLMLingua for non-tool content.

### Cache Key with Provider Rotation (9Router Combos)
- 9Router may rotate providers (Claude → GLM → Kiro) for the same query
- First successful response gets cached in Condense
- Subsequent identical/similar queries get the cached response regardless of which
  provider 9Router would have chosen
- This is a **feature**: cached response quality is consistent, and we skip the
  LLM entirely

### Anthropic API Format
- Claude Code sends Anthropic format (`/v1/messages`)
- Different from OpenAI format (`/v1/chat/completions`)
- Different streaming event types (`content_block_start`, `content_block_delta`)
- **Must build:** `/v1/messages` endpoint with Anthropic format support

---

## What We Need to Build (Ordered by Priority)

### Phase 1: Multi-Format Support (Enable Claude Code + Cursor BYOK)
1. `/v1/messages` endpoint (Anthropic format handling)
2. `/v1/models` endpoint (tool discovery)
3. Streaming support (SSE pass-through + synthetic SSE for cache hits)
4. Verify Cursor BYOK integration end-to-end

### Phase 2: Tool Output Compression (RTK for Condense)
5. RTK compression backend (auto-detect tool output → apply filters)
6. `@compression_registry.register("tool_output")`

### Phase 3: Distribution & DX
7. PyPI publish (`pip install condense-proxy`)
8. Docker Hub image
9. `condense connect <tool>` CLI helper
10. Per-tool setup guides in README

### Phase 4: Production Hardening
11. Fix concurrency bugs (session store + cache locks)
12. Persistence (survive restarts)
13. CI/CD (GitHub Actions)
14. Request correlation IDs

---

## Condense vs 9Router: Complementary, Not Competing

| | Condense | 9Router |
|---|----------|---------|
| **Core job** | Optimize every LLM call | Route to the right provider |
| **Techniques** | Cache, compress, ML-route, budget | Fallback, round-robin, quota tracking |
| **Provider knowledge** | None (transparent proxy) | 40+ providers, OAuth, protobuf |
| **Together** | Condense reduces tokens → 9Router routes optimized request → Provider |

The ideal stack for maximum savings:
```
Tool → Condense (optimize) → 9Router (route) → Provider (serve)
```

9Router saves you from rate limits. Condense saves you from unnecessary spending.
Together, they maximize both availability AND cost efficiency.
