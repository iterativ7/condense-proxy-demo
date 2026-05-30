# Condense — B2C Strategy & Market Positioning

> Compiled from deep analysis of the AI coding tool ecosystem, 9Router's approach,
> and honest assessment of where Condense fits. This document captures both the
> research and the strategic reasoning behind our positioning decisions.

---

## The Ecosystem Reality (As of May 2026)

### How AI Coding Tools Bill Their Users

There are two fundamentally different billing models in the market today, and
they determine who our customer is:

#### Model 1: Subscription (Credit Pool)
```
User pays:    $20-200/month (Cursor Pro/Pro+/Ultra, Claude Code Max)
What they get: Dollar-equivalent credit pool that depletes per token
API key:      NO — auth is via OAuth/session token
Token billing: Indirect — credits deplete based on model × tokens consumed
User controls: Cannot change where the tool sends requests (subscription traffic)
```

Real-world numbers (Cursor Pro, $20/month):
- Claude Sonnet 4: ~225 requests per $20
- GPT 4.1: ~650 requests per $20
- Auto mode: Unlimited (does not deplete credits)
- Overages billed at API rates when pool exhausted

#### Model 2: API Key (Pay-Per-Token)
```
User pays:    Per token consumed ($3/M input, $15/M output for Claude Sonnet)
What they get: Unlimited requests, billed by usage
API key:      YES — sk-ant-xxx or sk-xxx
Token billing: Direct — every token costs money
User controls: Can change base URL → CAN route through Condense
```

#### Model 3: Hybrid (BYOK)
Some tools support "bring your own key" alongside subscription:
- Cursor: Settings → Models → Use own API key (bypasses credit system)
- Claude Code: `ANTHROPIC_API_KEY` env var (direct API, not subscription)

This bypasses the subscription credit system entirely. User pays the provider
directly at API rates, which removes the tool's per-token margin.

---

## How 9Router Handles Subscriptions (Deep Technical Analysis)

### What 9Router Actually Does

9Router doesn't intercept subscription traffic. It **replaces the provider entirely**
by reverse-engineering each tool's authentication and protocol:

### Provider-by-Provider Breakdown

#### Claude Pro/Max (Anthropic Subscription)
- **Auth:** OAuth device code flow using Claude CLI's real client ID (`9d1c250a-...`)
- **Token URL:** `api.anthropic.com/v1/oauth/token`
- **Token refresh:** Every ~4 hours, auto-refreshed via OAuth refresh token
- **Header spoofing:** 12+ headers mimicking Claude CLI exactly:
  - `User-Agent: claude-cli/2.1.92 (external, sdk-cli)`
  - `X-App: cli`
  - `Anthropic-Dangerous-Direct-Browser-Access: true`
  - Full X-Stainless SDK fingerprint (runtime, arch, OS, package version)
- **Protocol:** Standard Anthropic Messages API (`/v1/messages`)
- **How it works:** Request arrives in any format → 9Router translates to Anthropic
  format → sends to `api.anthropic.com` with OAuth token + spoofed CLI headers →
  Anthropic sees "Claude CLI, valid subscription" → processes against credit pool

#### Cursor ($20-200/month Subscription)
- **Auth:** Access token from Cursor IDE + machine ID
- **Checksum:** Reverse-engineered "Jyh Cipher" — timestamp-based XOR cipher with
  rolling key, base64 encoded, appended with machine ID
- **Header spoofing:** 15+ headers mimicking Cursor IDE:
  - `x-cursor-checksum: <jyh_cipher_output>`
  - `x-cursor-client-version: 3.1.0`
  - `x-cursor-client-type: ide`
  - `x-client-key: sha256(token)`
  - `x-session-id: uuidv5(token)`
  - `content-type: application/connect+proto`
- **Protocol:** gRPC over HTTP/2 with Connect protocol + Protobuf
  - Requests serialized to Protobuf via `generateCursorBody()`
  - Responses decoded from gRPC Connect frames (compressed protobuf)
  - `parseConnectRPCFrame()` handles gzip/deflate decompression
