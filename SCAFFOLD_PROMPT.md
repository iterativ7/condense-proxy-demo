# Condense Proxy — Scaffold & Implementation Prompt

**How to use this file:**

```bash
# Option A: Copy this file into your new project folder
mkdir ~/personal-projects/condense && cd ~/personal-projects/condense
cp /Users/agupta51/personal-projects/litellm/research-reports/condense_scaffold_prompt.md ./SCAFFOLD_PROMPT.md
# Then in Rovo Dev: "Read SCAFFOLD_PROMPT.md and scaffold the project. Start with steps 1-6."

# Option B: Paste the contents of this file directly into a new Rovo Dev session

# Option C: Reference this file by absolute path in the new session
# "Read /Users/agupta51/personal-projects/litellm/research-reports/condense_scaffold_prompt.md
#  and scaffold the entire project in this empty folder. Start with steps 1-6."
```

---

## What You're Building

**Condense** is an open-source LLM cost optimization proxy. It sits behind an AI gateway (like LiteLLM, Portkey, or any OpenAI-compatible gateway) and reduces LLM API costs by 50-80% through config-driven caching, provider cache injection, model routing, and budget enforcement.

```
App → AI Gateway (auth, rate-limit, retry) → CONDENSE PROXY (optimize) → LLM Provider
```

The customer installs Condense, writes a `condense.yaml` config file, and all optimizations are applied transparently. No code changes to their app — just point the gateway's upstream to Condense.

---

## Architecture Decisions (Already Made)

| Decision | Choice | Rationale |
|---|---|---|
| Language | Python 3.12 | Team expertise, same ecosystem as LiteLLM |
| Framework | FastAPI + uvicorn | Async I/O, native OpenAPI, production-proven for proxies |
| HTTP client | httpx (async) | Connection pooling, streaming, timeout handling |
| Pipeline pattern | Sequential Pipeline + FastAPI DI hybrid | Explicit ordering, short-circuiting (cache hits), testable, config-driven |
| Config | YAML (condense.yaml) + Pydantic validation | Version-controllable, auditable, hot-reloadable |
| Cache (V1 default) | In-memory (LRU + TTL) | Zero external deps for dev. Redis for production |
| Cache (production) | Redis (exact match + vector search for semantic) | Shared across instances, persistent |
| Deployment | Docker image + docker-compose | Self-hosted, any cloud |
| Package manager | Poetry | Standard for modern Python projects |
| Position | Behind AI gateway | Inherits auth, rate limits, retries, guardrails from gateway |

---

## V1 Scope — What to Build

### 4 Optimization Techniques

1. **Exact-match caching** — Hash request params, return cached response on match
2. **Provider prompt cache injection** — Auto-inject `cache_control` for Anthropic, optimize prefix for OpenAI
3. **Rule-based model routing** — Route simple requests to cheaper models based on configurable rules
4. **Session-level budget enforcement** — Per-session cost caps, turn limits, loop detection

### Supporting Infrastructure

5. **Config system** — Load/validate `condense.yaml`, hot-reload on file change
6. **CLI** — `condense init` (generate config from preset), `condense start`, `condense status`
7. **Metrics** — Prometheus-compatible `/metrics` endpoint, `/health`, `/health/ready`
8. **Response headers** — `X-Condense-*` headers on every response reporting savings
9. **Session detection** — Auto-detect conversation sessions from message prefix patterns
10. **Failsafe** — Circuit breaker, passthrough on error (never block a valid request)

### NOT in V1

- Semantic caching (V1.1 — needs embeddings, more complexity)
- Context compression (V2 — needs LLM call for summarization)
- Tool pruning (V2 — needs session history tracking)
- Streaming support (V1.1 — important but can fast-follow)
- Dashboard/UI (V2+)
- Framework plugins (V3)

---

## Project Structure — Create These Files

