"""ML-based model routing.

Routes requests to strong or weak models based on query complexity analysis.
Supports two backends:

1. **routellm** (lm-sys/RouteLLM) — ships with pre-trained routers (``bert``,
   ``mf``, ``causal_llm``, ``sw_ranking``) that classify query complexity
   locally. ``bert`` runs fully offline; others may require an OpenAI API key
   for embeddings.  Install with ``pip install routellm``.

2. **llmrouter** (llmrouter-lib) — heuristic strategies (``smallest_llm``,
   ``largest_llm``) and trained ML strategies.
   Install with ``pip install llmrouter-lib``.

Both backends are optional — the router gracefully degrades with a warning
when neither library is installed.
"""

from __future__ import annotations

import atexit
import contextlib
import io
import json
import logging
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

_LLMROUTER_HEURISTICS = frozenset({"smallest_llm", "largest_llm"})
_ROUTELLM_STRATEGIES = frozenset({"bert", "mf", "causal_llm", "sw_ranking", "random"})


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _messages_to_query(messages: list[dict[str, Any]], max_chars: int = 16_000) -> str:
    """Extract a text query from chat messages for the router to evaluate.

    Handles both simple string content and structured content blocks
    (e.g. OpenAI vision messages with type: "text" blocks).
    """
    parts: list[str] = []
    for m in messages:
        c = m.get("content", "")
        if isinstance(c, str):
            parts.append(c)
        elif isinstance(c, list):
            for block in c:
                if isinstance(block, dict) and block.get("type") == "text":
                    parts.append(str(block.get("text", "")))
    text = "\n".join(parts).strip()
    if not text:
        return "."
    if len(text) > max_chars:
        return text[:max_chars]
    return text