- **Token refresh:** None (manual re-paste when expired)

#### Codex (OpenAI/ChatGPT Subscription)
- **Auth:** OAuth device code flow with OpenAI auth server
- **Endpoint:** `chatgpt.com/backend-api/codex/responses` (private, not public API)
- **Header spoofing:** `originator: codex-cli`, `User-Agent: codex-cli/1.0.18`
- **Protocol:** OpenAI Responses API (newer format with `input[]`, not `messages[]`)
- **Token refresh:** Every ~5 days via OAuth refresh token
- **Special handling:** Session management, tool normalization, server-generated ID stripping

#### GitHub Copilot ($10-39/month Subscription)
- **Auth:** Two-step: GitHub OAuth token → exchange for short-lived `copilotToken`
- **Endpoint:** `api.githubcopilot.com/chat/completions`
- **Header spoofing:** Mimics VS Code Copilot Chat extension (editor version, plugin version, user agent)
- **Token refresh:** Every ~30 minutes (copilotToken is short-lived)
- **Special handling:** Message sanitization (only text/image_url parts accepted)

#### Kiro (AWS, free with account)
- **Auth:** OAuth with Kiro auth server
- **Protocol:** AWS EventStream binary format (not JSON SSE)
  - Binary framing with big-endian headers, CRC checksums
  - `parseEventFrame()` decodes binary → JSON → SSE
- **Token refresh:** Short-lived tokens, auto-refreshed

#### Antigravity / Gemini CLI (Google, free with account)
- **Auth:** Google OAuth2 with Gemini CLI's embedded client ID + secret
- **Endpoint:** `cloudcode-pa.googleapis.com/v1internal` (internal, not public API)
- **Special handling:** Auto-discovers GCP project ID on first use
- **Token refresh:** Every ~5 minutes

### The Combo/Fallback System

9Router chains providers together in priority order:

```
Combo: "my-coding-stack"
  1. cc/claude-opus-4       → Claude subscription (OAuth)
  2. gh/claude-sonnet-4     → GitHub Copilot (OAuth)
  3. glm/glm-4.7            → GLM API key ($0.60/1M)
  4. kiro/claude-sonnet-4   → Kiro free tier (OAuth)
  Strategy: "fallback"      → try in order, fall back on failure
```

On each request:
1. Try provider 1 → if 200, return
2. If 429/503, mark account unavailable (exponential backoff), try next
3. Multi-account support per provider (round-robin across accounts)
4. All models fail → return 503 "All combo models unavailable"

---

## The Honest Assessment: Should Condense Piggyback on This?

### The Temptation

Mode B (Condense → 9Router) could reach ALL subscription users. Every Cursor Pro,
Claude Max, and Copilot user could benefit from our optimizations. The market is
massive. The value is real (extending credit pools by 40%).

### Why We Should NOT Build Our B2C Strategy Around This

#### 1. It's Building on Quicksand

9Router's subscription proxying works today because providers haven't cracked down
**yet**. But the trajectory is clear:

- Cursor implemented checksum verification (Jyh cipher) — an anti-proxy measure
- Anthropic added `Anthropic-Dangerous-Direct-Browser-Access` header — tracking non-standard clients
- iFlow and Qwen already discontinued free tiers — providers are tightening
- A GitHub user raised security concerns about 9Router's `9remote` feature

If Anthropic detects and blocks spoofed CLI headers, or Cursor changes their
protobuf format, that entire user base evaporates overnight. And our brand gets
associated with the breakage.

#### 2. It's Not Our Value Proposition

Two pitches:

**Pitch A (piggybacking):**
> "Install 9Router, install Condense, chain them together, and your Claude
> subscription lasts 40% longer by exploiting Anthropic's OAuth flow through
> a reverse-engineered proxy."

**Pitch B (standalone, honest):**
> "Set your API base URL to Condense. Same provider, same billing, but 40%
> fewer tokens consumed. Caching, compression, and smart routing — all transparent."

