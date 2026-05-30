# Agentic IDE Integration Plan

> **Reference:** See `docs/architecture-and-integration-strategy.md` for the
> definitive dual-mode architecture (standalone + before-9Router).

> Status: **Planned** — enabling Condense as a transparent optimization proxy for AI coding tools.

---

## Vision

Condense sits between AI coding tools and LLM providers, automatically optimizing every request — caching, compression, routing, budgeting — with zero code changes in the tools themselves.

```
┌─────────────────────────────────────────────────────────┐
│  AI Coding Tools (any)                                  │
│  Claude Code · Cursor · Codex · Aider · Cline · etc.   │
└─────────────────────┬───────────────────────────────────┘
                      │  Just change the base URL
                      ▼
┌─────────────────────────────────────────────────────────┐
│  Condense Proxy (localhost:8080)                        │
│                                                         │
│  /v1/chat/completions  ← OpenAI format (Cursor, Codex) │
│  /v1/messages          ← Anthropic format (Claude Code) │
│  /v1/models            ← Model discovery                │
│  /dashboard            ← Real-time savings UI           │
│                                                         │
│  Pipeline: Cache → Compress → Route → Budget → Forward  │
└─────────────────────┬───────────────────────────────────┘
                      │
                      ▼
┌─────────────────────────────────────────────────────────┐
│  LLM Providers                                          │
│  OpenAI · Anthropic · Ollama · DeepSeek · Gemini · etc. │
└─────────────────────────────────────────────────────────┘
```

---

## How Each Tool Connects

### Tier 1: Already Works (OpenAI-compatible)

These tools speak OpenAI format. Condense already serves `/v1/chat/completions`.

#### Cursor IDE

```
Cursor Settings → Models → Override API Base URL
→ http://localhost:8080/v1

# OR via environment variable:
OPENAI_BASE_URL=http://localhost:8080/v1 cursor
```

**Note:** Cursor requires a reachable URL. For local development, `localhost` works.
For team setups, deploy Condense to a server or use a tunnel (`ngrok`, `cloudflared`).

**Gotchas:**
- Cursor sends both regular chat and "agent mode" requests — both go through the same endpoint
- Tool call format follows OpenAI spec — our proxy handles this natively
- Streaming SSE must be reliable — Cursor will silently fail on buffered/broken streams

#### OpenAI Codex CLI

```bash
OPENAI_BASE_URL=http://localhost:8080/v1 \
OPENAI_API_KEY=your-key \
codex
```

**Gotchas:**
- Codex uses the OpenAI Responses API (`/v1/responses`) in some modes — we'd need to add this endpoint for full compatibility
- Standard chat completions mode works today

#### Aider

```bash
aider --openai-api-base http://localhost:8080/v1 \
      --openai-api-key your-key
```

**Gotchas:**
- Aider makes many small requests (file edits) — exact cache hit rate will be high
- Supports `--model` flag to pick any model — routing step can override for cost savings

#### Continue.dev (VS Code / JetBrains)

```json
// ~/.continue/config.json
{
  "models": [
    {
      "title": "GPT-4o via Condense",
      "provider": "openai",
      "model": "gpt-4o",
      "apiBase": "http://localhost:8080/v1",
      "apiKey": "your-key"
    }
  ]
}
```

**Gotchas:**
- Continue sends tab-completion requests (very short) — perfect for model routing (route to cheaper model)
- Also sends larger "explain" / "refactor" requests — compression helps here

#### Cline (VS Code Extension)

```
Cline Settings → API Provider → OpenAI Compatible
→ Base URL: http://localhost:8080/v1
→ API Key: your-key
→ Model: gpt-4o
```

**Gotchas:**
- Cline sends large tool outputs (file contents, terminal output) — RTK-style compression is critical
- Streaming must work correctly for the real-time output display

#### Windsurf / Cascade

```bash
OPENAI_BASE_URL=http://localhost:8080/v1 windsurf
```

**Gotchas:**
- Windsurf uses a mix of direct and MCP-based tool calls
- Streaming behavior is similar to Cursor

#### Roo Code

```
Extension Settings → API Configuration
→ Base URL: http://localhost:8080/v1
```

---

### Tier 2: Needs `/v1/messages` Endpoint (Anthropic-compatible)

These tools speak Anthropic's Messages API natively. We need to add a `/v1/messages` route.

#### Claude Code (CLI)

```bash
ANTHROPIC_BASE_URL=http://localhost:8080 \
ANTHROPIC_API_KEY=your-key \
claude
```

**What needs to happen:**
1. Add `/v1/messages` POST route that accepts Anthropic Messages API format
2. Translate internally: Anthropic request → our pipeline → Anthropic response
3. Support Anthropic-specific SSE event format (`content_block_delta`, `message_stop`, etc.)
4. Preserve `cache_control` markers (our ProviderCacheStep already injects these)

**Anthropic request format:**
```json
{
  "model": "claude-sonnet-4-20250514",
  "max_tokens": 8096,
  "system": "You are a helpful assistant.",
  "messages": [
    {"role": "user", "content": "Explain Python decorators"}
  ],
  "stream": true
}
```