def _write_heuristic_bundle(strong: str, weak: str) -> str:
    """Create a minimal LLMRouter YAML + llm_data.json for heuristic strategies.

    Returns the absolute path to the generated YAML config file.
    The temp directory is cleaned up on process exit.
    """
    d = tempfile.mkdtemp(prefix="condense-llmrouter-")
    atexit.register(lambda p=d: shutil.rmtree(p, ignore_errors=True))
    base = Path(d)

    llm_path = base / "llm_data.json"
    llm_path.write_text(
        json.dumps(
            {
                "weak": {"size": "1B", "model": weak},
                "strong": {"size": "70B", "model": strong},
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    yaml_path = base / "router.yaml"
    yaml_path.write_text(
        "data_path:\n"
        f"  llm_data: {llm_path.resolve()!s}\n",
        encoding="utf-8",
    )
    return str(yaml_path.resolve())


# ---------------------------------------------------------------------------
# RouteLLM backend (lm-sys)
# ---------------------------------------------------------------------------

class _RouteLLMBackend:
    """Wraps lm-sys/RouteLLM Controller for query-complexity routing."""

    def __init__(
        self,
        router_type: str,
        strong: str,
        weak: str,
        threshold: float,
    ):
        self.router_type = router_type
        self.strong = strong
        self.weak = weak
        self.threshold = threshold
        self._controller = self._load()

    @property
    def available(self) -> bool:
        return self._controller is not None

    def _load(self) -> Any:
        try:
            # RouteLLM eagerly creates an OpenAI client at import time;
            # make sure it doesn't crash if the env var is absent.
            _orig = os.environ.get("OPENAI_API_KEY")
            if not _orig:
                os.environ["OPENAI_API_KEY"] = "sk-placeholder"
            try:
                from routellm.controller import Controller  # type: ignore[import-untyped]
            finally:
                if not _orig:
                    os.environ.pop("OPENAI_API_KEY", None)

            controller = Controller(
                routers=[self.router_type],
                strong_model=self.strong,
                weak_model=self.weak,
            )
            logger.info(
                "RouteLLM backend loaded (strategy=%s)", self.router_type
            )
            return controller
        except ImportError:
            logger.debug("routellm package not installed")
            return None
        except Exception as exc:
            logger.warning("RouteLLM load failed: %s", exc)
            return None

    def route(self, query: str) -> Optional[str]:
        """Return the model name chosen by RouteLLM, or None on failure."""
        if self._controller is None:
            return None
        try:
            return self._controller.route(
                prompt=query,
                threshold=self.threshold,
                router=self.router_type,
            )
        except Exception as exc:
            logger.warning("[routellm] routing error: %s", exc)
            return None


# ---------------------------------------------------------------------------
# LLMRouter backend (llmrouter-lib)
# ---------------------------------------------------------------------------

class _LLMRouterBackend:
    """Wraps ulab-uiuc/LLMRouter for heuristic and trained strategies."""

    def __init__(
        self,
        router_type: str,
        strong: str,
        weak: str,
        config_path: str | None,
    ):
        self.router_type = router_type
        self.strong = strong
        self.weak = weak
        self.config_path = config_path
        self._key_to_litellm = {"weak": weak, "strong": strong}
        self._router = self._load()

    @property
    def available(self) -> bool:
        return self._router is not None

    def _load(self) -> Any:
        try:
            from llmrouter.cli.router_inference import load_router  # type: ignore[import-untyped]
        except ImportError:
            logger.debug("llmrouter-lib package not installed")
            return None

        rt = self.router_type.replace("-", "_").lower()

        if self.config_path:
            cfg_path = self.config_path
            if not Path(cfg_path).is_file():
                logger.warning(
                    "router config_path not found: %s — model routing disabled",
                    cfg_path,
                )
                return None
        elif rt in _LLMROUTER_HEURISTICS:
            cfg_path = _write_heuristic_bundle(self.strong, self.weak)
        else:
            logger.warning(
                "router_type=%r requires config_path for llmrouter-lib backend",
                self.router_type,
            )
            return None

        try:
            with contextlib.redirect_stdout(io.StringIO()):
                router = load_router(self.router_type, cfg_path)
            logger.info(
                "llmrouter-lib backend loaded (strategy=%s)", self.router_type
            )
            return router
        except Exception as exc:
            logger.warning("llmrouter-lib load failed: %s", exc)
            return None

    def _resolve_model(self, key: str) -> str:
        """Map a router key (e.g. 'weak', 'strong') to a LiteLLM model id."""
        if key in self._key_to_litellm:
            return self._key_to_litellm[key]
        ld = getattr(self._router, "llm_data", None) or {}
        entry = ld.get(key) if isinstance(ld, dict) else None
        if isinstance(entry, dict) and entry.get("model"):
            return str(entry["model"])
        return key

    def route(self, query: str) -> Optional[str]:
        """Return the resolved model name, or None on failure."""
        if self._router is None:
            return None
        try:
            routing = self._router.route_single({"query": query})
            key = (
                routing.get("model_name")
                or routing.get("predicted_llm")
                or routing.get("predicted_llm_name")
            )
            if not key:
                return None
            return self._resolve_model(str(key))
        except Exception as exc:
            logger.warning("[llmrouter] routing error: %s", exc)
            return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

class ModelRouter:
    """Cost-aware model router with pluggable backends.

    Automatically selects the appropriate backend based on ``router_type``:

    - RouteLLM strategies (``bert``, ``mf``, ``causal_llm``, ``sw_ranking``,
      ``random``) use the lm-sys/RouteLLM backend.
    - LLMRouter strategies (``smallest_llm``, ``largest_llm``, or any trained
      strategy with a ``config_path``) use the llmrouter-lib backend.

    Parameters
    ----------
    strong : str
        LiteLLM model identifier for the strong/expensive model.
    weak : str
        LiteLLM model identifier for the weak/cheap model.
    threshold : float
        Routing threshold — higher values route more queries to the weak
        model.  Semantics are backend-specific.
    router_type : str
        Strategy name.  Determines which backend is used.
    config_path : str or None
        Path to a config file (required for trained llmrouter-lib strategies).
    """

    def __init__(
        self,
        strong: str = "gpt-4o",
        weak: str = "gpt-4o-mini",
        threshold: float = 0.5,
        router_type: str = "bert",
        config_path: str | None = None,
    ):
        self.strong = strong
        self.weak = weak
        self.threshold = threshold
        self.router_type = router_type
        self.config_path = config_path

        rt_lower = router_type.replace("-", "_").lower()

        if rt_lower in _ROUTELLM_STRATEGIES:
            self._backend = _RouteLLMBackend(
                router_type=rt_lower,
                strong=strong,
                weak=weak,
                threshold=threshold,
            )
        else:
            self._backend = _LLMRouterBackend(
                router_type=router_type,
                strong=strong,
                weak=weak,
                config_path=config_path,
            )

        if not self._backend.available:
            logger.warning(
                "No routing backend available for router_type=%r. "
                "Install routellm (pip install routellm) or "
                "llmrouter-lib (pip install llmrouter-lib).",
                router_type,
            )

    @property
    def available(self) -> bool:
        """Whether the underlying routing backend loaded successfully."""
        return self._backend.available

    def route(self, request: dict) -> Optional[str]:
        """Route a request to the appropriate model.

        Parameters
        ----------
        request : dict
            OpenAI-compatible chat completions request body.

        Returns
        -------
        str or None
            The LiteLLM model identifier to use, or None if routing
            could not determine a model (caller should keep original).
        """
        if not self._backend.available:
            return None

        try:
            messages = request.get("messages", [])
            query = _messages_to_query(messages)
            chosen = self._backend.route(query)

            if chosen:
                logger.debug(
                    "[model_router] routed to %s (strategy=%s)",
                    chosen,
                    self.router_type,
                )
            return chosen

        except Exception as exc:
            logger.warning(
                "[model_router] routing failed, keeping original: %s", exc
            )
            return None