Pitch A requires the user to understand and accept ToS risk. Pitch B is clean —
we're optimizing traffic the user already controls. No spoofing, no gray area.

#### 3. The Brand Risk Is Real

If Condense becomes known as "the tool that helps you game your AI subscriptions":

- **Enterprise credibility gone** — CISOs won't approve a tool associated with ToS violations
- **Provider partnerships blocked** — Anthropic and OpenAI won't feature or recommend us
- **Marketplace presence lost** — IDE marketplaces won't list tools associated with credential abuse
- **Long-term trust eroded** — legitimate users leave if we're perceived as a hack

9Router can afford this positioning — they're community-driven, MIT-licensed, with
no commercial ambitions. If Condense ever wants to be a business or a respected
open-source project, we can't afford it.

#### 4. The Market Without Subscription Proxying Is Still Massive

| Segment | Needs 9Router? | Market Size | Annual Value Per Customer |
|---------|:-:|------|-----------|
| **Cursor BYOK users** | No | Millions (explicitly supported by Cursor) | $200-2,000/year in API costs |
| **Claude Code API key users** | No | Large | $200-5,000/year |
| **Enterprise teams** (centralized API) | No | High value | $50K-500K/year |
| **App developers** (production LLM calls) | No | Massive | $1K-100K/year |
| **Self-hosted LLM users** | No | Growing fast | Value = speed, not $ |

**Enterprise API spend alone is a multi-billion dollar market.** A 10-person team
spending $50K/year on API calls saves $20K/year with Condense. That's real money
with zero ToS risk.

#### 5. Subscription Users Will Find Mode B On Their Own

If someone already uses 9Router, they can point it through Condense with one
config change. We don't need to market this. We just need to not break it.

```yaml
# User figures this out themselves:
upstream:
  url: "http://localhost:20128"  # 9Router
```

We document compatibility. We don't promote exploitation.

---

## Our B2C Strategy: Clean, Honest, Defensible

### Primary Target: API Key Power Users

Developers who **choose** API keys over subscriptions because:
- They want higher rate limits than subscription provides
- Their company provides API keys (enterprise engineering teams)
- They're building applications, not just using coding tools
- They want full control over their LLM usage and billing

**Our pitch:** "You're already paying per token. Condense makes every token count."

**How they connect:**
```bash
# Cursor BYOK
# Settings → Models → OpenAI API Base URL → http://localhost:8080/v1

# Claude Code with API key
ANTHROPIC_BASE_URL=http://localhost:8080 claude

# Codex CLI
OPENAI_BASE_URL=http://localhost:8080/v1 codex

# Any SDK
client = OpenAI(base_url="http://localhost:8080/v1")
```

### Secondary Target: Enterprise Teams

Engineering organizations with $10K-500K/month in LLM API spend across teams.

**Our pitch:** "Drop-in proxy for your engineering org. No code changes. 40% savings
with a dashboard your VP of Engineering can show the CFO."

**How they connect:**
```
DNS / Load Balancer → Condense Cluster → Provider API
All developer tools configured with company API keys → auto-routed through Condense
```

### Tertiary Target: App Developers

Anyone using OpenAI/Anthropic SDK in production: RAG chatbots, coding assistants,
document processors, customer support bots.

**Our pitch:** "Add one line to your config. Semantic caching means 30% of queries
never hit the API. Exact same responses, zero additional cost."

### Position on Subscription Users

We mention Mode B (Condense → 9Router) in technical docs as a compatibility note.
We don't market it. We don't promote it. We don't build features specifically for
subscription stretching.

If someone chains Condense → 9Router, our optimizations help regardless. But that's
their choice, not our pitch.

---

## Competitive Positioning

### vs 9Router

**We are complementary, not competing.**

