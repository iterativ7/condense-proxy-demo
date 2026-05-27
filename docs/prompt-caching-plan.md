# Prompt Caching — Research & Implementation Plan

> Status: **Planned** — to be implemented in a future sprint.

---

## What's Already in the Proxy

The proxy has a `ProviderCacheStep` (`condense/pipeline/steps/provider_cache_step.py`) that does Anthropic-specific `cache_control` injection:

| Feature | Status |
|---------|--------|
| Injects `cache_control: {type: "ephemeral"}` on system prompts | ✅ |
| Injects `cache_control` on last tool definition | ✅ |
| Provider auto-detection (Anthropic/OpenAI/DeepSeek) | ✅ |
| Savings tracking from provider responses | ❌ Always returns `savings_usd=0.0` |
| Response parsing (`cache_read_input_tokens`) | ❌ |
| Cache hit rate monitoring | ❌ |
| Message reordering for prefix stability | ❌ |
| Token threshold checks (min 1024 for Sonnet) | ❌ |
| Google Gemini support | ❌ |
| OpenAI/DeepSeek optimization & observability | ❌ |

---

## Background: What is Prompt Caching?

Prompt caching is **KV-cache reuse at the provider level** — the provider caches the key-value pairs computed during attention for stable prompt prefixes. Unlike response caching (which stores LLM outputs), prompt caching stores intermediate computation so the model skips re-processing the same input tokens.

**Key properties:**
- The output is **not affected** — responses are generated fresh each time
- Savings come from reduced input token processing cost
- Works on the **prefix** — only the stable beginning of the prompt is cached
- Any change to the prefix invalidates the entire cache

---

## Provider Comparison (as of May 2026)

| Provider | Approach | Cache Write Cost | Cache Read Discount | TTL | Min Tokens |
|----------|----------|-----------------|-------------------|-----|------------|
| **Anthropic** | Explicit `cache_control` markers | 1.25x base | **90% off** | 5m (or 1h at 2x) | 1024 (Sonnet), 4096 (Opus/Haiku) |
| **OpenAI** | Fully automatic (no config needed) | Free | **50% off** | 5–10m | 1024 |
| **Google Gemini** | Implicit (2.5+) or explicit context caching API | Base rate | **90% off** | Configurable (default 1h) | Varies |
| **DeepSeek** | Fully automatic | Normal rate | **90% off** | Hours to days | 64 tokens |

### Important: Anthropic TTL Change (2026)
Anthropic reduced default cache TTL from **60 minutes → 5 minutes** in early 2026, increasing effective costs by 30–60% for many workloads. The 1-hour TTL is still available at 2x write cost.

### Savings Potential
- For workloads with >2,000 token stable prefixes: **50–90% input token cost reduction**
- At scale: tens or hundreds of thousands of dollars per month
- Break-even for Anthropic: only **1.4 cache reads per cache write**

---

## What the Proxy Can Auto-Optimize

As a transparent proxy, Condense is uniquely positioned to auto-optimize prompt caching for users **without any application code changes**:

| Optimization | What | Estimated Savings |
|---|---|---|
| Auto-inject cache breakpoints | Detect system prompts, tools, stable content → inject `cache_control` for Anthropic | 50–90% on input tokens |
| Message reordering | Stable content first (system → tools → static context → history → user msg) for OpenAI/DeepSeek prefix matching | 20–40% more cache hits |
| Savings tracking | Parse `cache_read_input_tokens`, `cached_tokens` from provider responses → actual $ saved shown in dashboard | Observability |
| Token threshold checks | Count tokens before injecting — skip if below minimum to avoid paying 1.25x write cost for no benefit | Avoids wasted write cost |
| Prefix stability detection | Track whether prefix is stable across session requests — warn when dynamic content breaks caching | Debugging |
| Cross-provider normalization | Same config → proxy translates to provider-native format | Developer UX |

---

## Proposed Architecture

### Approach: Enhance `ProviderCacheStep` with Extensible Provider Backends

Refactor the current `ProviderCacheStep` into our **extensible backend registry pattern** (same as routing and compression). One backend per provider, registered via `@provider_cache_registry.register("anthropic")`.

```
ProviderCacheBackend (ABC)
├── available → bool
├── inject_cache_hints(request) → dict      # modify request for caching
├── parse_cache_savings(response) → dict    # extract savings from response
└── min_cacheable_tokens → int              # provider threshold

ProviderCacheRegistry
├── "anthropic"  → AnthropicCacheBackend
├── "openai"     → OpenAICacheBackend
├── "deepseek"   → DeepSeekCacheBackend
└── "gemini"     → GeminiCacheBackend
```

**What each backend does:**

