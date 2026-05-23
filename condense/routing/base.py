"""Abstract base class for routing backends.

Every routing backend must implement this interface.  New backends
self-register via the ``routing_registry`` decorator — no core code
changes required.

Example::

    from condense.routing.base import RoutingBackend, routing_registry

    @routing_registry.register("my_router")
    class MyRouter(RoutingBackend):
        def __init__(self, *, strong, weak, threshold, **kwargs):
            ...

        @property
        def available(self) -> bool:
            return self._engine is not None

        def route(self, query: str) -> str | None:
            ...
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional

from condense.backends.registry import BackendRegistry


class RoutingBackend(ABC):
    """Contract that every routing backend must fulfil."""

    @property
    @abstractmethod
    def available(self) -> bool:
        """Whether the backend loaded successfully and can route."""
        ...

    @abstractmethod
    def route(self, query: str) -> Optional[str]:
        """Route a query and return the chosen model name, or ``None``."""
        ...


# Singleton registry — backends register themselves at import time.
routing_registry: BackendRegistry[RoutingBackend] = BackendRegistry("routing")