```
condense/
├── __init__.py                        # Version, package metadata
├── __main__.py                        # Entry: python -m condense
├── cli.py                             # CLI commands (init, start, status)
│
├── config/
│   ├── __init__.py
│   ├── schema.py                      # Pydantic models for condense.yaml
│   ├── loader.py                      # YAML loading + validation + hot-reload
│   └── presets/
│       ├── agent.yaml                 # Preset: agent workloads
│       ├── rag.yaml                   # Preset: RAG workloads
│       ├── chat.yaml                  # Preset: simple chat
│       ├── conservative.yaml          # Preset: safe defaults, minimal optimization
│       └── aggressive.yaml            # Preset: max savings, slightly more risk
│
├── server/
│   ├── __init__.py
│   ├── app.py                         # FastAPI app factory
│   ├── routes.py                      # POST /v1/chat/completions + proxy other endpoints
│   ├── dependencies.py                # Depends() functions (session, config, namespace)
│   └── middleware.py                  # ASGI middleware (request timing, metrics)
│
├── pipeline/
│   ├── __init__.py                    # build_pipeline() factory
│   ├── context.py                     # PipelineContext dataclass
│   ├── executor.py                    # PipelineExecutor (runs steps sequentially)
│   ├── result.py                      # StepResult dataclass
│   └── steps/
│       ├── __init__.py
│       ├── base.py                    # BaseStep ABC
│       ├── cache_step.py              # Exact-match cache lookup/store
│       ├── provider_cache_step.py     # Inject cache_control headers
│       ├── routing_step.py            # Rule-based model routing
│       ├── budget_step.py             # Budget caps + loop detection
│       └── forward_step.py            # httpx POST to upstream
│
├── cache/
│   ├── __init__.py
│   ├── base.py                        # CacheBackend ABC
│   ├── memory.py                      # InMemoryCache (LRU + TTL, OrderedDict)
│   ├── redis_backend.py               # RedisCache (exact match via Redis)
│   └── key.py                         # Cache key computation (SHA-256 hash)
│
├── session/
│   ├── __init__.py
│   ├── detector.py                    # Auto-detect sessions from message prefix
│   └── store.py                       # Session state storage (memory / Redis)
│
├── routing/
│   ├── __init__.py
│   └── rules.py                       # Rule engine for model routing
│
├── metrics/
│   ├── __init__.py
│   ├── tracker.py                     # Per-request savings tracking + aggregation
│   └── prometheus.py                  # Prometheus text format export
│
├── upstream/
│   ├── __init__.py
│   ├── client.py                      # httpx async client pool + connection management
│   └── provider_detect.py             # Detect provider from model name
│
└── utils/
    ├── __init__.py
    ├── hashing.py                     # SHA-256 cache key hashing
    └── tokens.py                      # Token counting (tiktoken or char-based estimate)

tests/
├── __init__.py
├── conftest.py                        # Shared fixtures (mock config, mock upstream)
├── test_pipeline/
│   ├── test_executor.py               # Pipeline execution, short-circuit, error handling
│   ├── test_cache_step.py
│   ├── test_provider_cache_step.py
│   ├── test_routing_step.py
│   ├── test_budget_step.py
│   └── test_forward_step.py
├── test_cache/
│   ├── test_memory.py
│   ├── test_key.py
│   └── test_redis_backend.py
├── test_config/
│   ├── test_schema.py
│   └── test_loader.py
├── test_session/
│   └── test_detector.py
└── test_server/
    ├── test_routes.py                 # Integration tests (FastAPI TestClient)
    └── test_health.py

# Root files
pyproject.toml                         # Poetry project config
condense.default.yaml                  # Default config (shipped with package)
Dockerfile
docker-compose.yml
docker-compose.minimal.yml             # Without Redis (dev/testing only)
README.md
LICENSE                                # BSL or AGPL (TBD)
.gitignore
Makefile                               # Common commands (test, lint, build, docker)
```

---

## Core Interfaces — Implement EXACTLY These Contracts

### 1. BaseStep (pipeline/steps/base.py)

```python
from abc import ABC, abstractmethod
from condense.pipeline.context import PipelineContext
from condense.pipeline.result import StepResult


class BaseStep(ABC):
    """Base class for all optimization pipeline steps."""

    def __init__(self, config: dict):
        self.config = config
        self.enabled = config.get("enabled", True)

    @abstractmethod
    async def execute(self, ctx: PipelineContext) -> StepResult:
        """Execute this optimization step.

        Returns:
            StepResult with action:
              - "next" → continue to next step
              - "short_circuit" → return response immediately (cache hit)
              - "reject" → return error (budget exceeded)
        """
        pass

    def is_enabled(self) -> bool:
        return self.enabled
```

### 2. PipelineContext (pipeline/context.py)

