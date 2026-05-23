"""RouteLLM backend (lm-sys/RouteLLM).

Pre-trained routers that classify query complexity locally.
The ``bert`` strategy runs fully offline; ``mf`` and ``sw_ranking``
may require an OpenAI API key for embeddings.

Install: ``pip install routellm``
"""

from __future__ import annotations

import logging
import os
from typing import Any, Optional

from condense.routing.base import RoutingBackend, routing_registry

logger = logging.getLogger(__name__)

_STRATEGIES = frozenset({"bert", "mf", "causal_llm", "sw_ranking", "random"})


def _register_all():
    """Register one backend entry per RouteLLM strategy name."""
    for strategy in sorted(_STRATEGIES):

        # Use a factory to capture *strategy* in the closure.
        def _make_cls(strat: str) -> type:
            class _RouteLLMBackend(RoutingBackend):
                __doc__ = f"RouteLLM '{strat}' routing strategy."

                def __init__(self, *, strong: str, weak: str, threshold: float, **kwargs):
                    self.router_type = strat
                    self.strong = strong
                    self.weak = weak
                    self.threshold = threshold
                    self._controller = self._load()

                @property
                def available(self) -> bool:
                    return self._controller is not None

                def _load(self) -> Any:
                    try:
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
                        logger.info("RouteLLM backend loaded (strategy=%s)", self.router_type)
                        return controller
                    except ImportError:
                        logger.debug("routellm package not installed")
                        return None
                    except Exception as exc:
                        logger.warning("RouteLLM load failed: %s", exc)
                        return None

                def route(self, query: str) -> Optional[str]:
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

            _RouteLLMBackend.__name__ = f"RouteLLM_{strat}"
            _RouteLLMBackend.__qualname__ = f"RouteLLM_{strat}"
            return _RouteLLMBackend

        routing_registry.register(strategy)(_make_cls(strategy))


_register_all()
