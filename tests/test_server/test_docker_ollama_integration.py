"""Docker integration test for local Ollama-backed Condense pipeline."""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

import httpx
import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
COMPOSE_FILE = REPO_ROOT / "docker-compose.minimal.yml"
RUNTIME_CONFIG = REPO_ROOT / "condense.yaml"


def _run_command(args: list[str], *, timeout_s: int = 240) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        timeout=timeout_s,
        check=True,
    )


def _wait_for_health(url: str, timeout_s: int = 120) -> None:
    deadline = time.time() + timeout_s
    last_error = ""
    while time.time() < deadline:
        try:
            response = httpx.get(url, timeout=5.0)
            if response.status_code == 200:
                body = response.json()
                if body.get("status") == "healthy":
                    return
            last_error = f"health check returned {response.status_code}: {response.text}"
        except Exception as exc:  # pragma: no cover - best-effort polling
            last_error = str(exc)
        time.sleep(2)
    raise AssertionError(f"Service did not become healthy in time: {last_error}")


def _ollama_has_model(model_name: str) -> bool:
    response = httpx.get("http://localhost:11434/api/tags", timeout=5.0)
    response.raise_for_status()
    payload = response.json()
    models = payload.get("models", [])
    names = {m.get("name", "") for m in models}
    return model_name in names


@pytest.mark.integration
def test_docker_stack_ollama_query_returns_200() -> None:
    """Start docker stack, validate a 200 LLM response, then tear down."""
    try:
        _run_command(["docker", "info"], timeout_s=20)
    except Exception as exc:
        pytest.skip(f"Docker is unavailable: {exc}")

    try:
        if not _ollama_has_model("gemma3:4b"):
            pytest.skip("Ollama model gemma3:4b is not installed")
    except Exception as exc:
        pytest.skip(f"Ollama is unavailable on localhost:11434: {exc}")

    config = """upstream:
  url: "http://host.docker.internal:11434"
  timeout_seconds: 300

deployment:
  mode: "standalone"
  host: "0.0.0.0"
  port: 8080

optimizations:
  - id: "exact_cache"
    type: "cache"
    enabled: true
    config:
      exact:
        enabled: true
        backend: "memory"
        max_size: 10000
        ttl_seconds: 3600
      non_deterministic: "skip"
  - id: "provider_cache"
    type: "provider_cache"
    enabled: false
    config: {}
  - id: "routing"
    type: "routing"
    enabled: false
    config:
      rules: []
  - id: "budget"
    type: "budget"
    enabled: true
    config:
      max_session_cost_usd: 10.0
      max_turns_per_session: 100
      loop_detection_window: 5

redis:
  enabled: false
  url: "redis://localhost:6379"

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
"""
    original_config = RUNTIME_CONFIG.read_text(encoding="utf-8") if RUNTIME_CONFIG.exists() else None
    RUNTIME_CONFIG.write_text(config, encoding="utf-8")

    try:
        # Ensure clean start, then bring up stack.
        _run_command(
            ["docker", "compose", "-f", str(COMPOSE_FILE), "down", "--remove-orphans"],
            timeout_s=60,
        )
        _run_command(
            ["docker", "compose", "-f", str(COMPOSE_FILE), "up", "-d", "--build"],
            timeout_s=240,
        )
        _wait_for_health("http://localhost:8080/health", timeout_s=120)

        request = {
            "model": "ollama/gemma3:4b",
            "messages": [
                {"role": "user", "content": "Respond in exactly four words about local AI."}
            ],
            "temperature": 0,
        }
        response = httpx.post(
            "http://localhost:8080/v1/chat/completions",
            json=request,
            timeout=120.0,
        )
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload.get("choices"), json.dumps(payload)
        assert payload["choices"][0]["message"]["content"]
    finally:
        subprocess.run(
            ["docker", "compose", "-f", str(COMPOSE_FILE), "down", "--remove-orphans"],
            cwd=REPO_ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        if original_config is None:
            if RUNTIME_CONFIG.exists():
                RUNTIME_CONFIG.unlink()
        else:
            RUNTIME_CONFIG.write_text(original_config, encoding="utf-8")
