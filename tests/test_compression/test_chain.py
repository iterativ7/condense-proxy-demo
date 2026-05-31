"""Tests for compression chain (P0-13 fix).

Tests verify that multiple backends can run back-to-back, each
targeting specific message roles.
"""

from unittest.mock import MagicMock, patch

import pytest

from condense.compression.base import CompressionBackend, CompressResult, compression_registry
from condense.compression.chain import CompressionChain
from condense.compression.compressor import Compressor


# -----------------------------------------------------------------------
# Helpers — mock backends that just transform content predictably
# -----------------------------------------------------------------------

class _MockToolBackend(CompressionBackend):
    """Mock backend that prefixes tool content with [TOOL_COMPRESSED]."""

    @property
    def available(self) -> bool:
        return True

    def compress_messages(self, messages):
        compressed = []
        original_chars = 0
        compressed_chars = 0
        for msg in messages:
            content = msg.get("content", "")
            original_chars += len(content)
            new_content = f"[TOOL_COMPRESSED]{content[:20]}"
            compressed_chars += len(new_content)
            compressed.append({**msg, "content": new_content})
        reduction = (1 - compressed_chars / max(original_chars, 1)) * 100
        return CompressResult(
            messages=compressed,
            original_tokens=original_chars,
            compressed_tokens=compressed_chars,
            reduction_pct=max(0, reduction),
        )


class _MockUserBackend(CompressionBackend):
    """Mock backend that prefixes user content with [USER_COMPRESSED]."""

    @property
    def available(self) -> bool:
        return True

    def compress_messages(self, messages):
        compressed = []
        original_chars = 0
        compressed_chars = 0
        for msg in messages:
            content = msg.get("content", "")
            original_chars += len(content)
            new_content = f"[USER_COMPRESSED]{content[:20]}"
            compressed_chars += len(new_content)
            compressed.append({**msg, "content": new_content})
        reduction = (1 - compressed_chars / max(original_chars, 1)) * 100
        return CompressResult(
            messages=compressed,
            original_tokens=original_chars,
            compressed_tokens=compressed_chars,
            reduction_pct=max(0, reduction),
        )


class _MockUnavailableBackend(CompressionBackend):
    @property
    def available(self) -> bool:
        return False

    def compress_messages(self, messages):
        return CompressResult(messages=messages)


# -----------------------------------------------------------------------
# CompressionChain tests
# -----------------------------------------------------------------------

