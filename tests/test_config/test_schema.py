"""Tests for config schema validation."""

import pytest
from pydantic import ValidationError
from condense.config.schema import CondenseConfig, UpstreamConfig, RoutingRule


class TestCondenseConfig:
    def test_default_config(self):
        """Default config should be valid."""
        config = CondenseConfig()
        assert config.upstream.url == "https://api.openai.com/v1"
        assert config.deployment.port == 8080
        assert config.deployment.streaming_enabled is True
        assert config.optimizations == []
        assert config.failsafe.on_error == "passthrough"
        assert config.metrics.backend == "sqlite"
        assert config.metrics.sqlite_path == ".condense/metrics.sqlite3"

    def test_from_dict(self):
        """Config can be created from a dict (as loaded from YAML)."""
        data = {
            "upstream": {"url": "https://custom.api.com/v1", "timeout_seconds": 60},
            "deployment": {"port": 9090},
            "optimizations": [
                {
                    "id": "cache-opt",
                    "type": "cache",
                    "enabled": False,
                    "config": {"non_deterministic": "skip"},
                },
                {
                    "id": "routing-opt",
                    "type": "routing",
                    "enabled": True,
                    "config": {
                        "rules": [{"condition": "short_messages", "max_chars": 200, "model": "gpt-4o-mini"}]
                    },
                },
            ],
        }
        config = CondenseConfig(**data)
        assert config.upstream.url == "https://custom.api.com/v1"
        assert config.upstream.timeout_seconds == 60
        assert config.deployment.port == 9090
        assert config.optimizations[0].enabled is False
        assert config.optimizations[1].enabled is True
        assert len(config.routing_config().rules) == 1

    def test_routing_rule_validation(self):
        """Routing rules require condition and model."""
        rule = RoutingRule(condition="short_messages", model="gpt-4o-mini", max_chars=500)
        assert rule.condition == "short_messages"
        assert rule.model == "gpt-4o-mini"
        assert rule.max_chars == 500

    def test_partial_override(self):
        """Partial config overrides should use defaults for missing fields."""
        config = CondenseConfig(upstream={"url": "http://localhost:4000"})
        assert config.upstream.url == "http://localhost:4000"
        assert config.upstream.timeout_seconds == 300  # default
        assert config.deployment.port == 8080  # default

    def test_model_dump(self):
        """Config can be serialized back to dict."""
        config = CondenseConfig()
        data = config.model_dump()
        assert "upstream" in data
        assert "optimizations" in data
        assert data["upstream"]["url"] == "https://api.openai.com/v1"

    def test_duplicate_optimization_ids_fail(self):
        with pytest.raises(ValidationError):
            CondenseConfig(
                optimizations=[
                    {"id": "dup", "type": "cache"},
                    {"id": "dup", "type": "routing"},
                ]
            )

    def test_dependency_cycle_fails(self):
        with pytest.raises(ValidationError):
            CondenseConfig(
                optimizations=[
                    {"id": "a", "type": "cache", "depends_on": ["b"]},
                    {"id": "b", "type": "routing", "depends_on": ["a"]},
                ]
            )
