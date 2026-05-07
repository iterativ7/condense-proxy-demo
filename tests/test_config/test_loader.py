"""Tests for config loader."""

import pytest
import tempfile
import os
from pathlib import Path
from condense.config.loader import load_config, reset_config_cache
from condense.config.schema import CondenseConfig


class TestConfigLoader:
    def test_load_defaults_when_no_file(self, tmp_path, monkeypatch):
        """When no config file exists, defaults are used."""
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("CONDENSE_CONFIG", raising=False)
        reset_config_cache()
        config = load_config()
        assert isinstance(config, CondenseConfig)
        assert config.upstream.url == "https://api.openai.com/v1"

    def test_load_from_yaml(self, tmp_path):
        """Config loads from a YAML file."""
        config_file = tmp_path / "condense.yaml"
        config_file.write_text("""
upstream:
  url: "http://localhost:4000/v1"
  timeout_seconds: 120
deployment:
  port: 9999
""")
        reset_config_cache()
        config = load_config(str(config_file))
        assert config.upstream.url == "http://localhost:4000/v1"
        assert config.upstream.timeout_seconds == 120
        assert config.deployment.port == 9999

    def test_file_not_found(self):
        """FileNotFoundError raised for missing config."""
        reset_config_cache()
        with pytest.raises(FileNotFoundError):
            load_config("/nonexistent/condense.yaml")

    def test_caching(self, tmp_path):
        """Config is cached after first load."""
        config_file = tmp_path / "condense.yaml"
        config_file.write_text("upstream:\n  url: 'http://test.com'\n")
        reset_config_cache()
        config1 = load_config(str(config_file))
        config2 = load_config(str(config_file))
        assert config1 is config2  # Same object (cached)

    def test_env_var_config_path(self, tmp_path, monkeypatch):
        """CONDENSE_CONFIG env var specifies config path."""
        config_file = tmp_path / "custom.yaml"
        config_file.write_text("deployment:\n  port: 7777\n")
        monkeypatch.setenv("CONDENSE_CONFIG", str(config_file))
        reset_config_cache()
        config = load_config()
        assert config.deployment.port == 7777
