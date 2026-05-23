"""ML-based model routing with pluggable backends.

Backends auto-register via the ``routing_registry`` — see
``condense/routing/backends/`` for built-in implementations.

Adding a new routing backend requires **zero changes** to this file:
just create a new module, subclass ``RoutingBackend``, and decorate
with ``@routing_registry.register("my_strategy")``.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from condense.routing.base import routing_registry

# Trigger auto-registration of built-in backends.
import condense.routing.backends  # noqa: F401

logger = logging.getLogger(__name__)


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


class ModelRouter:
    """Cost-aware model router with pluggable backends.

    Automatically selects the appropriate backend from the registry
    based on ``router_type``.

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
        Strategy name.  Must match a registered backend.
    config_path : str or None
        Path to a config file (required for some trained strategies).
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
        backend_cls = routing_registry.get(rt_lower)

        if backend_cls is None:
            available = ", ".join(routing_registry.available_names()) or "(none)"
            logger.warning(
                "Unknown routing backend %r. Available: %s. "
                "Model routing disabled.",
                router_type,
                available,
            )
            self._backend = None
        else:
            self._backend = backend_cls(
                strong=strong,
                weak=weak,
                threshold=threshold,
                config_path=config_path,
            )

        if self._backend is not None and not self._backend.available:
            logger.warning(
                "Routing backend %r loaded but unavailable. "
                "Check that the required library is installed.",
                router_type,
            )

    @property
    def available(self) -> bool:
        """Whether the underlying routing backend loaded successfully."""
        return self._backend is not None and self._backend.available

    def route(self, request: dict) -> Optional[str]:
        """Route a request to the appropriate model.

        Returns the chosen model identifier, or ``None`` if routing
        could not determine a model (caller keeps the original).
        """
        if not self.available:
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
