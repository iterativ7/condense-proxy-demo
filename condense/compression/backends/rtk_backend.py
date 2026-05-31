"""RTK compression backend.

Shells out to the ``rtk pipe`` command to compress tool-output messages
(``role: "tool"`` or ``type: "tool_result"``).  RTK auto-detects the
content type and applies the best structural filter — git diffs, test
output, grep results, build logs, etc.

Install:  ``pip install condense[rtk]``   (wraps the ``rtk-py`` package)

Philosophy 4 alignment:
- TRANSPARENT — we auto-detect content type in Python and log it *before*
  calling RTK, so every decision is visible.
- COMPOSABLE — this is just another ``CompressionBackend`` in the chain.
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
from condense.compression.backends.rtk_detect import detect_content_type

logger = logging.getLogger(__name__)

# Roles whose content should be compressed by RTK.
_TOOL_ROLES = frozenset({"tool"})

# Maximum content length we'll send to RTK pipe (safety bound).
_MAX_CONTENT_LENGTH = 500_000  # 500 KB

# Subprocess timeout in seconds.
_RTK_TIMEOUT = 10


def _rtk_binary_path() -> str | None:
    """Return the path to the ``rtk`` binary, or ``None``.

    Checks both the system PATH and the current Python environment's
    bin/Scripts directory (for ``pip install rtk-py`` into a venv).
    """
    # 1. Check system PATH
    path = shutil.which("rtk")
    if path:
        return path

    # 2. Check the current Python environment's bin directory
    #    (rtk-py installs the binary there via pip)
    env_bin = Path(sys.executable).parent / "rtk"
    if env_bin.exists() and env_bin.is_file():
        return str(env_bin)

    # 3. Windows: Scripts/rtk.exe
    env_scripts = Path(sys.executable).parent / "rtk.exe"
    if env_scripts.exists() and env_scripts.is_file():
        return str(env_scripts)

    return None


def _is_tool_message(msg: dict[str, Any]) -> bool:
    """Check if a message is a tool-output message that RTK should compress.

    Handles three API formats:
    - OpenAI chat: ``{"role": "tool", "content": "..."}``
    - Anthropic:   ``{"role": "user", "content": [{"type": "tool_result", ...}]}``
    - Responses API: ``{"type": "function_call_output", "output": "..."}``
    """
    # OpenAI chat format
    if msg.get("role") in _TOOL_ROLES:
        return True

    # Responses API format
    if msg.get("type") == "function_call_output":
        return True

    # Anthropic format — check for tool_result blocks in content
    content = msg.get("content")
    if isinstance(content, list):
        return any(
            isinstance(block, dict) and block.get("type") == "tool_result"
            for block in content
        )

    return False


def _extract_text(msg: dict[str, Any]) -> str:
    """Extract the text content from a tool message."""
    # Responses API
    if msg.get("type") == "function_call_output":
        return str(msg.get("output", ""))

    content = msg.get("content", "")

    # String content (OpenAI tool format)
    if isinstance(content, str):
        return content

    # Anthropic structured blocks — extract tool_result text
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "tool_result":
                inner = block.get("content", "")
                if isinstance(inner, str):
                    parts.append(inner)
                elif isinstance(inner, list):
                    for sub in inner:
                        if isinstance(sub, dict) and sub.get("type") == "text":
                            parts.append(sub.get("text", ""))
            elif isinstance(block, dict) and block.get("type") == "text":
                parts.append(block.get("text", ""))
        return "\n".join(parts)

    return str(content) if content else ""


def _replace_text(msg: dict[str, Any], new_text: str) -> dict[str, Any]:
    """Return a copy of *msg* with the text content replaced."""
    out = msg.copy()

    # Responses API
    if out.get("type") == "function_call_output":
        out["output"] = new_text
        return out

    content = out.get("content", "")

    # String content
    if isinstance(content, str):
        out["content"] = new_text
        return out

    # Anthropic structured blocks — replace tool_result text
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


def _run_rtk_pipe(text: str, filter_name: str | None = None, binary: str | None = None) -> str | None:
    """Run ``rtk pipe`` on *text* and return the filtered output.

    Returns ``None`` on any failure (timeout, missing binary, etc.).
    """
    rtk_bin = binary or _rtk_binary_path() or "rtk"
    cmd = [rtk_bin, "pipe"]
    if filter_name:
        cmd.extend(["--filter", filter_name])

    try:
        proc = subprocess.run(
            cmd,
            input=text,
            capture_output=True,
            text=True,
            timeout=_RTK_TIMEOUT,
        )
        if proc.returncode == 0:
            return proc.stdout
        logger.debug("[rtk] pipe returned exit code %d: %s", proc.returncode, proc.stderr[:200])
        return None
    except FileNotFoundError:
        logger.debug("[rtk] binary not found in PATH")
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

    def __init__(
        self,
        *,
        apply_to: list[str] | None = None,
        timeout: int = _RTK_TIMEOUT,
        **kwargs: Any,
    ):
        self._apply_to = frozenset(apply_to) if apply_to else _TOOL_ROLES
        self._timeout = timeout
        self._binary_path = _rtk_binary_path()
        self._warned = False

        if self._binary_path:
            logger.info(
                "RTK backend loaded (binary=%s, apply_to=%s)",
                self._binary_path,
                sorted(self._apply_to),
            )
        else:
            logger.info(
                "RTK backend: binary not found. "
                "Install with: pip install condense[rtk]"
            )

    @property
    def available(self) -> bool:
        """RTK is available if the binary is in PATH."""
        if self._binary_path is None:
            # Re-check in case it was installed after init
            self._binary_path = _rtk_binary_path()
        return self._binary_path is not None

    def compress_messages(self, messages: list[dict[str, Any]]) -> CompressResult:
        """Compress tool-output messages via RTK pipe.

        Non-tool messages are passed through unchanged.  For each tool
        message we:

        1. Auto-detect the content type (Python-side, for transparency).
        2. Shell out to ``rtk pipe --filter=<name>``.
        3. Apply safety invariant: never grow, never empty.
        4. Log the filter name and savings.
        """
        if not self.available:
            if not self._warned:
                logger.warning(
                    "[rtk] backend unavailable — skipping tool-output compression. "
                    "Install with: pip install condense[rtk]"
                )
                self._warned = True
            return CompressResult(messages=messages)

        compressed_messages = []
        total_original_chars = 0
        total_compressed_chars = 0
        filter_stats: list[dict[str, Any]] = []

        for msg in messages:
            if not _is_tool_message(msg):
                compressed_messages.append(msg)
                continue

            text = _extract_text(msg)

            # Skip very short or very long content
            if len(text) < 50 or len(text) > _MAX_CONTENT_LENGTH:
                compressed_messages.append(msg)
                total_original_chars += len(text)
                total_compressed_chars += len(text)
                continue

            # Auto-detect content type (transparent — we log this)
            detected = detect_content_type(text)

            # Shell out to RTK
            filtered = _run_rtk_pipe(text, filter_name=detected, binary=self._binary_path)

            # Safety invariant: never grow, never empty
            if filtered and 0 < len(filtered.rstrip()) < len(text):
                new_msg = _replace_text(msg, filtered.rstrip())
                compressed_messages.append(new_msg)
                original_len = len(text)
                compressed_len = len(filtered.rstrip())
                reduction = (1 - compressed_len / original_len) * 100

                total_original_chars += original_len
                total_compressed_chars += compressed_len

                filter_stats.append({
                    "filter": detected or "auto",
                    "original_chars": original_len,
                    "compressed_chars": compressed_len,
                    "reduction_pct": round(reduction, 1),
                })

                logger.info(
                    "[rtk] %s filter applied: %d → %d chars (%.1f%% reduction)",
                    detected or "auto",
                    original_len,
                    compressed_len,
                    reduction,
                )
            else:
                # Pass through unchanged
                compressed_messages.append(msg)
                total_original_chars += len(text)
                total_compressed_chars += len(text)

                if detected:
                    logger.debug(
                        "[rtk] %s filter detected but output was not smaller — keeping original",
                        detected,
                    )

        # Calculate overall stats
        if total_original_chars > 0:
            overall_reduction = (1 - total_compressed_chars / total_original_chars) * 100
        else:
            overall_reduction = 0.0

        return CompressResult(
            messages=compressed_messages,
            original_tokens=total_original_chars,  # chars as proxy for tokens
            compressed_tokens=total_compressed_chars,
            reduction_pct=overall_reduction,
            stats={
                "backend": "rtk",
                "filters_applied": filter_stats,
                "messages_processed": len([m for m in messages if _is_tool_message(m)]),
            },
        )
