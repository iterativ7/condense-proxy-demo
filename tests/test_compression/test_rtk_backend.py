"""Tests for RTK compression backend.

Tests use mocked subprocess calls — no RTK binary needed.
The integration test (test_rtk_integration) tests with the real binary.
"""

from unittest.mock import MagicMock, patch, PropertyMock
import subprocess

import pytest

from condense.compression.base import CompressionBackend, CompressResult, compression_registry
from condense.compression.backends.rtk_backend import (
    RTKCompressionBackend,
    _is_tool_message,
    _extract_text,
    _replace_text,
    _run_rtk_pipe,
)


# -----------------------------------------------------------------------
# Registration
# -----------------------------------------------------------------------

class TestRTKRegistration:
    def test_rtk_registered(self):
        assert "rtk" in compression_registry.available_names()

    def test_rtk_is_compression_backend(self):
        cls = compression_registry.get("rtk")
        assert cls is not None
        assert issubclass(cls, CompressionBackend)


# -----------------------------------------------------------------------
# Tool message detection
# -----------------------------------------------------------------------

class TestIsToolMessage:
    def test_openai_tool_role(self):
        assert _is_tool_message({"role": "tool", "content": "output"}) is True

    def test_user_role(self):
        assert _is_tool_message({"role": "user", "content": "hello"}) is False

    def test_assistant_role(self):
        assert _is_tool_message({"role": "assistant", "content": "hi"}) is False

    def test_system_role(self):
        assert _is_tool_message({"role": "system", "content": "prompt"}) is False

    def test_responses_api_function_call_output(self):
        assert _is_tool_message({"type": "function_call_output", "output": "result"}) is True

    def test_anthropic_tool_result_block(self):
        msg = {
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": "123", "content": "output"},
            ],
        }
        assert _is_tool_message(msg) is True

    def test_anthropic_text_block_not_tool(self):
        msg = {
            "role": "user",
            "content": [{"type": "text", "text": "hello"}],
        }
        assert _is_tool_message(msg) is False


# -----------------------------------------------------------------------
# Text extraction
# -----------------------------------------------------------------------

class TestExtractText:
    def test_string_content(self):
        assert _extract_text({"role": "tool", "content": "output data"}) == "output data"

    def test_responses_api(self):
        assert _extract_text({"type": "function_call_output", "output": "result"}) == "result"

    def test_empty_content(self):
        assert _extract_text({"role": "tool", "content": ""}) == ""

    def test_anthropic_tool_result_string(self):
        msg = {
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": "1", "content": "tool output"},
            ],
        }
        assert _extract_text(msg) == "tool output"

    def test_anthropic_tool_result_blocks(self):
        msg = {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "1",
                    "content": [{"type": "text", "text": "filtered output"}],
                },
            ],
        }
        assert _extract_text(msg) == "filtered output"


# -----------------------------------------------------------------------
# Text replacement
# -----------------------------------------------------------------------

class TestReplaceText:
    def test_string_content(self):
        msg = {"role": "tool", "content": "old"}
        result = _replace_text(msg, "new")
        assert result["content"] == "new"
        assert result["role"] == "tool"
        # Original unchanged
        assert msg["content"] == "old"

    def test_responses_api(self):
        msg = {"type": "function_call_output", "output": "old"}
        result = _replace_text(msg, "new")
        assert result["output"] == "new"

    def test_anthropic_string_content(self):
        msg = {
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": "1", "content": "old"},
            ],
        }
        result = _replace_text(msg, "new")
        assert result["content"][0]["content"] == "new"

    def test_anthropic_blocks_content(self):
        msg = {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "1",
                    "content": [{"type": "text", "text": "old"}],
                },
            ],
        }
        result = _replace_text(msg, "new")
        assert result["content"][0]["content"] == [{"type": "text", "text": "new"}]


# -----------------------------------------------------------------------
# RTK pipe subprocess (mocked)
# -----------------------------------------------------------------------

