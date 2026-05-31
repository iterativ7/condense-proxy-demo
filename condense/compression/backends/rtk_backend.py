"""RTK compression backend.

Shells out to ``rtk pipe`` to compress tool-output messages.  RTK
auto-detects the content type (git diffs, test output, grep results,
build logs, etc.) and applies the best structural filter.

Install:  ``pip install condense[rtk]``

Philosophy 4 alignment:
- TRANSPARENT — we log before/after savings for every compressed message.
- COMPOSABLE — just another ``CompressionBackend`` in the chain.
- DEFAULTS — optional dep with graceful degradation.
- SAFE — never grow, never empty; on any failure, pass through unchanged.

See also:  https://github.com/rtk-ai/rtk
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from condense.compression.base import (
    CompressionBackend,
    CompressResult,
    compression_registry,
)

logger = logging.getLogger(__name__)

_TOOL_ROLES = frozenset({"tool"})
_MAX_CONTENT_LENGTH = 500_000  # 500 KB
_MIN_CONTENT_LENGTH = 50  # not worth compressing
_RTK_TIMEOUT = 10


def _rtk_binary_path() -> str | None:
    """Return the path to the ``rtk`` binary, or ``None``.

    Checks both the system PATH and the current Python environment's
    bin/Scripts directory (for ``pip install rtk-py`` into a venv).
    """
    path = shutil.which("rtk")
    if path:
        return path

    # rtk-py installs the binary into the venv's bin directory
    for name in ("rtk", "rtk.exe"):
        candidate = Path(sys.executable).parent / name
        if candidate.is_file():
            return str(candidate)

    return None


def _is_tool_message(msg: dict[str, Any]) -> bool:
    """Check if a message is tool output that RTK should compress.

    Handles three API formats:
    - OpenAI chat: ``{"role": "tool", ...}``
    - Anthropic:   ``{"role": "user", "content": [{"type": "tool_result", ...}]}``
    - Responses API: ``{"type": "function_call_output", ...}``
    """
    if msg.get("role") in _TOOL_ROLES:
        return True
    if msg.get("type") == "function_call_output":
        return True
    content = msg.get("content")
    if isinstance(content, list):
        return any(
            isinstance(b, dict) and b.get("type") == "tool_result"
            for b in content
        )
    return False


def _extract_text(msg: dict[str, Any]) -> str:
    """Extract text content from a tool message."""
    if msg.get("type") == "function_call_output":
        return str(msg.get("output", ""))

    content = msg.get("content", "")
    if isinstance(content, str):
        return content

    if isinstance(content, list):
        parts = []
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "tool_result":
                inner = block.get("content", "")
                if isinstance(inner, str):
                    parts.append(inner)
                elif isinstance(inner, list):
                    parts.extend(
                        s.get("text", "") for s in inner
                        if isinstance(s, dict) and s.get("type") == "text"
                    )
            elif block.get("type") == "text":
                parts.append(block.get("text", ""))
        return "\n".join(parts)

    return str(content) if content else ""


def _replace_text(msg: dict[str, Any], new_text: str) -> dict[str, Any]:
    """Return a copy of *msg* with text content replaced."""
    out = msg.copy()

    if out.get("type") == "function_call_output":
        out["output"] = new_text
        return out

    content = out.get("content", "")
    if isinstance(content, str):
        out["content"] = new_text
        return out

    if isinstance(content, list):
        new_blocks = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "tool_result":
                new_block = block.copy()
                inner = block.get("content", "")
                if isinstance(inner, str):
                    new_block["content"] = new_text
                elif isinstance(inner, list):
                    new_block["content"] = [{"type": "text", "text": new_text}]
                new_blocks.append(new_block)
            else:
                new_blocks.append(block)
        out["content"] = new_blocks
        return out

    out["content"] = new_text
    return out


def _run_rtk_pipe(text: str, binary: str | None = None) -> str | None:
    """Run ``rtk pipe`` on *text*.  Returns filtered output or ``None``."""
    rtk_bin = binary or "rtk"
    try:
        proc = subprocess.run(
            [rtk_bin, "pipe"],
            input=text,
            capture_output=True,
            text=True,
            timeout=_RTK_TIMEOUT,
        )
        if proc.returncode == 0:
            return proc.stdout
        logger.debug("[rtk] pipe exit code %d: %s", proc.returncode, proc.stderr[:200])
        return None
    except FileNotFoundError:
        return None
    except subprocess.TimeoutExpired:
        logger.warning("[rtk] pipe timed out after %ds", _RTK_TIMEOUT)
        return None
    except Exception as exc:
        logger.warning("[rtk] pipe failed: %s", exc)
        return None


@compression_registry.register("rtk")
class RTKCompressionBackend(CompressionBackend):
    """Compress tool-output messages via the RTK binary.

    Parameters
    ----------
    apply_to : list[str]
        Message roles to compress.  Default: ``["tool"]``.
    timeout : int
        Subprocess timeout in seconds.  Default: 10.
    """

    def __init__(self, *, apply_to: list[str] | None = None, timeout: int = _RTK_TIMEOUT, **kwargs: Any):
        self._apply_to = frozenset(apply_to) if apply_to else _TOOL_ROLES
        self._timeout = timeout
        self._binary_path = _rtk_binary_path()
        self._warned = False

        if self._binary_path:
            logger.info("RTK backend loaded (binary=%s)", self._binary_path)
        else:
            logger.info("RTK backend: binary not found. Install with: pip install condense[rtk]")

    @property
    def available(self) -> bool:
        if self._binary_path is None:
            self._binary_path = _rtk_binary_path()
        return self._binary_path is not None

    def compress_messages(self, messages: list[dict[str, Any]]) -> CompressResult:
        if not self.available:
            if not self._warned:
                logger.warning("[rtk] unavailable — install with: pip install condense[rtk]")
                self._warned = True
            return CompressResult(messages=messages)

        compressed = []
        total_original = 0
        total_compressed = 0
        messages_compressed = 0

        for msg in messages:
            if not _is_tool_message(msg):
                compressed.append(msg)
                continue

            text = _extract_text(msg)
            original_len = len(text)
            total_original += original_len

            # Skip content that's too short or too long
            if original_len < _MIN_CONTENT_LENGTH or original_len > _MAX_CONTENT_LENGTH:
                compressed.append(msg)
                total_compressed += original_len
                continue

            # Let RTK auto-detect and filter
            filtered = _run_rtk_pipe(text, binary=self._binary_path)

            # Safety: never grow, never empty
            if filtered and 0 < len(filtered.rstrip()) < original_len:
                filtered = filtered.rstrip()
                compressed.append(_replace_text(msg, filtered))
                total_compressed += len(filtered)
                messages_compressed += 1
                reduction = (1 - len(filtered) / original_len) * 100
                logger.info("[rtk] compressed: %d → %d chars (%.1f%%)", original_len, len(filtered), reduction)
            else:
                compressed.append(msg)
                total_compressed += original_len

        overall = (1 - total_compressed / total_original) * 100 if total_original > 0 else 0.0

        return CompressResult(
            messages=compressed,
            original_tokens=total_original,
            compressed_tokens=total_compressed,
            reduction_pct=overall,
            stats={"backend": "rtk", "messages_compressed": messages_compressed},
        )
