"""LLMRouter backend (llmrouter-lib / ulab-uiuc).

Heuristic strategies (``smallest_llm``, ``largest_llm``) and trained
ML strategies.

Install: ``pip install llmrouter-lib``
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

from condense.routing.base import RoutingBackend, routing_registry

logger = logging.getLogger(__name__)

_HEURISTIC_STRATEGIES = frozenset({"smallest_llm", "largest_llm"})


def _write_heuristic_bundle(strong: str, weak: str) -> str:
    """Create a minimal LLMRouter YAML + llm_data.json for heuristic strategies."""
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


def _register_all():
    """Register one backend entry per LLMRouter heuristic strategy."""
    for strategy in sorted(_HEURISTIC_STRATEGIES):

        def _make_cls(strat: str) -> type:
            class _LLMRouterBackend(RoutingBackend):
                __doc__ = f"LLMRouter '{strat}' heuristic strategy."

                def __init__(
                    self,
                    *,
                    strong: str,
                    weak: str,
                    threshold: float = 0.5,
                    config_path: str | None = None,
                    **kwargs,
                ):
                    self.router_type = strat
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

                    if self.config_path:
                        cfg_path = self.config_path
                        if not Path(cfg_path).is_file():
                            logger.warning(
                                "router config_path not found: %s — disabled", cfg_path
                            )
                            return None
                    else:
                        cfg_path = _write_heuristic_bundle(self.strong, self.weak)

                    try:
                        with contextlib.redirect_stdout(io.StringIO()):
                            router = load_router(self.router_type, cfg_path)
                        logger.info("llmrouter-lib backend loaded (strategy=%s)", self.router_type)
                        return router
                    except Exception as exc:
                        logger.warning("llmrouter-lib load failed: %s", exc)
                        return None

                def _resolve_model(self, key: str) -> str:
                    if key in self._key_to_litellm:
                        return self._key_to_litellm[key]
                    ld = getattr(self._router, "llm_data", None) or {}
                    entry = ld.get(key) if isinstance(ld, dict) else None
                    if isinstance(entry, dict) and entry.get("model"):
                        return str(entry["model"])
                    return key

                def route(self, query: str) -> Optional[str]:
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

            _LLMRouterBackend.__name__ = f"LLMRouter_{strat}"
            _LLMRouterBackend.__qualname__ = f"LLMRouter_{strat}"
            return _LLMRouterBackend

        routing_registry.register(strategy)(_make_cls(strategy))


_register_all()
