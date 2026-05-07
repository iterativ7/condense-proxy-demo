"""SHA-256 hashing utilities."""

import hashlib


def sha256_hex(data: str) -> str:
    """Return the SHA-256 hex digest of the given string."""
    return hashlib.sha256(data.encode()).hexdigest()


def short_hash(data: str, length: int = 16) -> str:
    """Return a truncated SHA-256 hex digest."""
    return sha256_hex(data)[:length]