```python
from dataclasses import dataclass, field
from typing import Optional, Dict, Any


@dataclass
class PipelineContext:
    """Shared state passed through all pipeline steps."""

    # Original request (immutable — for failsafe passthrough)
    original_request: dict

    # Working request (steps modify this)
    request: dict

    # Config
    config: Any  # CondenseConfig

    # Session info
    session_id: Optional[str] = None
    session_turn: int = 0

    # Cache namespace (API key hash for tenant isolation)
    cache_namespace: str = ""

    # Tracking (accumulated by steps)
    original_model: Optional[str] = None
    routed_model: Optional[str] = None
    original_tokens: int = 0
    optimized_tokens: int = 0
    cache_hit: bool = False
    cache_hit_type: Optional[str] = None  # "exact" | "semantic"
    techniques_applied: list = field(default_factory=list)
    total_savings_usd: float = 0.0

    # Metadata
    metadata: Dict[str, Any] = field(default_factory=dict)
```

### 3. StepResult (pipeline/result.py)

```python
from dataclasses import dataclass
from typing import Optional, Any


@dataclass
class StepResult:
    action: str  # "next" | "short_circuit" | "reject"
    response: Optional[Any] = None  # For short_circuit
    error: Optional[str] = None  # For reject
    status_code: int = 200
    technique: Optional[str] = None  # Which technique acted
    savings_usd: float = 0.0
```

### 4. PipelineExecutor (pipeline/executor.py)

```python
import logging
from typing import List
from condense.pipeline.steps.base import BaseStep
from condense.pipeline.context import PipelineContext
from condense.pipeline.result import StepResult

logger = logging.getLogger(__name__)


class PipelineExecutor:
    def __init__(self, steps: List[BaseStep]):
        self.steps = [s for s in steps if s.is_enabled()]

    async def execute(self, ctx: PipelineContext) -> StepResult:
        for step in self.steps:
            try:
                result = await step.execute(ctx)

                if result.action == "short_circuit":
                    ctx.cache_hit = True
                    if result.technique:
                        ctx.techniques_applied.append(result.technique)
                    ctx.total_savings_usd += result.savings_usd
                    return result

                if result.action == "reject":
                    return result

                # action == "next": accumulate and continue
                if result.technique:
                    ctx.techniques_applied.append(result.technique)
                ctx.total_savings_usd += result.savings_usd

            except Exception as e:
                # FAILSAFE: skip broken step, never block a request
                logger.error(f"Step {step.__class__.__name__} failed: {e}", exc_info=True)
                continue

        # All steps passed — should have been handled by ForwardStep
        # If we get here, something is wrong (ForwardStep missing?)
        return StepResult(action="reject", error="Pipeline completed without forwarding", status_code=500)
```

### 5. CacheBackend ABC (cache/base.py)

```python
from abc import ABC, abstractmethod
from typing import Optional


class CacheBackend(ABC):
    @abstractmethod
    async def get(self, key: str) -> Optional[dict]:
        pass

    @abstractmethod
    async def set(self, key: str, value: dict, ttl: Optional[int] = None) -> None:
        pass

    @abstractmethod
    async def delete(self, key: str) -> None:
        pass

    @abstractmethod
    async def size(self) -> int:
        pass

    @abstractmethod
    async def clear(self) -> None:
        pass
```

### 6. build_pipeline() Factory (pipeline/__init__.py)

```python
from condense.pipeline.executor import PipelineExecutor
from condense.pipeline.steps.cache_step import CacheStep
from condense.pipeline.steps.provider_cache_step import ProviderCacheStep
from condense.pipeline.steps.routing_step import RoutingStep
from condense.pipeline.steps.budget_step import BudgetStep
from condense.pipeline.steps.forward_step import ForwardStep


def build_pipeline(config, cache_backend, session_store, http_client) -> PipelineExecutor:
    """Build optimization pipeline from config. Only enabled steps are included."""
    steps = []
    opt = config.optimizations

    # Order: Cache (short-circuit) → Provider cache → Route → Budget → Forward
    if opt.cache.enabled:
        steps.append(CacheStep(opt.cache.model_dump(), cache_backend))

    if opt.provider_cache.enabled:
        steps.append(ProviderCacheStep(opt.provider_cache.model_dump()))

    if opt.routing.enabled:
        steps.append(RoutingStep(opt.routing.model_dump()))

    if opt.budget.enabled:
        steps.append(BudgetStep(opt.budget.model_dump(), session_store))

    # Always last
    steps.append(ForwardStep(config.upstream.model_dump(), http_client))

    return PipelineExecutor(steps)
```

---

## Config Schema (condense.yaml)

Implement this EXACT schema as Pydantic models in `config/schema.py`:

