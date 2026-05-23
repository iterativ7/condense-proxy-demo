"""Generic backend registry.

A ``BackendRegistry`` stores name → class mappings for a specific domain
(routing, compression, …).  Backends self-register via a decorator that
the registry provides, so adding a new backend never requires editing
central code.

Example usage inside a domain package::

    from condense.backends.registry import BackendRegistry
    from condense.routing.base import RoutingBackend

    routing_registry = BackendRegistry[RoutingBackend]("routing")

    # In each backend file:
    @routing_registry.register("bert")
    class RouteLLMBertBackend(RoutingBackend):
        ...

    # At lookup time:
    backend_cls = routing_registry.get("bert")
"""

from __future__ import annotations

import logging
from typing import Callable, Dict, Generic, Optional, Type, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")


class BackendRegistry(Generic[T]):
    """A typed registry mapping string names to backend classes.

    Parameters
    ----------
    domain : str
        Human-readable domain name (for error messages), e.g. "routing".
    """

    def __init__(self, domain: str) -> None:
        self.domain = domain
        self._backends: Dict[str, Type[T]] = {}

    # -- registration -------------------------------------------------------

    def register(self, name: str) -> Callable[[Type[T]], Type[T]]:
        """Decorator that registers a backend class under *name*.

        Raises
        ------
        ValueError
            If *name* is already registered (prevents silent overwrites).
        """

        def decorator(cls: Type[T]) -> Type[T]:
            canonical = name.replace("-", "_").lower()
            if canonical in self._backends:
                raise ValueError(
                    f"[{self.domain}] backend {canonical!r} already registered "
                    f"(existing: {self._backends[canonical].__name__}, "
                    f"new: {cls.__name__})"
                )
            self._backends[canonical] = cls
            logger.debug("[%s] registered backend %r → %s", self.domain, canonical, cls.__name__)
            return cls

        return decorator

    # -- lookup -------------------------------------------------------------

    def get(self, name: str) -> Optional[Type[T]]:
        """Return the backend class for *name*, or ``None`` if not found."""
        return self._backends.get(name.replace("-", "_").lower())

    def get_or_raise(self, name: str) -> Type[T]:
        """Return the backend class for *name*, or raise ``KeyError``."""
        cls = self.get(name)
        if cls is None:
            available = ", ".join(sorted(self._backends)) or "(none)"
            raise KeyError(
                f"[{self.domain}] unknown backend {name!r}. "
                f"Available: {available}"
            )
        return cls

    # -- introspection ------------------------------------------------------

    def available_names(self) -> list[str]:
        """Return sorted list of registered backend names."""
        return sorted(self._backends)

    def __contains__(self, name: str) -> bool:
        return name.replace("-", "_").lower() in self._backends

    def __len__(self) -> int:
        return len(self._backends)

    def __repr__(self) -> str:
        names = ", ".join(sorted(self._backends))
        return f"BackendRegistry(domain={self.domain!r}, backends=[{names}])"
