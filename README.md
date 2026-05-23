# Condense

Condense is an LLM cost-optimization proxy that sits between your gateway/app and model provider.

For local setup and deterministic startup flow, see `DEVELOPMENT.md`.

```
App -> Gateway -> Condense Proxy -> Model Provider
```

## What It Does

- Exact-match caching
- Provider prompt-cache injection (Anthropic/OpenAI/DeepSeek-aware)
- Rule-based model routing
- Session budget enforcement
- Request/latency/savings metrics
- `X-Condense-*` response headers for transparency

## Key Architecture (Current)

- Canonical optimization declarations in config (`optimizations[]` entries)
- Dependency-aware DAG scheduler (topological batches)
- Two-phase step contract:
  - `forward()` (main execution)
  - `backward()` (reverse-order post-processing hooks)
- Forwarding uses LiteLLM SDK (`litellm.acompletion`) rather than raw upstream HTTP calls

## Quick Start

```bash
# Start local proxy (includes .venv setup, uses condense.local.yaml -> 127.0.0.1:8090)
make start-local

# Health checks
curl http://127.0.0.1:8090/health
curl http://127.0.0.1:8090/health/ready

# Verify Ollama is up and model is available
curl http://127.0.0.1:11434/api/tags

# (Optional) Build modular UI bundle for /_ui route
make ui-build
```

Stop local proxy:

```bash
make stop-local
```

Config resolution order when `--config` is not provided:

1. `CONDENSE_CONFIG` environment variable
2. `./condense.yaml`
3. `./condense.default.yaml`

If you specifically want to run with `condense.default.yaml`, use `--config condense.default.yaml`.

## Config Shape

`condense.yaml` uses canonical optimization entries:

```yaml
upstream:
  url: "https://api.openai.com/v1"
  timeout_seconds: 300
  # api_key_env: "OPENAI_API_KEY"

optimizations:
  - id: "exact_cache"
    type: "cache"
    enabled: true
    stage: "both"        # both | forward | backward
    config:
      exact:
        backend: "memory" # memory | redis
        max_size: 10000
        ttl_seconds: 3600
      non_deterministic: "skip"

  - id: "provider_cache"
    type: "provider_cache"
    enabled: true
    depends_on: ["exact_cache"]
    config: {}

  - id: "routing"
    type: "routing"
    enabled: false
    depends_on: ["provider_cache"]
    config:
      rules:
        - condition: "short_messages"
          max_chars: 500
          model: "gpt-4o-mini"

  - id: "budget"
    type: "budget"
    enabled: true
    # depends_on allowed even if referenced step is disabled
    config:
      max_session_cost_usd: 10.0
      max_turns_per_session: 100
      loop_detection_window: 5
```

Validation behavior:
- duplicate optimization ids fail
- unknown dependency ids fail
- dependency cycles across enabled entries fail
- dependencies to disabled entries are allowed and ignored at runtime

## Runtime Resource Behavior

Resource startup is optimization-aware:

- cache backend initializes only when `cache` optimization is enabled
- session store initializes only when `budget` optimization is enabled
- pipeline construction enforces required resources for enabled optimization types

## LiteLLM SDK Forwarding

`ForwardStep` uses LiteLLM SDK (`litellm.acompletion`) and supports:

- `upstream.url` as `api_base` (OpenAI-compatible providers, Ollama OpenAI surface, etc.)
- API key from incoming `Authorization` header when present
- fallback to `upstream.api_key_env` when configured

## Optimization Update Contract

Each optimization step can emit structured update payloads through the pipeline.
For backward compatibility, legacy step behavior still works, but the normalized
contract now requires each emitted update to include at least one of:

- `savings_usd`
- `tokens_saved`

These updates are aggregated for the modular UI and surfaced via
`/metrics/summary/v2` as per-optimization contributions.

## Local Ollama Example

```yaml
upstream:
  url: "http://localhost:11434"
  timeout_seconds: 300
```

Then send:

```bash
curl http://127.0.0.1:8090/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model":"ollama/gemma3:4b",
    "messages":[{"role":"user","content":"Say hello in one sentence."}],
    "temperature":0
  }'
```