```yaml
# condense.yaml — V1 schema

upstream:
  url: "https://api.openai.com/v1"     # Required. Where to forward requests.
  timeout_seconds: 300                   # Default 300
  api_key_env: "OPENAI_API_KEY"         # Optional. Env var to inject as Bearer token.

deployment:
  mode: "behind-gateway"                 # "behind-gateway" | "standalone"
  host: "0.0.0.0"
  port: 8080

optimizations:
  cache:
    enabled: true
    exact:
      enabled: true
      backend: "memory"                  # "memory" | "redis"
      max_size: 10000
      ttl_seconds: 3600
    non_deterministic: "skip"            # "skip" | "allow" | "normalize"

  provider_cache:
    enabled: true
    anthropic:
      inject_cache_control: true
      cache_system_prompt: true
      cache_tools: true
    openai:
      enabled: true
    deepseek:
      enabled: true

  routing:
    enabled: false
    rules:
      - condition: "short_messages"
        max_chars: 500
        model: "gpt-4o-mini"
      - condition: "no_tools"
        model: "gpt-4o-mini"

  budget:
    enabled: true
    max_session_cost_usd: 10.0
    max_turns_per_session: 100
    loop_detection_window: 5

redis:
  enabled: false
  url: "redis://localhost:6379"
  password_env: "REDIS_PASSWORD"

metrics:
  enabled: true
  endpoint: "/metrics"

headers:
  add_savings_headers: true

failsafe:
  on_error: "passthrough"
  circuit_breaker:
    threshold: 5
    recovery_seconds: 30
```

---

## Key Implementation Details

### Cache Key Computation

```python
# cache/key.py
import hashlib
import json

CACHE_KEY_PARAMS = [
    "model", "messages", "tools", "tool_choice",
    "temperature", "top_p", "max_tokens", "stop",
    "response_format", "seed",
]

def compute_cache_key(request: dict, namespace: str = "") -> str:
    key_parts = []
    for param in sorted(CACHE_KEY_PARAMS):
        if param in request and request[param] is not None:
            value = request[param]
            if isinstance(value, (dict, list)):
                value = json.dumps(value, sort_keys=True, default=str)
            key_parts.append(f"{param}:{value}")

    raw_key = "|".join(key_parts)
    hashed = hashlib.sha256(raw_key.encode()).hexdigest()

    if namespace:
        return f"{namespace}:{hashed}"
    return hashed
```

### Non-Deterministic Handling

- `"skip"` (default): Don't cache if temperature > 0
- `"allow"`: Cache everything (user accepts non-identical responses)
- `"normalize"`: Cache but exclude temperature from key

### Provider Detection

```python
# upstream/provider_detect.py
def detect_provider(model: str) -> str:
    model_lower = model.lower()
    if any(x in model_lower for x in ["claude", "anthropic"]):
        return "anthropic"
    if any(x in model_lower for x in ["gpt", "o1", "o3", "chatgpt"]):
        return "openai"
    if any(x in model_lower for x in ["deepseek"]):
        return "deepseek"
    if any(x in model_lower for x in ["gemini", "palm"]):
        return "google"
    return "unknown"
```

### Anthropic Cache Control Injection

For Anthropic models, auto-inject `cache_control: {"type": "ephemeral"}` on:
- System prompt message (always)
- Last tool definition (if tools present)
- This tells Anthropic to cache the prefix up to that point (90% savings on repeated tokens)

### Session Detection

Auto-detect sessions by hashing: `SHA-256(api_key_hash + system_prompt[:200] + first_user_msg[:200])`
This groups requests from the same conversation into a session without requiring explicit session IDs.

### Response Headers

Every response from Condense includes:
```
X-Condense-Cache-Hit: true|false
X-Condense-Cache-Type: exact|semantic|none
X-Condense-Original-Model: gpt-4o
X-Condense-Routed-Model: gpt-4o-mini
X-Condense-Techniques: provider_cache,routing
X-Condense-Savings-USD: 0.0234
X-Condense-Session-ID: a1b2c3d4
X-Condense-Session-Turn: 15
```

### Failsafe Rules

1. **Every pipeline step is wrapped in try/except** — if a step crashes, skip it, don't block the request
2. **Circuit breaker** — after N failures, bypass optimization entirely (passthrough mode)
3. **ForwardStep passes through provider errors** — 4xx/5xx from provider returned as-is
4. **NEVER make a request fail that would have succeeded without Condense**

---

## Request Lifecycle (The Full Flow)

