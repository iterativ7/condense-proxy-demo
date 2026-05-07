"""YAML config loading, validation, and hot-reload support."""

import logging
import os
from pathlib import Path
from typing import Optional

import yaml

from condense.config.schema import CondenseConfig

logger = logging.getLogger(__name__)

_cached_config: Optional[CondenseConfig] = None
_config_path: Optional[Path] = None
_config_mtime: float = 0.0


def load_config(path: Optional[str] = None) -> CondenseConfig:
    """Load and validate condense.yaml from the given path.

    If no path is specified, searches in order:
      1. CONDENSE_CONFIG env var
      2. ./condense.yaml
      3. ./condense.default.yaml
    """
    global _cached_config, _config_path, _config_mtime

    if path is None:
        path = os.environ.get("CONDENSE_CONFIG")

    if path is None:
        for candidate in ["condense.yaml", "condense.default.yaml"]:
            if Path(candidate).exists():
                path = candidate
                break

    if path is None:
        logger.info("No config file found, using defaults")
        _cached_config = CondenseConfig()
        return _cached_config

    config_path = Path(path)
    if not config_path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")

    # Hot-reload: check mtime
    current_mtime = config_path.stat().st_mtime
    if _cached_config is not None and _config_path == config_path and current_mtime == _config_mtime:
        return _cached_config

    logger.info(f"Loading config from {config_path}")
    with open(config_path) as f:
        raw = yaml.safe_load(f) or {}

    _cached_config = CondenseConfig(**raw)
    _config_path = config_path
    _config_mtime = current_mtime
    return _cached_config


def reload_config() -> Optional[CondenseConfig]:
    """Force reload the config from disk."""
    global _cached_config, _config_mtime
    _config_mtime = 0.0
    _cached_config = None
    if _config_path:
        return load_config(str(_config_path))
    return load_config()


def reset_config_cache() -> None:
    """Reset the cached config (for testing)."""
    global _cached_config, _config_path, _config_mtime
    _cached_config = None
    _config_path = None
    _config_mtime = 0.0