If the request fails with an upstream connection error, verify local Ollama first:

```bash
curl http://127.0.0.1:11434/api/tags
```

## API Reference (Request/Response)

Primary endpoint:

- `POST /v1/chat/completions`

This route is OpenAI-compatible and accepts standard chat-completions payloads.

Example request:

```bash
curl http://127.0.0.1:8090/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model":"ollama/gemma3:4b",
    "messages":[{"role":"user","content":"Explain cache hit in one line."}],
    "temperature":0
  }'
```

Expected success response (`200`):

```json
{
  "id": "chatcmpl-...",
  "object": "chat.completion",
  "choices": [
    {
      "index": 0,
      "message": {
        "role": "assistant",
        "content": "..."
      },
      "finish_reason": "stop"
    }
  ],
  "usage": {
    "prompt_tokens": 0,
    "completion_tokens": 0,
    "total_tokens": 0
  }
}
```

Condense response headers (when `headers.add_savings_headers: true`):

- `X-Condense-Cache-Hit`: `true`/`false`
- `X-Condense-Cache-Type`: cache strategy used (`none` if miss)
- `X-Condense-Original-Model`: model from incoming request
- `X-Condense-Routed-Model`: actual model used after routing
- `X-Condense-Techniques`: applied optimization ids (`none` when none applied)
- `X-Condense-Savings-USD`: estimated savings for the request
- `X-Condense-Session-ID`: present when a session is detected
- `X-Condense-Session-Turn`: present when a session is detected

Common error responses:

- `400` invalid JSON:
  - `{"error":{"message":"Invalid JSON: ...","type":"invalid_request_error"}}`
- `429` budget/session rejection:
  - `{"error":{"message":"...","type":"condense_error"}}`
- `502` upstream proxy failure (failsafe path):
  - `{"error":{"message":"...","type":"proxy_error"}}`

Savings and dashboard endpoints:

- `GET /metrics`:
  - Prometheus-format metrics output for monitoring systems.
- `GET /metrics/summary`:
  - Structured JSON summary intended for dashboards/UI.
  - Includes:
    - totals (`total_savings_usd`, `total_tokens_saved_estimate`, request/cache counters, token counters)
    - rates (`cache_hit_rate`, `avg_savings_per_request_usd`)
    - `uptime_seconds`
- `GET /dashboard`:
  - Built-in lightweight HTML dashboard with live KPI cards.
  - Auto-refreshes by polling `/metrics/summary` every 5 seconds.
- `GET /metrics/summary/v2`:
  - UI-focused payload for modular savings UI.
  - Includes:
    - `overall` consolidated savings values
    - `enabled_tabs` from enabled optimizations
    - `optimizations[]` per-optimization contributions/details
- `GET /_ui`:
  - Separate modular UI module (when built assets are present).
  - Use `make ui-build` before loading locally.

Quick check:

```bash
curl http://127.0.0.1:8090/metrics/summary
open http://127.0.0.1:8090/dashboard
curl http://127.0.0.1:8090/metrics/summary/v2
open http://127.0.0.1:8090/_ui
```

## Docker

```bash
# Minimal stack (no Redis)
docker compose -f docker-compose.minimal.yml up -d --build

# Full stack
docker compose up -d --build
```

The Docker healthcheck now uses Python stdlib (no curl dependency required in image).

## Tests

Prerequisites for Docker + Ollama integration tests:

- Docker Desktop/Engine installed, daemon running, and CLI access working (`docker info`)
- Internet access at least once (to pull `ollama/ollama` image and model if missing)
- A valid Condense config file available (default prompt value: `condense.local.yaml`)
- Port `11434` available for Ollama and port `8080` available for Condense test container

```bash
# 1) Docker setup for integration tests.
# Prompts for config YAML, starts/checks Ollama, and ensures model (default: gemma3:4b).
make docker-prep-integration

# 2) Regression gate: run all tests (unit + integration).
make test

# Focused suites
pytest tests/test_config tests/test_pipeline tests/test_server -q

# Docker + Ollama integration test
pytest tests/test_server/test_docker_ollama_integration.py -q
```

## License

Business Source License 1.1 — see `LICENSE`.
