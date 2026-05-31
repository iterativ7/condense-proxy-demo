"""Integration tests for RTK in the pipeline.

Tests the full flow: config → CompressionStep → chain → RTK backend.
Uses mocked subprocess calls (no real RTK binary needed).
"""

from unittest.mock import MagicMock, patch
import subprocess

import pytest

from condense.compression.base import CompressResult
from condense.config.schema import CondenseConfig
from condense.pipeline.context import PipelineContext
from condense.pipeline.steps.compression_step import CompressionStep, _compressor_cache


@pytest.fixture(autouse=True)
def clear_cache():
    _compressor_cache.clear()
    yield
    _compressor_cache.clear()


def _make_ctx(messages):
    req = {"model": "gpt-4o", "messages": messages}
    return PipelineContext(
        original_request=req.copy(),
        request=req,
        config=CondenseConfig(),
    )


# -----------------------------------------------------------------------
# Single RTK backend via pipeline
# -----------------------------------------------------------------------

class TestCompressionStepWithRTK:
    """Test CompressionStep using RTK as single backend."""

    @pytest.mark.asyncio
    @patch("condense.compression.backends.rtk_backend._rtk_binary_path", return_value="/usr/bin/rtk")
    @patch("condense.compression.backends.rtk_backend.subprocess.run")
    async def test_rtk_single_backend(self, mock_run, mock_path):
        """RTK as single compressor_type compresses tool messages."""
        mock_run.return_value = subprocess.CompletedProcess(
            args=["rtk", "pipe"],
            returncode=0,
            stdout="compact",
            stderr="",
        )

        step = CompressionStep({"compressor_type": "rtk"})
        ctx = _make_ctx([
            {"role": "user", "content": "run tests"},
            {"role": "tool", "content": "running 3 tests\n" + "x" * 200},
        ])
        result = await step.execute(ctx)

        assert result.action == "next"
        # User message unchanged
        assert ctx.request["messages"][0]["content"] == "run tests"
        # Tool message compressed
        assert ctx.request["messages"][1]["content"] == "compact"

    @pytest.mark.asyncio
    @patch("condense.compression.backends.rtk_backend._rtk_binary_path", return_value=None)
    async def test_rtk_unavailable_passes_through(self, mock_path):
        """When RTK binary is missing, step passes through."""
        step = CompressionStep({"compressor_type": "rtk"})
        ctx = _make_ctx([
            {"role": "tool", "content": "some output"},
        ])
        result = await step.execute(ctx)

        assert result.action == "next"
        assert result.technique is None
        assert ctx.request["messages"][0]["content"] == "some output"


# -----------------------------------------------------------------------
# Chain mode via pipeline
# -----------------------------------------------------------------------

class TestCompressionStepWithChain:
    """Test CompressionStep using chain config."""

    @pytest.mark.asyncio
    @patch("condense.compression.backends.rtk_backend._rtk_binary_path", return_value="/usr/bin/rtk")
    @patch("condense.compression.backends.rtk_backend.subprocess.run")
    async def test_chain_rtk_only(self, mock_run, mock_path):
        """Chain with just RTK targeting tool messages."""
        mock_run.return_value = subprocess.CompletedProcess(
            args=["rtk", "pipe"],
            returncode=0,
            stdout="compact output",
            stderr="",
        )

        step = CompressionStep({
            "chain": [
                {"backend": "rtk", "apply_to": ["tool"]},
            ],
        })
        ctx = _make_ctx([
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "What happened?"},
            {"role": "tool", "content": "x" * 200},
        ])
        result = await step.execute(ctx)

        assert result.action == "next"
        # System and user unchanged
        assert ctx.request["messages"][0]["content"] == "You are helpful."
        assert ctx.request["messages"][1]["content"] == "What happened?"
        # Tool compressed
        assert ctx.request["messages"][2]["content"] == "compact output"

    @pytest.mark.asyncio
    @patch("condense.compression.backends.rtk_backend._rtk_binary_path", return_value=None)
    async def test_chain_skips_unavailable_rtk(self, mock_path):
        """Chain gracefully skips RTK when binary is missing."""
        step = CompressionStep({
            "chain": [
                {"backend": "rtk", "apply_to": ["tool"]},
            ],
        })
        ctx = _make_ctx([
            {"role": "tool", "content": "some output"},
        ])
        result = await step.execute(ctx)

        assert result.action == "next"
        # Nothing compressed
        assert ctx.request["messages"][0]["content"] == "some output"


# -----------------------------------------------------------------------
# Config schema validation
# -----------------------------------------------------------------------

class TestConfigSchemaRTK:
    """Test that RTK-related config parses correctly."""

    def test_single_rtk_config(self):
        config = CondenseConfig(
            optimizations=[{
                "id": "compression",
                "type": "compression",
                "config": {"compressor_type": "rtk"},
            }],
        )
        cc = config.compression_config()
        assert cc.compressor_type == "rtk"

    def test_chain_config(self):
        config = CondenseConfig(
            optimizations=[{
                "id": "compression",
                "type": "compression",
                "config": {
                    "chain": [
                        {"backend": "rtk", "apply_to": ["tool"]},
                        {"backend": "fusion", "apply_to": ["user", "system"]},
                    ],
                },
            }],
        )
        cc = config.compression_config()
        assert len(cc.chain) == 2
        assert cc.chain[0].backend == "rtk"
        assert cc.chain[0].apply_to == ["tool"]
        assert cc.chain[1].backend == "fusion"
        assert cc.chain[1].apply_to == ["user", "system"]

    def test_backward_compatible_fusion_config(self):
        """Existing fusion-only configs still work."""
        config = CondenseConfig(
            optimizations=[{
                "id": "compression",
                "type": "compression",
                "config": {"compressor_type": "fusion"},
            }],
        )
        cc = config.compression_config()
        assert cc.compressor_type == "fusion"
        assert cc.chain == []

    def test_no_compression_config(self):
        """No compression optimization entry returns disabled config."""
        config = CondenseConfig()
        cc = config.compression_config()
        assert cc.enabled is False