**Anthropic SSE format (different from OpenAI):**
```
event: message_start
data: {"type":"message_start","message":{"id":"msg_...","model":"claude-sonnet-4-20250514",...}}

event: content_block_start
data: {"type":"content_block_start","index":0,"content_block":{"type":"text","text":""}}

event: content_block_delta
data: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"Hello"}}

event: content_block_stop
data: {"type":"content_block_stop","index":0}

event: message_stop
data: {"type":"message_stop"}
```

**Claude Code-specific considerations:**
- Sends large `tool_result` blocks (file reads, bash output, grep results)
- Uses `cache_control` for prompt caching — we should preserve AND enhance this
- Session conversations are long (50+ turns) — semantic cache should respect `max_conversation_turns`
- Naming/warmup requests should be detected and handled cheaply

---

### Tier 3: Needs Additional Work

#### GitHub Copilot

Copilot uses a proprietary protocol — cannot be proxied via base URL override.
Not feasible without reverse-engineering the Copilot extension protocol.

#### JetBrains AI Assistant

Uses JetBrains-specific API format. Would need a dedicated translator.
Low priority — Continue.dev on JetBrains is the better integration path.

---

## Implementation Phases

### Phase 1: Multi-Format API Gateway (Immediate Priority)

| Task | Effort | Impact |
|------|--------|--------|
| Add `/v1/messages` route (Anthropic format) | 2-3 days | Unlocks Claude Code |
| Add `/v1/models` route | 0.5 day | Tool model discovery |
| Anthropic SSE streaming support | 1-2 days | Required for Claude Code |
| Test with real Cursor | 0.5 day | Validate OpenAI path |
| Test with real Claude Code | 0.5 day | Validate Anthropic path |
| Add `condense connect <tool>` CLI helper | 1 day | DX improvement |

### Phase 2: Tool Output Compression (RTK-style)

See `docs/rtk-tool-output-compression-plan.md` for details.

| Task | Effort | Impact |
|------|--------|--------|
| `tool_output` compression backend | 2-3 days | 20-40% token savings on tool results |
| Auto-detect filters (git diff, grep, ls, etc.) | 2 days | Domain-specific compression |
| Per-tool savings tracking | 1 day | Dashboard: "Claude Code saved $X" |

### Phase 3: Distribution & DX

| Task | Effort | Impact |
|------|--------|--------|
| Docker Hub image | 0.5 day | `docker run condense-proxy` |
| `pipx install condense` | 0.5 day | One-command install |
| `condense connect` CLI | 1 day | Print setup instructions per tool |
| Per-tool savings in dashboard | 1 day | "Cursor: $12.40 saved this week" |

### Phase 4: Advanced

| Task | Effort | Impact |
|------|--------|--------|
| OpenAI Responses API (`/v1/responses`) | 2 days | Full Codex compatibility |
| Anthropic token counting (`/v1/messages/count_tokens`) | 1 day | Claude Code compatibility |
| Per-tool request identification (user-agent parsing) | 0.5 day | Dashboard breakdown by tool |
| Team deployment guide (Docker + public URL) | 1 day | Multi-developer setups |

---

## Format Translation Strategy

Following 9router's proven approach — **OpenAI as the pivot format**:

```
Anthropic request → Translate to OpenAI → Pipeline → Translate response to Anthropic
                                            ↕
OpenAI request ──────────────────→ Pipeline → OpenAI response (no translation needed)
```

This means:
- The pipeline always works with one internal format (OpenAI-style dicts)
- We only need 2 translators total: `anthropic_to_openai` and `openai_to_anthropic`
- Adding a new format (e.g., Gemini) = 2 more translators, not N²

**Why this works:** LiteLLM (which we already use for forwarding) already handles the reverse translation when calling upstream providers. We just need to handle the client-facing translation.

---

## Dogfooding Strategy

| Tool | Status | Priority |
|------|--------|----------|
| **Cursor** → Condense → OpenAI | ✅ Works today | Start dogfooding immediately |
| **Claude Code** → Condense → Anthropic | 🔨 After Phase 1 | Primary dogfood target |
| **Codex CLI** → Condense → OpenAI | ✅ Works today | Secondary dogfood |
| **Aider** → Condense → Any | ✅ Works today | Testing tool |

**Dogfooding loop:** Use Condense → find savings → find bugs → improve → repeat.

---

## Industry Reference

### 9router (github.com/decolua/9router)
- Node.js/Next.js, serves `/v1/chat/completions` + `/v1/messages` + `/v1/responses`
- Format translation via registry pattern (OpenAI as pivot)
- RTK token saver for tool_result compression
- Focus: multi-provider routing & subscription quota management
- **Complementary to Condense** — they route, we optimize

### LiteLLM Proxy
- Python, OpenAI-compatible proxy
- Focus: unified API for 100+ providers
- No caching, compression, or ML routing
- We already use LiteLLM as our forwarding layer

### Portkey / Helicone / Braintrust
- Cloud-hosted proxies with observability
- Focus: logging, analytics, guardrails
- No local deployment, no response caching
- Different market segment (enterprise observability)
