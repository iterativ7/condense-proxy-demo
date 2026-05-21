"""ML-based model routing via LLMRouter.

Routes requests to strong or weak models based on query complexity analysis.
Supports heuristic strategies (smallest_llm, largest_llm) out of the box,
and trained ML strategies when a config_path is provided.

LLMRouter (llmrouter-lib) is an optional dependency — the router gracefully
degrades with a warning when the library is not installed.
"""

from __future__ import annotations

import atexit
import contextlib
import io
import json
import logging
import shutil
import tempfile
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

_HEURISTIC_ROUTERS = frozenset({"smallest_llm", "largest_llm"})


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


class ModelRouter:
    """Cost-aware model router wrapping LLMRouter.

    At each request the router's ``route_single`` method chooses between
    strong and weak models based on query complexity. Heuristic routers
    (``smallest_llm``, ``largest_llm``) work with only strong/weak model
    names. Trained strategies require a ``config_path`` to a valid
    LLMRouter YAML.

    Parameters
    ----------
    strong : str
        LiteLLM model identifier for the strong/expensive model.
    weak : str
        LiteLLM model identifier for the weak/cheap model.
    threshold : float
        Routing threshold (strategy-dependent, typically 0.0–1.0).
    router_type : str
        LLMRouter strategy name (e.g. ``"smallest_llm"``, ``"largest_llm"``).
    config_path : str or None
        Path to a LLMRouter YAML config (required for trained strategies).
    """

    def __init__(
        self,
        strong: str = "gpt-4o",
        weak: str = "gpt-4o-mini",
        threshold: float = 0.5,
        router_type: str = "smallest_llm",
        config_path: str | None = None,
    ):
        self.strong = strong
        self.weak = weak
        self.threshold = threshold
        self.router_type = router_type
        self.config_path = config_path
        self._key_to_litellm = {"weak": weak, "strong": strong}
        self._router = self._load_router()

    @property
    def available(self) -> bool:
        """Whether the underlying LLMRouter loaded successfully."""
        return self._router is not None

    def _load_router(self) -> Any:
        """Attempt to load the LLMRouter backend.

        Returns None (with a warning) if:
        - llmrouter-lib is not installed
        - config_path is missing for trained strategies
        - LLMRouter initialization fails
        """
        try:
            from llmrouter.cli.router_inference import load_router  # type: ignore[import-untyped]
        except ImportError:
            logger.warning(
                "llmrouter-lib not available — model routing disabled. "
                "Install with: pip install llmrouter-lib"
            )
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
        elif rt in _HEURISTIC_ROUTERS:
            cfg_path = _write_heuristic_bundle(self.strong, self.weak)
        else:
            logger.warning(
                "router_type=%r requires config_path (YAML), or use "
                "smallest_llm / largest_llm — model routing disabled",
                self.router_type,
            )
            return None

        try:
            # LLMRouter prints to stdout during init; suppress it.
            with contextlib.redirect_stdout(io.StringIO()):
                return load_router(self.router_type, cfg_path)
        except Exception as exc:
            logger.warning("LLMRouter load failed — model routing disabled: %s", exc)
            return None

    def _resolve_litellm_model(self, key: str) -> str:
        """Map a router key (e.g. 'weak', 'strong') to a LiteLLM model id.

        Falls back to the raw key if it doesn't match strong/weak and
        isn't found in the router's llm_data.
        """
        if key in self._key_to_litellm:
            return self._key_to_litellm[key]
        # Check llm_data from the router instance for additional model mappings
        ld = getattr(self._router, "llm_data", None) or {}
        entry = ld.get(key) if isinstance(ld, dict) else None
        if isinstance(entry, dict) and entry.get("model"):
            return str(entry["model"])
        return key

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
        if self._router is None:
            return None

        try:
            messages = request.get("messages", [])
            query = _messages_to_query(messages)
            routing = self._router.route_single({"query": query})

            key = (
                routing.get("model_name")
                or routing.get("predicted_llm")
                or routing.get("predicted_llm_name")
            )
            if not key:
                logger.warning(
                    "[model_router] no model in routing result, keeping original"
                )
                return None

            chosen = self._resolve_litellm_model(str(key))
            logger.debug(
                "[model_router] routed to %s (route key=%s)", chosen, key
            )
            return chosen

        except Exception as exc:
            logger.warning(
                "[model_router] routing failed, keeping original: %s", exc
            )
            return None
