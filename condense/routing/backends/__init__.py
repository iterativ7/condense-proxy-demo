"""Routing backend implementations.

Importing this package auto-registers all built-in backends.
"""

# Import all backend modules so their @register decorators run.
from condense.routing.backends import routellm_backend  # noqa: F401
from condense.routing.backends import llmrouter_backend  # noqa: F401
