# Condense Proxy Scaffold Prompt (Updated)

Use this document when scaffolding or refactoring Condense Proxy so generated code matches the current implementation.

## Product Goal

Condense is an optimization proxy for chat-completion traffic:

```
App -> Gateway -> Condense -> Model Provider
```

It applies configurable optimization steps while preserving request success semantics.

## Current Core Decisions

- Python 3.12, FastAPI, async pipeline
- Config-first behavior via `condense.yaml`
- Canonical optimization entries (`optimizations[]`) instead of nested `optimizations.cache/routing/...` blocks
- Dependency-aware DAG scheduling
- Two-phase step API:
  - `forward(ctx) -> StepResult`
  - `backward(ctx, result) -> None`
- Forwarding via LiteLLM SDK (`litellm.acompletion`)
- Resource startup tied to enabled optimizations:
  - cache backend only when cache optimization is enabled
  - session store only when budget optimization is enabled

## Canonical Config Contract

Implement and preserve this shape:

```yaml
upstream:
  url: "https://api.openai.com/v1"
  timeout_seconds: 300
  api_key_env: "OPENAI_API_KEY" # optional

optimizations:
  - id: "exact_cache"
    type: "cache"            # cache | provider_cache | routing | budget
    enabled: true
    stage: "both"            # both | forward | backward
    depends_on: []
    parallelizable: null
    config: {}
```

Validation rules:

- Duplicate optimization ids: invalid
- Unknown dependency ids: invalid
- Dependency cycles among enabled entries: invalid
- Dependency references to disabled entries: allowed (ignored at runtime)

## Pipeline Contracts

### BaseStep (`pipeline/steps/base.py`)

Required members:

- metadata fields: `name`, `optimization_id`, `supports_parallel`, `can_short_circuit`, `reads`, `writes`, `execution_stage`
- `depends_on` tuple
- methods:
  - `execute(ctx)` (legacy compatibility)
  - `forward(ctx)` defaults to `execute(ctx)`
  - `backward(ctx, result)` default no-op
  - `runs_forward()`
  - `runs_backward()`

### StepResult (`pipeline/result.py`)

- `action`: `"next" | "short_circuit" | "reject"`
- `response`, `error`, `status_code`, `technique`, `savings_usd`

### PipelineExecutor (`pipeline/executor.py`)

Must provide:

- DAG planning with topological batches
- hazard-aware parallel batching using step read/write surfaces
- forward phase execution by batch
- short-circuit/reject exit behavior
- backward phase execution in reverse applied order
- failsafe behavior (step failures do not break otherwise-valid requests)

## Forwarding Requirements (LiteLLM SDK)

`ForwardStep` must:

- call `litellm.acompletion(...)` with:
  - `model`
  - `messages`
  - `api_base` from `upstream.url`
  - extra request params passthrough
- resolve API key from:
  1. incoming `Authorization` header bearer token
  2. `upstream.api_key_env` fallback
- convert LiteLLM response to dict
- append `_condense_estimated_cost`
- populate `ctx.metadata["estimated_cost"]`
- map provider/status failures to `StepResult` without crashing pipeline

## Resource Initialization Rules (`server/app.py`)

At startup:

- always initialize config, HTTP client, metrics, circuit breaker
- initialize cache backend only if cache optimization is enabled
- initialize session store only if budget optimization is enabled

At shutdown:

- close HTTP client
- close Redis client only when cache backend is Redis-backed

## Route Behavior (`server/routes.py`)

`POST /v1/chat/completions` must:

- use app-scoped config (`app.state.config`) for consistency
- build pipeline with optional `cache_backend`/`session_store`
- add `X-Condense-*` response headers
- only perform cache writeback when cache backend exists
- only update session state when session store exists

## Tests To Keep

- schema validation tests (`test_config/test_schema.py`)
- pipeline scheduling tests (`test_pipeline/test_executor.py`)
- forward-step LiteLLM tests (`test_pipeline/test_forward_step.py`)
- route integration tests (`test_server/test_routes.py`)
- E2E route tests (`test_server/test_e2e_pipeline_integration.py`)
- Docker + local Ollama integration test (`test_server/test_docker_ollama_integration.py`)

## Docker Expectations

- `docker-compose.minimal.yml` runs proxy-only service
- healthcheck should not depend on curl; use Python stdlib probe
- integration test lifecycle:
  - write runtime config
  - `compose up -d --build`
  - wait for `/health`
  - run chat request and assert `200`
  - always `compose down` in `finally`

## Local Model Validation Example

For Ollama local:

```yaml
upstream:
  url: "http://localhost:11434"
```

Request:

```json
{
  "model": "ollama/gemma3:4b",
  "messages": [{"role": "user", "content": "Hello"}],
  "temperature": 0
}
```

Expected:

- first call `200`, normal model output
- second identical call cache hit (`X-Condense-Cache-Hit: true`)

## Scaffolding Checklist

When generating or refactoring code, ensure:

1. canonical optimization schema exists
2. DAG scheduler + forward/backward phases exist
3. LiteLLM SDK forwarding is used (no LiteLLM server dependency)
4. optimization-aware resource startup is present
5. tests reflect current architecture and pass