| Backend | Injection | Reordering | Response Parsing |
|---------|-----------|------------|-----------------|
| `anthropic` | `cache_control: {type: "ephemeral"}` on system + tools + stable messages | Yes | `cache_read_input_tokens`, `cache_creation_input_tokens` |
| `openai` | None (automatic) | Reorder stable content first | `prompt_tokens_details.cached_tokens` |
| `deepseek` | None (automatic) | Reorder stable content first | `prompt_cache_hit_tokens`, `prompt_cache_miss_tokens` |
| `gemini` | Translate to Gemini context caching API | Yes | `cached_content_token_count` |

### Planned Config Shape

```yaml
- id: "provider_cache"
  type: "provider_cache"
  enabled: true
  config:
    # Auto-detect provider from model name
    auto_detect_provider: true
    
    # Reorder messages for maximum prefix stability
    reorder_for_prefix_stability: true
    
    # Skip injection if prefix too short (avoids 1.25x write cost)
    enforce_min_token_threshold: true
    
    # Track savings from provider response usage data
    track_savings: true
    
    anthropic:
      inject_cache_control: true
      cache_system_prompt: true
      cache_tools: true
      cache_stable_context: true   # Also cache RAG/few-shot blocks
      ttl: "ephemeral"             # "ephemeral" (5m) or "static" (1h)
    
    openai:
      optimize_prefix_order: true
    
    deepseek:
      optimize_prefix_order: true
    
    gemini:
      use_context_caching_api: false
      ttl_seconds: 3600
```

---

## Implementation Tasks

### Phase 1: Savings Tracking (Low Risk, High Value)
- [ ] Parse provider response usage fields for cache metrics
  - Anthropic: `cache_read_input_tokens`, `cache_creation_input_tokens`
  - OpenAI: `prompt_tokens_details.cached_tokens`
  - DeepSeek: `prompt_cache_hit_tokens`, `prompt_cache_miss_tokens`
- [ ] Calculate actual `savings_usd` and `tokens_saved` from usage data
- [ ] Emit `OptimizationUpdate` with real values (not zeros)
- [ ] Show prompt cache savings in dashboard separately from response cache

### Phase 2: Anthropic Enhancement
- [ ] Add token counting before injection (skip if < 1024/4096)
- [ ] Support `cache_stable_context` — inject `cache_control` on RAG/few-shot blocks
- [ ] Support `ttl: "static"` (1-hour TTL) vs `"ephemeral"` (5-min)
- [ ] Track invalidation rate per session

### Phase 3: OpenAI / DeepSeek Optimization
- [ ] Implement message reordering: stable content first, user message last
- [ ] Parse and track `cached_tokens` from responses
- [ ] Verify prefix stability across requests in a session

### Phase 4: Provider Registry Refactor
- [ ] Create `ProviderCacheBackend` ABC + `provider_cache_registry`
- [ ] Move each provider into its own backend file
- [ ] Follow routing/compression registry pattern for extensibility

### Phase 5: Google Gemini
- [ ] Detect Gemini models
- [ ] Implement explicit context caching API (optional)
- [ ] Parse implicit cache savings from responses

---

## Key Risks & Edge Cases

| Risk | Description | Mitigation |
|------|------------|-----------|
| **Prefix invalidation** | Any byte change in the cached prefix invalidates the whole cache | Track per-session prefix hash; warn on invalidation |
| **Dynamic content in prefix** | Timestamps, session IDs, rotating instructions silently break caching | Detect and warn; offer to strip known dynamic patterns |
| **TTL expiry** | 5-min Anthropic TTL is very short for low-traffic use cases | Optional cache warming; TTL observability |
| **Write overhead without reads** | Paying 1.25x per write but getting 0 cache hits | Token threshold check; min-request-count before enabling |
| **Tool schema non-determinism** | JSON serializer order changes invalidate tool cache | Normalize tool JSON before hashing |
| **Security** | Provider caches are shared globally; timing attacks possible | Document risk; consider disabling for sensitive workloads |

---

## Open-Source Reference Implementations

| Tool | What to Borrow |
|------|----------------|
| **LiteLLM** | `cache_control_injection_points` auto-injection pattern; cross-provider translation |
| **prompt-caching.ai** | `analyze_cacheability` dry-run; `get_cache_stats` per-session tracking |
| **OpenRouter** | Sticky provider routing to maximize cache hit continuity |
| **vLLM** | PagedAttention prefix caching (relevant for self-hosted Ollama integration) |

---

## References

- [Anthropic Prompt Caching Docs](https://docs.anthropic.com/en/docs/build-with-claude/prompt-caching)
- [OpenAI Prompt Caching Docs](https://platform.openai.com/docs/guides/prompt-caching)
- [Google Gemini Caching](https://ai.google.dev/gemini-api/docs/caching)
- [DeepSeek Context Caching](https://api-docs.deepseek.com/guides/kv_cache)
- [LiteLLM Prompt Caching](https://docs.litellm.ai/docs/completion/prompt_caching)
