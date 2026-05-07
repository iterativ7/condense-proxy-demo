"""Cache key computation using SHA-256 hash."""

import hashlib
import json

CACHE_KEY_PARAMS = [
    "model", "messages", "tools", "tool_choice",
    "temperature", "top_p", "max_tokens", "stop",
    "response_format", "seed",
]


def compute_cache_key(request: dict, namespace: str = "") -> str:
    """Compute a deterministic cache key from request parameters.

    Args:
        request: The OpenAI-compatible request body.
        namespace: Optional namespace prefix (API key hash for tenant isolation).

    Returns:
        A SHA-256 hex digest string, optionally prefixed with namespace.
    """
    key_parts = []
    for param in sorted(CACHE_KEY_PARAMS):
        if param in request and request[param] is not None:
            value = request[param]
            if isinstance(value, (dict, list)):
                value = json.dumps(value, sort_keys=True, default=str)
            key_parts.append(f"{param}:{value}")

    raw_key = "|".join(key_parts)
    hashed = hashlib.sha256(raw_key.encode()).hexdigest()

    if namespace:
        return f"{namespace}:{hashed}"
    return hashed
