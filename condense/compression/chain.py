"""Compression chain — ordered list of backends that run back-to-back.

Fixes P0-13 from the philosophy audit: "Compression Is Single-Backend
Only (No Chain)".

Each link in the chain can target specific message roles via ``apply_to``.
This enables the canonical pattern:

    chain:
      - backend: "rtk"       # structural filters on tool output (fast)
        apply_to: ["tool"]
      - backend: "fusion"    # semantic compression on natural language
        apply_to: ["user", "system"]

Messages flow through backends in order.  Each backend only sees (and may
modify) messages whose role matches its ``apply_to`` filter.  Non-matching
messages pass through unchanged.

If ``apply_to`` is omitted or empty, the backend processes ALL messages
(backward compatible with single-backend mode).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from condense.compression.base import (
    CompressionBackend,
    CompressResult,
    compression_registry,
)

logger = logging.getLogger(__name__)


@dataclass
class ChainLink:
    """One backend in the compression chain."""

    backend_name: str
    apply_to: frozenset[str]  # empty = all roles
    backend_kwargs: dict[str, Any] = field(default_factory=dict)


class CompressionChain:
    """Ordered chain of compression backends.

    Parameters
    ----------
    chain_config : list[dict]
        Each entry has ``backend`` (str), optional ``apply_to`` (list[str]),
        and any backend-specific kwargs.
    """

    def __init__(self, chain_config: list[dict[str, Any]]):
        self._links: list[tuple[ChainLink, CompressionBackend | None]] = []

        for entry in chain_config:
            backend_name = entry.get("backend", "")
            apply_to = frozenset(entry.get("apply_to", []))
            # Everything except "backend" and "apply_to" is passed to the backend
            backend_kwargs = {
                k: v for k, v in entry.items()
                if k not in ("backend", "apply_to")
            }

            link = ChainLink(
                backend_name=backend_name,
                apply_to=apply_to,
                backend_kwargs=backend_kwargs,
            )

            backend_cls = compression_registry.get(backend_name)
            if backend_cls is None:
                available = ", ".join(compression_registry.available_names()) or "(none)"
                logger.warning(
                    "[chain] unknown backend %r, skipping. Available: %s",
                    backend_name,
                    available,
                )
                self._links.append((link, None))
            else:
                try:
                    instance = backend_cls(**backend_kwargs)
                    self._links.append((link, instance))
                except Exception as exc:
                    logger.warning(
                        "[chain] failed to create backend %r: %s",
                        backend_name,
                        exc,
                    )
                    self._links.append((link, None))

        active = [link.backend_name for link, inst in self._links if inst and inst.available]
        logger.info(
            "[chain] compression chain initialized: %s",
            " → ".join(active) if active else "(empty)",
        )

    @property
    def available(self) -> bool:
        """At least one backend in the chain is available."""
        return any(inst is not None and inst.available for _, inst in self._links)

    def compress_messages(self, messages: list[dict[str, Any]]) -> CompressResult:
        """Run messages through each backend in the chain sequentially.

        Returns an aggregated ``CompressResult`` with combined stats.
        """
        if not self.available:
            return CompressResult(messages=messages)

        current_messages = messages
        total_original = 0
        total_compressed = 0
        chain_stats: list[dict[str, Any]] = []
        first_backend = True

        for link, backend in self._links:
            if backend is None or not backend.available:
                continue

            # Filter messages by role if apply_to is specified
            if link.apply_to:
                # Split messages: those matching apply_to and those not
                target_messages = []
                passthrough_indices: set[int] = set()

                for i, msg in enumerate(current_messages):
                    role = msg.get("role", "")
                    msg_type = msg.get("type", "")
                    if role in link.apply_to or msg_type in link.apply_to:
                        target_messages.append(msg)
                    else:
                        passthrough_indices.add(i)

                if not target_messages:
                    logger.debug(
                        "[chain] %s: no messages match apply_to=%s, skipping",
                        link.backend_name,
                        sorted(link.apply_to),
                    )
                    continue

                # Compress only the matching messages
                result = backend.compress_messages(target_messages)

                # Reassemble: merge compressed messages back in order
                compressed_iter = iter(result.messages)
                reassembled = []
                for i, msg in enumerate(current_messages):
                    if i in passthrough_indices:
                        reassembled.append(msg)
                    else:
                        reassembled.append(next(compressed_iter, msg))

                current_messages = reassembled
            else:
                # No role filter — compress all messages
                result = backend.compress_messages(current_messages)
                current_messages = result.messages

            # Track stats
            if first_backend:
                total_original = result.original_tokens
                first_backend = False
            total_compressed = result.compressed_tokens

            if result.reduction_pct > 0:
                chain_stats.append({
                    "backend": link.backend_name,
                    "apply_to": sorted(link.apply_to) if link.apply_to else ["*"],
                    "original_tokens": result.original_tokens,
                    "compressed_tokens": result.compressed_tokens,
                    "reduction_pct": result.reduction_pct,
                    "stats": result.stats,
                })

                logger.info(
                    "[chain] %s: %d → %d tokens (%.1f%% reduction, apply_to=%s)",
                    link.backend_name,
                    result.original_tokens,
                    result.compressed_tokens,
                    result.reduction_pct,
                    sorted(link.apply_to) if link.apply_to else ["*"],
                )

        # Calculate overall reduction
        if total_original > 0 and total_compressed > 0:
            overall_reduction = (1 - total_compressed / total_original) * 100
        elif chain_stats:
            # If we only had role-filtered backends, sum up their reductions
            overall_reduction = sum(s["reduction_pct"] for s in chain_stats) / len(chain_stats)
        else:
            overall_reduction = 0.0

        return CompressResult(
            messages=current_messages,
            original_tokens=total_original,
            compressed_tokens=total_compressed,
            reduction_pct=overall_reduction,
            stats={
                "chain": chain_stats,
                "backends_run": len(chain_stats),
            },
        )
