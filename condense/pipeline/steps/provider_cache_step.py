"""Provider prompt cache injection step.

Auto-injects cache_control headers for Anthropic models to leverage
their prompt caching feature (90% savings on repeated token prefixes).
"""

import logging
from condense.pipeline.context import PipelineContext
from condense.pipeline.result import StepResult
from condense.pipeline.steps.base import BaseStep
from condense.upstream.provider_detect import detect_provider

logger = logging.getLogger(__name__)


class ProviderCacheStep(BaseStep):
    """Inject provider-specific cache control headers."""
    name = "provider_cache"
    reads = frozenset({"request:model", "request:messages", "request:tools"})
    writes = frozenset({"request:messages", "request:tools"})

    async def execute(self, ctx: PipelineContext) -> StepResult:
        model = ctx.request.get("model", "")
        provider = detect_provider(model)

        if provider == "anthropic":
            return await self._handle_anthropic(ctx)
        elif provider == "openai":
            # OpenAI handles prefix caching automatically — no injection needed
            # but we note the technique was considered
            if self.config.get("openai", {}).get("enabled", True):
                return StepResult(action="next", technique="provider_cache_openai")
        elif provider == "deepseek":
            if self.config.get("deepseek", {}).get("enabled", True):
                return StepResult(action="next", technique="provider_cache_deepseek")

        return StepResult(action="next")

    async def _handle_anthropic(self, ctx: PipelineContext) -> StepResult:
        """Inject cache_control for Anthropic models."""
        anthropic_config = self.config.get("anthropic", {})
        if not anthropic_config.get("inject_cache_control", True):
            return StepResult(action="next")

        modified = False
        messages = ctx.request.get("messages", [])

        # Inject cache_control on system prompt
        if anthropic_config.get("cache_system_prompt", True):
            for msg in messages:
                if msg.get("role") == "system":
                    if isinstance(msg.get("content"), str):
                        # Convert to block format with cache_control
                        msg["content"] = [
                            {
                                "type": "text",
                                "text": msg["content"],
                                "cache_control": {"type": "ephemeral"},
                            }
                        ]
                        modified = True
                    elif isinstance(msg.get("content"), list):
                        # Add cache_control to last text block
                        for block in reversed(msg["content"]):
                            if isinstance(block, dict) and block.get("type") == "text":
                                block["cache_control"] = {"type": "ephemeral"}
                                modified = True
                                break
                    break  # Only process first system message

        # Inject cache_control on last tool definition
        if anthropic_config.get("cache_tools", True):
            tools = ctx.request.get("tools")
            if tools and len(tools) > 0:
                last_tool = tools[-1]
                if isinstance(last_tool, dict):
                    last_tool["cache_control"] = {"type": "ephemeral"}
                    modified = True

        if modified:
            logger.debug("Injected Anthropic cache_control headers")
            return StepResult(action="next", technique="provider_cache_anthropic")

        return StepResult(action="next")
