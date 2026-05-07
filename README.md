# Condense

**LLM cost optimization proxy** — reduce API costs 50-80% through caching, provider cache injection, model routing, and budget enforcement.

```
App → AI Gateway (auth, rate-limit, retry) → CONDENSE PROXY (optimize) → LLM Provider
```

Condense sits behind your AI gateway and optimizes every LLM request transparently. No code changes to your app — just point the gateway's upstream to Condense.

## Quick Start

```bash
# Install
pip install -e .

# Generate config
condense init --preset conservative

# Edit condense.yaml to set your upstream URL
# Start proxy
condense start

# Send a test request
curl http://localhost:8080/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model": "gpt-4o", "messages": [{"role": "user", "content": "Hello"}]}'

# Check health
curl http://localhost:8080/health

# Check metrics
curl http://localhost:8080/metrics
```

## Features

### 4 Optimization Techniques

1. **Exact-match caching** — Hash request params, return cached response on match
2. **Provider prompt cache injection** — Auto-inject `cache_control` for Anthropic, optimize prefix for OpenAI
3. **Rule-based model routing** — Route simple requests to cheaper models based on configurable rules
4. **Session-level budget enforcement** — Per-session cost caps, turn limits, loop detection

### Infrastructure

- **Config-driven** — Enable/disable techniques via `condense.yaml`, not code changes
- **Pipeline architecture** — Extensible sequential pipeline, new technique = 1 step file
- **Failsafe first** — Never block a valid request due to optimization failure
- **Tenant isolation** — Cache keys include API key hash
- **Prometheus metrics** — `/metrics` endpoint with cache hit rates, savings, latency
- **Response headers** — `X-Condense-*` headers report what was optimized

## Configuration

```yaml
upstream:
  url: "https://api.openai.com/v1"
  timeout_seconds: 300

optimizations:
  cache:
    enabled: true
    exact:
      backend: "memory"    # or "redis"
      max_size: 10000
      ttl_seconds: 3600

  provider_cache:
    enabled: true

  routing:
    enabled: false
    rules:
      - condition: "short_messages"
        max_chars: 500
        model: "gpt-4o-mini"

  budget:
    enabled: true
    max_session_cost_usd: 10.0
    max_turns_per_session: 100
```

### Presets

Generate config from a preset: `condense init --preset <name>`

| Preset | Description |
|---|---|
| `conservative` | Safe defaults, caching + provider cache only |
| `aggressive` | Max savings, all optimizations enabled |
| `agent` | For agentic workloads with tool use |
| `rag` | For RAG pipelines, heavy caching |
| `chat` | For conversational chat apps |

## Docker

```bash
# With Redis (production)
docker compose up -d

# Without Redis (dev/testing)
docker compose -f docker-compose.minimal.yml up -d
```

## Development

```bash
# Install dev dependencies
poetry install

# Run tests
make test

# Lint
make lint

# Run locally
make run
```

## Architecture

```
Request → FastAPI → Pipeline:
  1. CacheStep (exact match lookup)
  2. ProviderCacheStep (inject cache_control)
  3. RoutingStep (swap to cheaper model)
  4. BudgetStep (check session limits)
  5. ForwardStep (POST to upstream)
→ Response + X-Condense-* headers
```

## License

Business Source License 1.1 — see [LICENSE](LICENSE).
