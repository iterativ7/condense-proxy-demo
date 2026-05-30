"""CLI commands for Condense proxy."""

import importlib.resources
import logging
import os
import shutil
import sys
from pathlib import Path

import click

from condense import __version__

PRESETS = ["agent", "rag", "chat", "conservative", "aggressive"]


@click.group()
@click.version_option(version=__version__, prog_name="condense")
def cli():
    """Condense — LLM cost optimization proxy."""
    pass


@cli.command()
@click.option("--preset", type=click.Choice(PRESETS), default=None, help="Config preset to use")
@click.option("--output", "-o", default="condense.yaml", help="Output file path")
def init(preset: str, output: str):
    """Generate a condense.yaml configuration file."""
    if Path(output).exists():
        if not click.confirm(f"{output} already exists. Overwrite?"):
            click.echo("Aborted.")
            return

    if preset is None:
        click.echo("Available presets:")
        for p in PRESETS:
            click.echo(f"  - {p}")
        preset = click.prompt("Choose a preset", type=click.Choice(PRESETS), default="conservative")

    # Load preset from package data
    preset_path = Path(__file__).parent / "config" / "presets" / f"{preset}.yaml"
    if preset_path.exists():
        shutil.copy(preset_path, output)
        click.echo(f"[OK] Generated {output} from '{preset}' preset")
    else:
        # Fallback: generate a default config
        _write_default_config(output, preset)
        click.echo(f"[OK] Generated {output} with '{preset}' settings")

    click.echo(f"\nNext steps:")
    click.echo(f"  1. Edit {output} to set your upstream URL and API key")
    click.echo(f"  2. Run: condense start")


@cli.command()
@click.option("--config", "-c", default=None, help="Path to condense.yaml")
@click.option("--port", "-p", default=None, type=int, help="Port to listen on")
@click.option("--host", default=None, help="Host to bind to")
def start(config: str, port: int, host: str):
    """Start the Condense proxy server."""
    import uvicorn
    from condense.config.loader import load_config

    # Load config to get defaults
    try:
        cfg = load_config(config)
    except FileNotFoundError as e:
        click.echo(f"Error: {e}", err=True)
        click.echo("Run 'condense init' to generate a config file.", err=True)
        sys.exit(1)
    except Exception as e:
        click.echo(f"Config error: {e}", err=True)
        sys.exit(1)

    listen_port = port or cfg.deployment.port
    listen_host = host or cfg.deployment.host

    click.echo(f"Condense v{__version__} starting on {listen_host}:{listen_port}")
    click.echo(f"   Upstream: {cfg.upstream.url}")
    click.echo(f"   Mode: {cfg.deployment.mode}")

    # Set config path env for the app factory
    if config:
        os.environ["CONDENSE_CONFIG"] = config

    uvicorn.run(
        "condense.server.app:create_app",
        factory=True,
        host=listen_host,
        port=listen_port,
        log_level="info",
    )


@cli.command()
@click.option("--url", default="http://localhost:8080", help="Condense proxy URL")
def status(url: str):
    """Show the status of a running Condense instance."""
    import httpx

    try:
        # Health check
        health_resp = httpx.get(f"{url}/health", timeout=5)
        health = health_resp.json()
        click.echo(f"Status: {health.get('status', 'unknown')}")

        # Readiness
        ready_resp = httpx.get(f"{url}/health/ready", timeout=5)
        ready = ready_resp.json()
        click.echo(f"Config loaded: {ready.get('config_loaded', False)}")
        click.echo(f"Upstream: {ready.get('upstream', 'unknown')}")

        # Metrics
        metrics_resp = httpx.get(f"{url}/metrics", timeout=5)
        if metrics_resp.status_code == 200:
            click.echo(f"\nMetrics:")
            for line in metrics_resp.text.strip().split("\n"):
                if not line.startswith("#"):
                    click.echo(f"  {line}")

    except httpx.ConnectError:
        click.echo(f"Error: Cannot connect to {url}", err=True)
        click.echo("Is Condense running? Start with: condense start", err=True)
        sys.exit(1)
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)


def _write_default_config(path: str, preset: str):
    """Write a default config file."""
    # Preset-specific overrides
    configs = {
        "conservative": {
            "cache_enabled": True,
            "routing_enabled": False,
            "budget_enabled": True,
            "provider_cache_enabled": True,
        },
        "aggressive": {
            "cache_enabled": True,
            "routing_enabled": True,
            "budget_enabled": True,
            "provider_cache_enabled": True,
        },
        "agent": {
            "cache_enabled": True,
            "routing_enabled": False,
            "budget_enabled": True,
            "provider_cache_enabled": True,
        },
        "rag": {
            "cache_enabled": True,
            "routing_enabled": True,
            "budget_enabled": False,
            "provider_cache_enabled": True,
        },
        "chat": {
            "cache_enabled": True,
            "routing_enabled": True,
            "budget_enabled": True,
            "provider_cache_enabled": True,
        },
    }

    c = configs.get(preset, configs["conservative"])

    content = f"""# condense.yaml — Generated with '{preset}' preset
# Docs: https://github.com/condense-ai/condense

upstream:
  url: "https://api.openai.com/v1"
  timeout_seconds: 300
  # api_key_env: "OPENAI_API_KEY"

deployment:
  mode: "behind-gateway"
  host: "0.0.0.0"
  port: 8080

optimizations:
  - id: "cache"
    type: "cache"
    enabled: {str(c['cache_enabled']).lower()}
    config:
      exact:
        enabled: true
        backend: "memory"
        max_size: 10000
        ttl_seconds: 3600
      non_deterministic: "skip"

  - id: "provider_cache"
    type: "provider_cache"
    enabled: {str(c['provider_cache_enabled']).lower()}
    depends_on: ["cache"]
    config:
      anthropic:
        inject_cache_control: true
        cache_system_prompt: true
        cache_tools: true
      openai:
        enabled: true
      deepseek:
        enabled: true

  - id: "routing"
    type: "routing"
    enabled: {str(c['routing_enabled']).lower()}
    depends_on: ["provider_cache"]
    config:
      rules:
        - condition: "short_messages"
          max_chars: 500
          model: "gpt-4o-mini"
        - condition: "no_tools"
          model: "gpt-4o-mini"

  - id: "budget"
    type: "budget"
    enabled: {str(c['budget_enabled']).lower()}
    config:
      max_session_cost_usd: 10.0
      max_turns_per_session: 100
      loop_detection_window: 5

redis:
  enabled: false
  url: "redis://localhost:6379"
  # password_env: "REDIS_PASSWORD"

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
    with open(path, "w") as f:
        f.write(content)
