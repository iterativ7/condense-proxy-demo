"""Extensible backend registry for optimization techniques.

This module provides the core abstractions and registry machinery that
all optimization backends (routing, compression, etc.) build on.

**Adding a new backend** (contributor workflow):

1.  Pick a domain (e.g. ``routing``, ``compression``).
2.  Create a new file under the domain package
    (e.g. ``condense/routing/backends/my_router.py``).
3.  Subclass the domain's abstract base (e.g. ``RoutingBackend``).
4.  Decorate with ``@register_routing_backend("my_router")``.
5.  Done — the strategy name ``"my_router"`` is now available in YAML config.

No core files need to be edited.
"""

from condense.backends.registry import BackendRegistry

__all__ = ["BackendRegistry"]