```
1. Request arrives at POST /v1/chat/completions
2. FastAPI Depends():
   a. load_config() → cached CondenseConfig (hot-reloadable)
   b. get_cache_namespace() → SHA-256(api_key)[:16]
   c. detect_session() → (session_id, turn_number)
3. Build PipelineContext with request + config + session + namespace
4. If circuit breaker is OPEN → skip pipeline, forward directly
5. PipelineExecutor.execute(ctx):
   a. CacheStep: compute key, check cache → HIT? return cached response
   b. ProviderCacheStep: detect provider, inject cache_control headers
   c. RoutingStep: evaluate rules, swap model if matched
   d. BudgetStep: check session cost/turns, detect loops → OVER? reject 429
   e. ForwardStep: POST to upstream, return response
6. Post-pipeline (background tasks):
   a. Store response in cache (if cacheable)
   b. Update session state (cost, turn count, request hash)
   c. Update metrics (savings, cache hits, latency)
7. Add X-Condense-* response headers
8. Return response to caller
```

---

## CLI Commands

### `condense init [--preset PRESET]`
- Generates `condense.yaml` from a preset template
- Presets: `agent`, `rag`, `chat`, `conservative`, `aggressive`
- If no preset specified, use interactive prompts

### `condense start [--config PATH] [--port PORT]`
- Loads config, starts FastAPI server with uvicorn
- Starts file watcher for hot-reload

### `condense status`
- Shows: uptime, total requests, savings, cache hit rate, circuit breaker state
- Reads from running instance's `/health` endpoint

---

## Docker

### Dockerfile
- Base: `python:3.12-slim`
- Install via Poetry (production deps only)
- Copy condense/ source + default config
- Expose 8080
- HEALTHCHECK via curl to /health
- ENTRYPOINT: `python -m condense start`

### docker-compose.yml
- Condense service (port 8080, volume mount for condense.yaml, env vars for API keys)
- Redis service (redis:7-alpine, for production cache)
- docker-compose.minimal.yml — without Redis, for dev/testing

---

## Testing Strategy

- Use pytest + pytest-asyncio
- Mock upstream with httpx mock or respx
- Test each pipeline step independently (unit tests)
- Test full pipeline with TestClient (integration tests)
- Test config validation (valid/invalid YAML → Pydantic errors)
- Test cache key determinism (same request → same key)
- Test cache tenant isolation (different API keys → different namespaces)
- Test failsafe (step crashes → request still succeeds)
- Test circuit breaker (N failures → bypass mode)

---

## What Success Looks Like

After scaffolding, you should be able to:

```bash
# Install
pip install -e .

# Generate config
condense init --preset conservative

# Start proxy
condense start

# Send a test request (passthrough mode works)
curl http://localhost:8080/v1/chat/completions \
  -H "Authorization: Bearer sk-xxx" \
  -H "Content-Type: application/json" \
  -d '{"model": "gpt-4o", "messages": [{"role": "user", "content": "Hello"}]}'

# Check health
curl http://localhost:8080/health

# Check metrics
curl http://localhost:8080/metrics

# Send same request again → cache hit
# Response should have: X-Condense-Cache-Hit: true
```

---

## Important Design Principles

1. **Config drives everything** — enable/disable techniques via YAML, not code changes
2. **Pipeline is extensible** — new technique = 1 step file + 1 config block + 1 line in build_pipeline()
3. **Failsafe first** — never block a valid request due to optimization failure
4. **Tenant isolation** — cache keys include API key hash, different keys = different cache partitions
5. **Gateway-agnostic** — works behind LiteLLM, Portkey, Kong, or standalone
6. **Transparent** — X-Condense-* headers report exactly what was optimized and how much was saved
7. **Don't duplicate the gateway** — no auth, no rate limiting, no retry logic (gateway does that)

---

## Start Here

1. Create the project with `poetry init` (Python 3.12, FastAPI, httpx, pyyaml, pydantic, uvicorn, click)
2. Create the directory structure (all files listed above)
3. Implement the core interfaces first (BaseStep, PipelineContext, StepResult, PipelineExecutor, CacheBackend)
4. Implement ForwardStep (passthrough proxy — the simplest end-to-end test)
5. Implement the FastAPI server + routes (POST /v1/chat/completions)
6. Verify: request in → response out (passthrough mode)
7. Add CacheStep (exact match)
8. Add ProviderCacheStep
9. Add RoutingStep
10. Add BudgetStep
11. Add CLI (init, start, status)
12. Add Dockerfile + docker-compose
13. Write tests for each component
