"""Tests for CompressionStep."""

from unittest.mock import MagicMock, patch

import pytest

from condense.compression.base import CompressResult
from condense.config.schema import CondenseConfig
from condense.pipeline.context import PipelineContext
from condense.pipeline.steps.compression_step import CompressionStep, _compressor_cache


def make_ctx(request=None):
    req = request or {
        "model": "gpt-4o",
        "messages": [{"role": "user", "content": "Hello world, this is a test message"}],
    }
    return PipelineContext(
        original_request=req.copy(),
        request=req,
        config=CondenseConfig(),
    )


@pytest.fixture(autouse=True)
def clear_compressor_cache():
    _compressor_cache.clear()
    yield
    _compressor_cache.clear()


class TestCompressionStep:
    def _mock_compressor(self, compress_result):
        mock = MagicMock()
        mock.available = True
        mock.compress_messages.return_value = compress_result
        return mock

    @pytest.mark.asyncio
    async def test_compression_applied(self):
        """Messages are compressed and stats tracked."""
        mock = self._mock_compressor(CompressResult(
            messages=[{"role": "user", "content": "compressed"}],
            original_tokens=50,
            compressed_tokens=30,
            reduction_pct=40.0,
        ))
        with patch(
            "condense.pipeline.steps.compression_step._get_or_create_compressor",
            return_value=mock,
        ):
            step = CompressionStep({"compressor_type": "fusion"})
            ctx = make_ctx()
            result = await step.execute(ctx)

        assert result.action == "next"
        assert result.technique == "compression"
        assert ctx.request["messages"] == [{"role": "user", "content": "compressed"}]
        assert ctx.original_tokens == 50
        assert ctx.optimized_tokens == 30
        assert ctx.metadata["compression_stats"]["reduction_pct"] == 40.0

    @pytest.mark.asyncio
    async def test_no_compression_when_zero_reduction(self):
        """No technique applied when compression doesn't reduce tokens."""
        mock = self._mock_compressor(CompressResult(
            messages=[{"role": "user", "content": "same"}],
            original_tokens=10,
            compressed_tokens=10,
            reduction_pct=0.0,
        ))
        with patch(
            "condense.pipeline.steps.compression_step._get_or_create_compressor",
            return_value=mock,
        ):
            step = CompressionStep({"compressor_type": "fusion"})
            ctx = make_ctx()
            result = await step.execute(ctx)

        assert result.action == "next"
        assert result.technique is None

    @pytest.mark.asyncio
    async def test_no_compression_when_unavailable(self):
        """Step passes through when compressor is unavailable."""
        mock = MagicMock()
        mock.available = False
        with patch(
            "condense.pipeline.steps.compression_step._get_or_create_compressor",
            return_value=mock,
        ):
            step = CompressionStep({"compressor_type": "fusion"})
            ctx = make_ctx()
            result = await step.execute(ctx)

        assert result.action == "next"
        assert result.technique is None

    @pytest.mark.asyncio
    async def test_empty_messages(self):
        """Empty messages list should pass through without error."""
        step = CompressionStep({"compressor_type": "fusion"})
        ctx = make_ctx(request={"model": "gpt-4o", "messages": []})

        # Mock to avoid loading real backend
        mock = MagicMock()
        mock.available = True
        with patch(
            "condense.pipeline.steps.compression_step._get_or_create_compressor",
            return_value=mock,
        ):
            result = await step.execute(ctx)

        assert result.action == "next"
        assert result.technique is None
