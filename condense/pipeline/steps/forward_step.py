"""Forward step — sends the request to the upstream LLM provider."""

import logging
import os
from typing import Optional

import httpx

from condense.pipeline.context import PipelineContext
from condense.pipeline.result import StepResult
from condense.pipeline.steps.base import BaseStep

logger = logging.getLogger(__name__)


class ForwardStep(BaseStep):
    """Forward the (possibly modified) request to the upstream provider.

    This is always the last step in the pipeline.
    """

    def __init__(self, config: dict, http_client: httpx.AsyncClient):
        super().__init__(config)
        self.http_client = http_client
        self.upstream_url = config.get("url", "https://api.openai.com/v1")
        self.timeout = config.get("timeout_seconds", 300)
        self.api_key_env = config.get("api_key_env")

    async def execute(self, ctx: PipelineContext) -> StepResult:
        url = f"{self.upstream_url.rstrip('/')}/chat/completions"

        # Build headers
        headers = {
            "Content-Type": "application/json",
        }

        # Inject API key if configured
        if self.api_key_env:
            api_key = os.environ.get(self.api_key_env)
            if api_key:
                headers["Authorization"] = f"Bearer {api_key}"

        # Forward any Authorization header from the original request
        original_auth = ctx.metadata.get("authorization_header")
        if original_auth:
            headers["Authorization"] = original_auth

        try:
            response = await self.http_client.post(
                url,
                json=ctx.request,
                headers=headers,
                timeout=self.timeout,
            )

            response_data = response.json()

            # Pass through provider errors as-is
            if response.status_code >= 400:
                logger.warning(
                    f"Upstream returned {response.status_code}: "
                    f"{response_data.get('error', {}).get('message', 'unknown')}"
                )
                return StepResult(
                    action="short_circuit",
                    response=response_data,
                    status_code=response.status_code,
                )

            # Estimate cost from usage (for budget tracking)
            usage = response_data.get("usage", {})
            estimated_cost = self._estimate_cost(
                ctx.request.get("model", ""),
                usage.get("prompt_tokens", 0),
                usage.get("completion_tokens", 0),
            )

            # Store estimated cost in response for cache savings calculation
            response_data["_condense_estimated_cost"] = estimated_cost
            ctx.metadata["estimated_cost"] = estimated_cost

            return StepResult(
                action="short_circuit",
                response=response_data,
                status_code=response.status_code,
            )

        except httpx.TimeoutException:
            logger.error(f"Upstream request timed out after {self.timeout}s")
            return StepResult(
                action="reject",
                error="Upstream request timed out",
                status_code=504,
            )
        except httpx.ConnectError as e:
            logger.error(f"Failed to connect to upstream: {e}")
            return StepResult(
                action="reject",
                error=f"Failed to connect to upstream: {str(e)}",
                status_code=502,
            )
        except Exception as e:
            logger.error(f"Forward step failed: {e}", exc_info=True)
            return StepResult(
                action="reject",
                error=f"Internal proxy error: {str(e)}",
                status_code=500,
            )

    def _estimate_cost(self, model: str, prompt_tokens: int, completion_tokens: int) -> float:
        """Rough cost estimation based on model.

        These are approximate per-1M-token prices.
        """
        # Pricing per 1M tokens (input, output)
        pricing = {
            "gpt-4o": (2.50, 10.00),
            "gpt-4o-mini": (0.15, 0.60),
            "gpt-4-turbo": (10.00, 30.00),
            "gpt-4": (30.00, 60.00),
            "gpt-3.5-turbo": (0.50, 1.50),
            "claude-3-5-sonnet": (3.00, 15.00),
            "claude-3-5-haiku": (0.80, 4.00),
            "claude-3-opus": (15.00, 75.00),
            "claude-3-sonnet": (3.00, 15.00),
            "claude-3-haiku": (0.25, 1.25),
            "deepseek-chat": (0.14, 0.28),
            "deepseek-coder": (0.14, 0.28),
        }

        model_lower = model.lower()
        input_price, output_price = 5.0, 15.0  # Default fallback

        for model_key, (inp, out) in pricing.items():
            if model_key in model_lower:
                input_price, output_price = inp, out
                break

        cost = (prompt_tokens * input_price + completion_tokens * output_price) / 1_000_000
        return round(cost, 6)