class TestRunRTKPipe:
    @patch("condense.compression.backends.rtk_backend.subprocess.run")
    def test_success(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(
            args=["rtk", "pipe"],
            returncode=0,
            stdout="compressed output",
            stderr="",
        )
        result = _run_rtk_pipe("original long output text")
        assert result == "compressed output"
        mock_run.assert_called_once()

    @patch("condense.compression.backends.rtk_backend.subprocess.run")
    def test_nonzero_exit(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(
            args=["rtk", "pipe"],
            returncode=1,
            stdout="",
            stderr="error",
        )
        assert _run_rtk_pipe("text") is None

    @patch("condense.compression.backends.rtk_backend.subprocess.run")
    def test_timeout(self, mock_run):
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="rtk", timeout=10)
        assert _run_rtk_pipe("text") is None

    @patch("condense.compression.backends.rtk_backend.subprocess.run")
    def test_binary_not_found(self, mock_run):
        mock_run.side_effect = FileNotFoundError()
        assert _run_rtk_pipe("text") is None


# -----------------------------------------------------------------------
# RTKCompressionBackend — full message-level tests (mocked binary)
# -----------------------------------------------------------------------

class TestRTKCompressionBackend:
    @patch("condense.compression.backends.rtk_backend._rtk_binary_path", return_value="/usr/bin/rtk")
    def _make_backend(self, mock_path):
        return RTKCompressionBackend()

    @patch("condense.compression.backends.rtk_backend._rtk_binary_path", return_value="/usr/bin/rtk")
    def test_available_when_binary_exists(self, mock_path):
        backend = RTKCompressionBackend()
        assert backend.available is True

    @patch("condense.compression.backends.rtk_backend._rtk_binary_path", return_value=None)
    def test_unavailable_when_no_binary(self, mock_path):
        backend = RTKCompressionBackend()
        assert backend.available is False

    @patch("condense.compression.backends.rtk_backend._rtk_binary_path", return_value=None)
    def test_unavailable_returns_original(self, mock_path):
        backend = RTKCompressionBackend()
        messages = [{"role": "tool", "content": "some output"}]
        result = backend.compress_messages(messages)
        assert result.messages == messages

    @patch("condense.compression.backends.rtk_backend._run_rtk_pipe")
    @patch("condense.compression.backends.rtk_backend._rtk_binary_path", return_value="/usr/bin/rtk")
    def test_compresses_tool_messages(self, mock_path, mock_pipe):
        long_content = "x" * 200  # > 50 chars to pass minimum threshold
        short_content = "y" * 100
        mock_pipe.return_value = short_content

        backend = RTKCompressionBackend()
        messages = [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "Run the tests"},
            {"role": "tool", "content": long_content},
        ]
        result = backend.compress_messages(messages)

        # System and user messages should be unchanged
        assert result.messages[0] == messages[0]
        assert result.messages[1] == messages[1]
        # Tool message should be compressed
        assert result.messages[2]["content"] == short_content
        assert result.reduction_pct > 0

    @patch("condense.compression.backends.rtk_backend._run_rtk_pipe")
    @patch("condense.compression.backends.rtk_backend._rtk_binary_path", return_value="/usr/bin/rtk")
    def test_safety_never_grow(self, mock_path, mock_pipe):
        """If RTK output is larger than input, keep original (safety invariant)."""
        original = "a" * 100
        mock_pipe.return_value = "b" * 200  # larger!

        backend = RTKCompressionBackend()
        messages = [{"role": "tool", "content": original}]
        result = backend.compress_messages(messages)

        assert result.messages[0]["content"] == original

    @patch("condense.compression.backends.rtk_backend._run_rtk_pipe")
    @patch("condense.compression.backends.rtk_backend._rtk_binary_path", return_value="/usr/bin/rtk")
    def test_safety_never_empty(self, mock_path, mock_pipe):
        """If RTK output is empty, keep original (safety invariant)."""
        mock_pipe.return_value = ""

        backend = RTKCompressionBackend()
        messages = [{"role": "tool", "content": "x" * 100}]
        result = backend.compress_messages(messages)

        assert result.messages[0]["content"] == "x" * 100

    @patch("condense.compression.backends.rtk_backend._run_rtk_pipe")
    @patch("condense.compression.backends.rtk_backend._rtk_binary_path", return_value="/usr/bin/rtk")
    def test_safety_pipe_failure(self, mock_path, mock_pipe):
        """If RTK pipe returns None (failure), keep original."""
        mock_pipe.return_value = None

        backend = RTKCompressionBackend()
        messages = [{"role": "tool", "content": "x" * 100}]
        result = backend.compress_messages(messages)

        assert result.messages[0]["content"] == "x" * 100

    @patch("condense.compression.backends.rtk_backend._run_rtk_pipe")
    @patch("condense.compression.backends.rtk_backend._rtk_binary_path", return_value="/usr/bin/rtk")
    def test_skips_short_content(self, mock_path, mock_pipe):
        """Content under 50 chars is not worth compressing."""
        backend = RTKCompressionBackend()
        messages = [{"role": "tool", "content": "ok"}]
        result = backend.compress_messages(messages)

        mock_pipe.assert_not_called()
        assert result.messages[0]["content"] == "ok"

    @patch("condense.compression.backends.rtk_backend._run_rtk_pipe")
    @patch("condense.compression.backends.rtk_backend._rtk_binary_path", return_value="/usr/bin/rtk")
    def test_multiple_tool_messages(self, mock_path, mock_pipe):
        """Multiple tool messages should each be compressed independently."""
        mock_pipe.side_effect = ["compact1", "compact2"]

        backend = RTKCompressionBackend()
        messages = [
            {"role": "tool", "content": "a" * 100},
            {"role": "user", "content": "What happened?"},
            {"role": "tool", "content": "b" * 100},
        ]
        result = backend.compress_messages(messages)

        assert result.messages[0]["content"] == "compact1"
        assert result.messages[1]["content"] == "What happened?"
        assert result.messages[2]["content"] == "compact2"
        assert mock_pipe.call_count == 2

    @patch("condense.compression.backends.rtk_backend._run_rtk_pipe")
    @patch("condense.compression.backends.rtk_backend._rtk_binary_path", return_value="/usr/bin/rtk")
    def test_stats_include_backend_info(self, mock_path, mock_pipe):
        """Stats should report backend name and messages compressed."""
        mock_pipe.return_value = "short"

        backend = RTKCompressionBackend()
        messages = [{"role": "tool", "content": "running 3 tests\n" + "x" * 200}]
        result = backend.compress_messages(messages)

        assert result.stats["backend"] == "rtk"
        assert result.stats["messages_compressed"] == 1

    @patch("condense.compression.backends.rtk_backend._run_rtk_pipe")
    @patch("condense.compression.backends.rtk_backend._rtk_binary_path", return_value="/usr/bin/rtk")
    def test_responses_api_format(self, mock_path, mock_pipe):
        """Should handle Responses API format (type: function_call_output)."""
        mock_pipe.return_value = "compact"

        backend = RTKCompressionBackend()
        messages = [
            {"type": "function_call_output", "call_id": "call_1", "output": "x" * 100},
        ]
        result = backend.compress_messages(messages)

        assert result.messages[0]["output"] == "compact"
        assert result.messages[0]["type"] == "function_call_output"
        assert result.messages[0]["call_id"] == "call_1"
