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
# Install dependencies
poetry install

# Start proxy (explicit config is safest)
condense start --config condense.default.yaml

# Health checks
curl http://localhost:8080/health
curl http://localhost:8080/health/ready
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

## Local Ollama Example

```yaml
upstream:
  url: "http://localhost:11434"
  timeout_seconds: 300
```

Then send:

```bash
curl http://localhost:8080/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model":"ollama/gemma3:4b",
    "messages":[{"role":"user","content":"Say hello in one sentence."}],
    "temperature":0
  }'
```

## API Reference (Request/Response)

Primary endpoint:

- `POST /v1/chat/completions`

This route is OpenAI-compatible and accepts standard chat-completions payloads.

Example request:

```bash
curl http://localhost:8080/v1/chat/completions \
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