class TestCompressionChain:

    def _make_chain_with_mocks(self, chain_config, mock_map):
        """Build a chain with mocked registry lookups."""
        def fake_get(name):
            return mock_map.get(name)

        with patch.object(compression_registry, "get", side_effect=fake_get):
            return CompressionChain(chain_config)

    def test_single_backend_no_filter(self):
        """A chain with one backend and no apply_to processes all messages."""
        chain = self._make_chain_with_mocks(
            [{"backend": "mock_tool"}],
            {"mock_tool": _MockToolBackend},
        )
        messages = [
            {"role": "user", "content": "hello world this is a long message"},
            {"role": "tool", "content": "some tool output here for testing"},
        ]
        result = chain.compress_messages(messages)
        assert "[TOOL_COMPRESSED]" in result.messages[0]["content"]
        assert "[TOOL_COMPRESSED]" in result.messages[1]["content"]

    def test_role_filtering(self):
        """Backend with apply_to only processes matching messages."""
        chain = self._make_chain_with_mocks(
            [{"backend": "mock_tool", "apply_to": ["tool"]}],
            {"mock_tool": _MockToolBackend},
        )
        messages = [
            {"role": "user", "content": "hello"},
            {"role": "tool", "content": "tool output data here for compression"},
        ]
        result = chain.compress_messages(messages)

        # User message untouched
        assert result.messages[0]["content"] == "hello"
        # Tool message compressed
        assert "[TOOL_COMPRESSED]" in result.messages[1]["content"]

    def test_two_backends_different_roles(self):
        """Two backends targeting different roles both run."""
        chain = self._make_chain_with_mocks(
            [
                {"backend": "mock_tool", "apply_to": ["tool"]},
                {"backend": "mock_user", "apply_to": ["user"]},
            ],
            {
                "mock_tool": _MockToolBackend,
                "mock_user": _MockUserBackend,
            },
        )
        messages = [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "Please run the tests for me now"},
            {"role": "tool", "content": "running 42 tests... all passed ok"},
        ]
        result = chain.compress_messages(messages)

        # System message untouched (no backend targets it)
        assert result.messages[0]["content"] == "You are helpful."
        # User message compressed by mock_user
        assert "[USER_COMPRESSED]" in result.messages[1]["content"]
        # Tool message compressed by mock_tool
        assert "[TOOL_COMPRESSED]" in result.messages[2]["content"]

    def test_chain_order_matters(self):
        """Backends run in order — first backend's output is second's input."""
        chain = self._make_chain_with_mocks(
            [
                {"backend": "mock_tool"},   # runs first on all
                {"backend": "mock_user"},   # runs second on all
            ],
            {
                "mock_tool": _MockToolBackend,
                "mock_user": _MockUserBackend,
            },
        )
        messages = [{"role": "user", "content": "a" * 100}]
        result = chain.compress_messages(messages)

        # Should see both prefixes — USER_COMPRESSED wrapping TOOL_COMPRESSED
        assert "[USER_COMPRESSED]" in result.messages[0]["content"]
        assert "[TOOL_COMPRESSED]" in result.messages[0]["content"]

    def test_unavailable_backend_skipped(self):
        """Unavailable backends are skipped gracefully."""
        chain = self._make_chain_with_mocks(
            [
                {"backend": "unavailable"},
                {"backend": "mock_tool"},
            ],
            {
                "unavailable": _MockUnavailableBackend,
                "mock_tool": _MockToolBackend,
            },
        )
        assert chain.available  # mock_tool is available

        messages = [{"role": "tool", "content": "x" * 100}]
        result = chain.compress_messages(messages)
        assert "[TOOL_COMPRESSED]" in result.messages[0]["content"]

    def test_unknown_backend_skipped(self):
        """Unknown backend names are skipped with a warning."""
        chain = self._make_chain_with_mocks(
            [
                {"backend": "nonexistent_xyz"},
                {"backend": "mock_tool"},
            ],
            {"mock_tool": _MockToolBackend},
        )
        assert chain.available

        messages = [{"role": "tool", "content": "x" * 100}]
        result = chain.compress_messages(messages)
        assert "[TOOL_COMPRESSED]" in result.messages[0]["content"]

    def test_empty_chain(self):
        """Empty chain returns original messages."""
        chain = self._make_chain_with_mocks([], {})
        assert not chain.available

        messages = [{"role": "user", "content": "hello"}]
        result = chain.compress_messages(messages)
        assert result.messages == messages

    def test_all_unavailable_returns_original(self):
        """If all backends are unavailable, return original."""
        chain = self._make_chain_with_mocks(
            [{"backend": "unavailable"}],
            {"unavailable": _MockUnavailableBackend},
        )
        assert not chain.available

        messages = [{"role": "tool", "content": "x" * 100}]
        result = chain.compress_messages(messages)
        assert result.messages == messages

    def test_no_matching_messages_skips_backend(self):
        """Backend is skipped when no messages match its apply_to."""
        chain = self._make_chain_with_mocks(
            [{"backend": "mock_tool", "apply_to": ["tool"]}],
            {"mock_tool": _MockToolBackend},
        )
        messages = [
            {"role": "user", "content": "hello"},
            {"role": "system", "content": "prompt"},
        ]
        result = chain.compress_messages(messages)

        # All messages unchanged
        assert result.messages[0]["content"] == "hello"
        assert result.messages[1]["content"] == "prompt"

    def test_message_order_preserved(self):
        """Message order is preserved after role-filtered compression."""
        chain = self._make_chain_with_mocks(
            [{"backend": "mock_tool", "apply_to": ["tool"]}],
            {"mock_tool": _MockToolBackend},
        )
        messages = [
            {"role": "user", "content": "step 1"},
            {"role": "tool", "content": "output 1 with enough chars to compress"},
            {"role": "user", "content": "step 2"},
            {"role": "tool", "content": "output 2 with enough chars to compress"},
            {"role": "user", "content": "step 3"},
        ]
        result = chain.compress_messages(messages)

        assert len(result.messages) == 5
        assert result.messages[0]["content"] == "step 1"
        assert "[TOOL_COMPRESSED]" in result.messages[1]["content"]
        assert result.messages[2]["content"] == "step 2"
        assert "[TOOL_COMPRESSED]" in result.messages[3]["content"]
        assert result.messages[4]["content"] == "step 3"

    def test_stats_include_chain_info(self):
        """Result stats should include per-backend chain info."""
        chain = self._make_chain_with_mocks(
            [{"backend": "mock_tool", "apply_to": ["tool"]}],
            {"mock_tool": _MockToolBackend},
        )
        messages = [{"role": "tool", "content": "x" * 100}]
        result = chain.compress_messages(messages)

        assert "chain" in result.stats
        assert "backends_run" in result.stats


# -----------------------------------------------------------------------
# Compressor chain integration
# -----------------------------------------------------------------------

class TestCompressorChainMode:
    """Test that Compressor correctly delegates to CompressionChain."""

    def test_chain_mode_activated(self):
        """Passing chain= activates chain mode."""
        with patch.object(compression_registry, "get", return_value=_MockToolBackend):
            compressor = Compressor(chain=[{"backend": "mock_tool"}])
        assert compressor.available
        assert compressor.compressor_type == "chain"

    def test_chain_compress(self):
        """Chain mode compresses messages correctly."""
        with patch.object(compression_registry, "get", return_value=_MockToolBackend):
            compressor = Compressor(chain=[{"backend": "mock_tool"}])

        messages = [{"role": "tool", "content": "x" * 100}]
        result = compressor.compress_messages(messages)
        assert "[TOOL_COMPRESSED]" in result.messages[0]["content"]

    def test_single_mode_still_works(self):
        """Single backend mode (no chain) still works as before."""
        mock = MagicMock(spec=CompressionBackend)
        mock.available = True
        mock.compress_messages.return_value = CompressResult(
            messages=[{"role": "user", "content": "compressed"}],
            original_tokens=100,
            compressed_tokens=50,
            reduction_pct=50.0,
        )
        mock_cls = MagicMock(return_value=mock)

        with patch.object(compression_registry, "get", return_value=mock_cls):
            compressor = Compressor(compressor_type="mock")

        result = compressor.compress_messages([{"role": "user", "content": "original"}])
        assert result.compressed_tokens == 50
