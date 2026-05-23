"""Tests for Compressor and the compression backend registry."""

from unittest.mock import MagicMock, patch

import pytest

from condense.compression.base import (
    CompressionBackend,
    CompressResult,
    compression_registry,
)
from condense.compression.compressor import Compressor


# -----------------------------------------------------------------------
# Compression registry
# -----------------------------------------------------------------------

class TestCompressionRegistry:
    """Tests for the compression-specific registry."""

    def test_builtin_backends_registered(self):
        expected = {"fusion", "llmlingua"}
        registered = set(compression_registry.available_names())
        assert expected.issubset(registered), (
            f"Missing backends: {expected - registered}"
        )

    def test_all_backends_are_compression_backends(self):
        for name in compression_registry.available_names():
            cls = compression_registry.get(name)
            assert issubclass(cls, CompressionBackend), (
                f"{name} → {cls} is not a CompressionBackend subclass"
            )


# -----------------------------------------------------------------------
# Compressor with mocked backend
# -----------------------------------------------------------------------

class TestCompressorInit:
    def test_unknown_compressor_type_is_unavailable(self):
        compressor = Compressor(compressor_type="totally_unknown_xyz")
        assert not compressor.available

    def test_compress_returns_original_when_unavailable(self):
        compressor = Compressor(compressor_type="totally_unknown_xyz")
        messages = [{"role": "user", "content": "Hello"}]
        result = compressor.compress_messages(messages)
        assert result.messages == messages
        assert result.original_tokens == 0


class TestCompressorRoute:
    def _make_compressor(self, compress_result):
        mock_backend = MagicMock(spec=CompressionBackend)
        mock_backend.available = True
        mock_backend.compress_messages.return_value = compress_result

        mock_cls = MagicMock(return_value=mock_backend)
        with patch.object(compression_registry, "get", return_value=mock_cls):
            return Compressor(compressor_type="mock")

    def test_compress_returns_result(self):
        expected = CompressResult(
            messages=[{"role": "user", "content": "compressed"}],
            original_tokens=100,
            compressed_tokens=60,
            reduction_pct=40.0,
        )
        compressor = self._make_compressor(expected)
        result = compressor.compress_messages([{"role": "user", "content": "original text"}])
        assert result.compressed_tokens == 60
        assert result.reduction_pct == 40.0

    def test_compress_exception_returns_original(self):
        mock_backend = MagicMock(spec=CompressionBackend)
        mock_backend.available = True
        mock_backend.compress_messages.side_effect = RuntimeError("boom")
        mock_cls = MagicMock(return_value=mock_backend)
        with patch.object(compression_registry, "get", return_value=mock_cls):
            compressor = Compressor(compressor_type="mock")
        messages = [{"role": "user", "content": "Hello"}]
        result = compressor.compress_messages(messages)
        assert result.messages == messages


# -----------------------------------------------------------------------
# CompressResult
# -----------------------------------------------------------------------

class TestCompressResult:
    def test_defaults(self):
        r = CompressResult(messages=[])
        assert r.original_tokens == 0
        assert r.compressed_tokens == 0
        assert r.reduction_pct == 0.0
        assert r.stats == {}


# -----------------------------------------------------------------------
# Real FusionEngine tests (require claw-compactor)
# -----------------------------------------------------------------------

class TestFusionBackendReal:
    """Integration tests with real FusionEngine."""

    @pytest.fixture(autouse=True)
    def _check_claw(self):
        try:
            from claw_compactor.fusion.engine import FusionEngine  # noqa: F401
        except ImportError:
            pytest.skip("claw-compactor not installed")

    def test_fusion_available(self):
        compressor = Compressor(compressor_type="fusion", aggressive=True)
        assert compressor.available

    def test_fusion_compresses_code(self):
        """FusionEngine should compress code content."""
        compressor = Compressor(compressor_type="fusion", aggressive=True)
        messages = [{
            "role": "user",
            "content": (
                "Review this code:\n"
                "def fibonacci(n):\n"
                "    # Calculate the nth Fibonacci number\n"
                "    # using dynamic programming approach\n"
                "    if n <= 0:\n"
                "        return 0\n"
                "    elif n == 1:\n"
                "        return 1\n"
                "    # Initialize the first two numbers\n"
                "    fib_prev = 0  # F(0)\n"
                "    fib_curr = 1  # F(1)\n"
                "    # Calculate each subsequent number\n"
                "    for i in range(2, n + 1):\n"
                "        # The next number is the sum of the previous two\n"
                "        fib_next = fib_prev + fib_curr\n"
                "        fib_prev = fib_curr\n"
                "        fib_curr = fib_next\n"
                "    return fib_curr\n"
            ),
        }]
        result = compressor.compress_messages(messages)
        # Code should see some compression (comments stripped, etc.)
        assert result.original_tokens > 0
        assert len(result.messages) == 1

    def test_fusion_passthrough_short_text(self):
        """Very short text may see no compression — should not crash."""
        compressor = Compressor(compressor_type="fusion")
        messages = [{"role": "user", "content": "Hi"}]
        result = compressor.compress_messages(messages)
        assert result.messages[0]["content"] is not None


# -----------------------------------------------------------------------
# Real LLMLingua tests (require llmlingua)
# -----------------------------------------------------------------------

class TestLLMLinguaBackendReal:
    """Integration tests with real LLMLingua."""

    @pytest.fixture(autouse=True)
    def _check_llmlingua(self):
        try:
            from llmlingua import PromptCompressor  # noqa: F401
        except ImportError:
            pytest.skip("llmlingua not installed")

    def test_llmlingua_available(self):
        compressor = Compressor(compressor_type="llmlingua")
        assert compressor.available

    def test_llmlingua_compresses_text(self):
        """LLMLingua should compress natural language."""
        compressor = Compressor(compressor_type="llmlingua", rate=0.5)
        messages = [{
            "role": "user",
            "content": (
                "Can you please explain to me in great detail how Python "
                "list comprehensions work? I want to understand the syntax, "
                "the performance implications, and some advanced use cases "
                "with nested comprehensions and conditional filtering."
            ),
        }]
        result = compressor.compress_messages(messages)
        assert result.original_tokens > 0
        assert result.compressed_tokens <= result.original_tokens
        assert len(result.messages) == 1
