# Development Guide

This guide is for local development and repeatable startup of the Condense optimization server.

## Goal

Run a local Condense server that accepts OpenAI-compatible chat-completions requests and applies configured optimizations (cache, provider-cache, routing, budget).

## Prerequisites

- Python virtualenv at `.venv` with project dependencies installed
- Docker available if you want Ollama-backed local model testing
- A config file (`condense.default.yaml`, `condense.local.yaml`, or `condense.yaml`)

## Start The Optimization Server

Use an explicit config path to avoid ambiguity:

```bash
.venv/bin/python -m condense.cli start --config condense.default.yaml --host 127.0.0.1 --port 8080
```

Health checks:

```bash
curl http://127.0.0.1:8080/health
curl http://127.0.0.1:8080/health/ready
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