| | 9Router | Condense |
|---|---------|----------|
| Core job | Route to the right provider | Optimize every LLM call |
| Hook | "Never hit limits" / "FREE AI" | "Same AI, 40% cheaper" |
| Approach | Provider arbitrage + credential proxying | Transparent optimization |
| ToS risk | Gray area (spoofed credentials) | Clean (standard proxy) |
| Revenue model | None (community project) | TBD (open core?) |
| Enterprise-ready | No (local-only, no auth) | Building toward yes |

### vs Other Proxies (LiteLLM, Portkey, Helicone)

| | LiteLLM | Portkey | Helicone | Condense |
|---|---------|---------|----------|----------|
| Routing | ✅ Model routing | ✅ Smart routing | ❌ | ✅ ML-based routing |
| Caching | ✅ Simple caching | ✅ Prompt caching | ❌ | ✅ Exact + Semantic |
| Compression | ❌ | ❌ | ❌ | ✅ Three backends |
| Observability | ✅ | ✅ Rich | ✅ Rich | ⚠️ Basic dashboard |
| Open source | ✅ | Partial | ✅ | ✅ |
| Self-hosted | ✅ | ✅ | ✅ | ✅ |
| Unique value | Universal gateway | Enterprise features | Analytics | **Optimization depth** |

**Our moat:** No other open-source proxy combines semantic caching + ML model routing +
multi-backend compression. These are genuinely novel and hard to replicate.

---

## Growth Strategy

### What We Can Learn from 9Router's Growth (14.6K Stars)

9Router grew explosively by:
1. Solving an acute, universal pain point ("I hit my rate limit")
2. One-command install (`npx 9router`)
3. GitHub trending algorithm amplification
4. YouTube creator partnerships
5. Multi-language README
6. Trust-first positioning ("runs locally, no billing")

### What We Should Replicate

| Tactic | How to Apply | Effort |
|--------|-------------|--------|
| Specific pain messaging | "Cut your LLM bill by 40% — one command, zero code changes" | Low |
| One-command install | `pipx install condense-proxy && condense start` | Medium |
| Benchmark proof | "10K queries: $47 → $28. Here's the data." | Low (we have benchmarks) |
| Tool-specific guides | "How to save 40% on Claude Code costs" — SEO goldmine | Low |
| Trust messaging | "Runs locally. Your data never leaves your machine." | Low |
| Creator content | Blog posts + YouTube showing real $ savings | Medium |
| Contributor incentives | "Built a backend? Submit a PR" (like 9Router's video policy) | Low |

### What We Should NOT Replicate

| Tactic | Why Not |
|--------|---------|
| "FREE AI" hook | We don't provide free access, we optimize existing access |
| Provider arbitrage | Not our business, introduces ToS risk |
| Subscription credential proxying | Gray area, quicksand foundation |
| 40+ provider integrations | Not our core competency |

### Our Unique Growth Angle

**9Router says:** "Never stop coding — free fallback when you hit limits"
**Condense says:** "Same AI, 40% cheaper — semantic caching + ML routing + compression"

Our hook is **quantified savings with proof**:
```
"We tested 10,000 real queries through Condense:
 • 23% served from semantic cache (zero LLM cost)
 • 31% compressed (fewer tokens billed)
 • 18% routed to cheaper model (same quality, less cost)
 • Total: $47.20 → $28.30 (40% savings)"
```

This is something 9Router can't claim. They route to free/cheap tiers (quality
tradeoffs). We deliver the same quality for less money. That's a fundamentally
different and more defensible value proposition.

---

## Decision Log

| Decision | Rationale | Date |
|----------|-----------|------|
| Don't build subscription proxying | ToS risk, brand damage, building on quicksand | May 2026 |
| Support Mode B as compatibility, not feature | Users can chain Condense → 9Router on their own | May 2026 |
| Target API key users first | Clean, defensible, massive market, no gray area | May 2026 |
| Position as complementary to 9Router | Different jobs, no conflict, good community optics | May 2026 |
| Focus on optimization depth as moat | Semantic cache + ML routing + compression is unique | May 2026 |
