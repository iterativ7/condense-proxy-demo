"""Forward step — sends the request to the upstream LLM provider."""

import logging
import os
from typing import Optional

import httpx
import litellm

from condense.pipeline.context import PipelineContext
from condense.pipeline.result import StepResult
from condense.pipeline.steps.base import BaseStep

logger = logging.getLogger(__name__)


class ForwardStep(BaseStep):
    """Forward the (possibly modified) request to the upstream provider.

    This is always the last step in the pipeline.
    """
    name = "forward"
    can_short_circuit = True
    reads = frozenset({"request", "metadata:authorization_header"})
    writes = frozenset({"metadata:estimated_cost"})

    def __init__(self, config: dict, http_client: httpx.AsyncClient):
        super().__init__(config)
        self.http_client = http_client
        self.upstream_url = config.get("url", "https://api.openai.com/v1")
        self.timeout = config.get("timeout_seconds", 300)
        self.api_key_env = config.get("api_key_env")

    @staticmethod
    def _extract_bearer_token(authorization: Optional[str]) -> Optional[str]:
        if not authorization:
            return None
        if authorization.lower().startswith("bearer "):
            return authorization[7:].strip() or None
        return authorization.strip() or None

    def _resolve_api_key(self, ctx: PipelineContext) -> Optional[str]:
        original_auth = self._extract_bearer_token(ctx.metadata.get("authorization_header"))
        if original_auth:
            return original_auth
        if self.api_key_env:
            env_key = os.environ.get(self.api_key_env)
            if env_key:
                return env_key
        return None

    @staticmethod
    def _to_dict_response(raw) -> dict:
        if isinstance(raw, dict):
            return raw
        if hasattr(raw, "model_dump"):
            return raw.model_dump()
        if hasattr(raw, "dict"):
            return raw.dict()
        return dict(raw)

    def _litellm_model_name(self, model: str) -> str:
        """Ensure model name has a provider prefix that LiteLLM understands.

        When forwarding to an OpenAI-compatible upstream (like 9Router), models
        may use custom prefixes (e.g. ``cu/claude-4.5-sonnet``). LiteLLM doesn't
        recognise these, so we wrap them with ``openai/`` to tell LiteLLM to
        treat the upstream as a plain OpenAI-compatible endpoint.  Known
        provider prefixes (``openai/``, ``anthropic/``, ``deepseek/``, etc.)
        are left untouched.
        """
        known_prefixes = {
            "openai/", "anthropic/", "deepseek/", "azure/", "bedrock/",
            "vertex_ai/", "cohere/", "replicate/", "huggingface/",
            "together_ai/", "ollama/", "groq/", "mistral/", "gemini/",
            "ollama_chat/", "sagemaker/",
        }
        model_lower = model.lower()
        for prefix in known_prefixes:
            if model_lower.startswith(prefix):
                return model
        # Unknown prefix or no prefix — wrap as openai-compatible
        return f"openai/{model}"

    async def execute(self, ctx: PipelineContext) -> StepResult:
        try:
            original_model = ctx.request.get("model", "")
            litellm_model = self._litellm_model_name(original_model)

            payload = {
                "model": litellm_model,
                "messages": ctx.request.get("messages", []),
                "api_base": self.upstream_url,
                "timeout": self.timeout,
                "drop_params": True,
                **{
                    key: value
                    for key, value in ctx.request.items()
                    if key not in {"model", "messages"}
                },
            }
            api_key = self._resolve_api_key(ctx)
            # LiteLLM requires an api_key even for OpenAI-compatible upstreams.
            # Use a placeholder when none is provided (e.g. 9Router doesn't need auth).
            payload["api_key"] = api_key or "condense-proxy"

            raw_response = await litellm.acompletion(**payload)
            response_data = self._to_dict_response(raw_response)

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

            prompt_tokens = int(usage.get("prompt_tokens") or 0)
            completion_tokens = int(usage.get("completion_tokens") or 0)
            total_tokens = usage.get("total_tokens")
            if total_tokens is None:
                total_tokens = prompt_tokens + completion_tokens

            return StepResult(
                action="short_circuit",
                response=response_data,
                status_code=200,
                technique="forward",
                savings_usd=0.0,
                tokens_saved=0,
                details={
                    "estimated_cost": estimated_cost,
                    "prompt_tokens": prompt_tokens,
                    "completion_tokens": completion_tokens,
                    "total_tokens": int(total_tokens),
                    "model": ctx.request.get("model", ""),
                },
                optimization_updates=[
                    {
                        "technique": "forward",
                        "savings_usd": 0.0,
                        "tokens_saved": 0,
                        "details": {
                            "estimated_cost": estimated_cost,
                            "prompt_tokens": prompt_tokens,
                            "completion_tokens": completion_tokens,
                            "total_tokens": int(total_tokens),
                            "model": ctx.request.get("model", ""),
                        },
                    }
                ],
            )

        except Exception as e:
            status_code = getattr(e, "status_code", None) or getattr(e, "status", None)
            if status_code:
                message = str(e)
                logger.warning(f"LiteLLM upstream error ({status_code}): {message}")
                return StepResult(
                    action="short_circuit",
                    response={
                        "error": {
                            "message": message,
                            "type": "upstream_error",
                        }
                    },
                    status_code=int(status_code),
                    technique="forward",
                    savings_usd=0.0,
                    tokens_saved=0,
                    optimization_updates=[
                        {
                            "technique": "forward",
                            "savings_usd": 0.0,
                            "tokens_saved": 0,
                            "details": {
                                "error_type": "upstream_error",
                                "status_code": int(status_code),
                            },
                        }
                    ],
                )

            if "timeout" in str(e).lower():
                logger.error(f"Upstream request timed out after {self.timeout}s")
                return StepResult(
                    action="reject",
                    error="Upstream request timed out",
                    status_code=504,
                    technique="forward",
                    savings_usd=0.0,
                    tokens_saved=0,
                    optimization_updates=[
                        {
                            "technique": "forward",
                            "savings_usd": 0.0,
                            "tokens_saved": 0,
                            "details": {"error_type": "timeout"},
                        }
                    ],
                )
            if "connect" in str(e).lower() or "connection" in str(e).lower():
                logger.error(f"Failed to connect to upstream: {e}")
                return StepResult(
                    action="reject",
                    error=f"Failed to connect to upstream: {str(e)}",
                    status_code=502,
                    technique="forward",
                    savings_usd=0.0,
                    tokens_saved=0,
                    optimization_updates=[
                        {
                            "technique": "forward",
                            "savings_usd": 0.0,
                            "tokens_saved": 0,
                            "details": {"error_type": "connect"},
                        }
                    ],
                )

            logger.error(f"Forward step failed: {e}", exc_info=True)
            return StepResult(
                action="reject",
                error=f"Internal proxy error: {str(e)}",
                status_code=500,
                technique="forward",
                savings_usd=0.0,
                tokens_saved=0,
                optimization_updates=[
                    {
                        "technique": "forward",
                        "savings_usd": 0.0,
                        "tokens_saved": 0,
                        "details": {"error_type": "internal"},
                    }
                ],
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
