# Development Guide

This guide is for local development and repeatable startup of the Condense optimization server.

## Goal

Run a local Condense server that accepts OpenAI-compatible chat-completions requests and applies configured optimizations (cache, provider-cache, routing, budget).

## Prerequisites

- Python virtualenv at `.venv` with project dependencies installed
- Docker available if you want Ollama-backed local model testing
- A config file (`condense.default.yaml`, `condense.local.yaml`, or `condense.yaml`)

## Start The Optimization Server

Use the standardized local commands:

```bash
make start-local
```

Verify local Ollama is reachable and model is present:

```bash
curl http://127.0.0.1:11434/api/tags
```

The response should include the model you plan to use (for example `gemma3:4b`).

Health checks:

```bash
curl http://127.0.0.1:8090/health
curl http://127.0.0.1:8090/health/ready
```

Stop local server:

```bash
make stop-local
```

## Config Selection Rules

If you run `condense start` without `--config`, Condense loads config in this order:

1. `CONDENSE_CONFIG` environment variable
2. `./condense.yaml`
3. `./condense.default.yaml`

For deterministic behavior in development, always pass `--config`.

## Running With Local Ollama (Docker)

Prepare Docker + Ollama dependencies once:

```bash
make docker-prep-integration
```

The prep command:

- prompts for config YAML
- starts/creates `ollama-local`
- ensures selected model exists (default: `gemma3:4b`)
- checks for port conflicts that can break integration runs

## Run Tests (Regression Gate)

Run the full suite:

```bash
make test
```

Run Docker integration test only:

```bash
.venv/bin/python -m pytest tests/test_server/test_docker_ollama_integration.py -q
```

