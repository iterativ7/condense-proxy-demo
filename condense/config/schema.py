"""Pydantic models for condense.yaml configuration."""

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, model_validator


class UpstreamConfig(BaseModel):
    url: str = "https://api.openai.com/v1"
    timeout_seconds: int = 300
    api_key_env: Optional[str] = None


class DeploymentConfig(BaseModel):
    mode: str = "behind-gateway"  # "behind-gateway" | "standalone"
    host: str = "0.0.0.0"
    port: int = 8080


class ExactCacheConfig(BaseModel):
    enabled: bool = True
    backend: str = "memory"  # "memory" | "redis"
    max_size: int = 10000
    ttl_seconds: int = 3600


class CacheConfig(BaseModel):
    enabled: bool = True
    exact: ExactCacheConfig = Field(default_factory=ExactCacheConfig)
    non_deterministic: str = "skip"  # "skip" | "allow" | "normalize"


class AnthropicCacheConfig(BaseModel):
    inject_cache_control: bool = True
    cache_system_prompt: bool = True
    cache_tools: bool = True


class OpenAICacheConfig(BaseModel):
    enabled: bool = True


class DeepseekCacheConfig(BaseModel):
    enabled: bool = True


class ProviderCacheConfig(BaseModel):
    enabled: bool = True
    anthropic: AnthropicCacheConfig = Field(default_factory=AnthropicCacheConfig)
    openai: OpenAICacheConfig = Field(default_factory=OpenAICacheConfig)
    deepseek: DeepseekCacheConfig = Field(default_factory=DeepseekCacheConfig)


class RoutingRule(BaseModel):
    condition: str  # "short_messages" | "no_tools"
    max_chars: Optional[int] = None
    model: str


class ModelRoutingConfig(BaseModel):
    """ML-based model routing.

    Routes requests to strong or weak models based on query complexity.
    Supports two backends selected automatically by ``router_type``:

    - **RouteLLM** (lm-sys): ``bert``, ``mf``, ``causal_llm``, ``sw_ranking``,
      ``random``.  ``bert`` runs fully offline with a pre-trained classifier.
      Install with ``pip install routellm``.
    - **LLMRouter** (llmrouter-lib): ``smallest_llm``, ``largest_llm``, or
      trained strategies with a ``config_path``.
      Install with ``pip install llmrouter-lib``.
    """

    enabled: bool = False
    strong: str = "gpt-4o"
    weak: str = "gpt-4o-mini"
    threshold: float = 0.5
    router_type: str = "bert"  # bert | mf | causal_llm | sw_ranking | smallest_llm | largest_llm
    config_path: Optional[str] = None  # required for trained llmrouter-lib strategies


class RoutingConfig(BaseModel):
    enabled: bool = False
    rules: list[RoutingRule] = Field(default_factory=list)
    model_routing: ModelRoutingConfig = Field(default_factory=ModelRoutingConfig)


class BudgetConfig(BaseModel):
    enabled: bool = True
    max_session_cost_usd: float = 10.0
    max_turns_per_session: int = 100
    loop_detection_window: int = 5


class OptimizationEntry(BaseModel):
    id: str
    type: Literal["cache", "provider_cache", "routing", "budget"]
    enabled: bool = True
    stage: Literal["both", "forward", "backward"] = "both"
    depends_on: list[str] = Field(default_factory=list)
    parallelizable: bool | None = None
    config: dict[str, Any] = Field(default_factory=dict)


class RedisConfig(BaseModel):
    enabled: bool = False
    url: str = "redis://localhost:6379"
    password_env: Optional[str] = None


class MetricsConfig(BaseModel):
    enabled: bool = True
    endpoint: str = "/metrics"


class HeadersConfig(BaseModel):
    add_savings_headers: bool = True


class CircuitBreakerConfig(BaseModel):
    threshold: int = 5
    recovery_seconds: int = 30


class FailsafeConfig(BaseModel):
    on_error: str = "passthrough"
    circuit_breaker: CircuitBreakerConfig = Field(default_factory=CircuitBreakerConfig)


class CondenseConfig(BaseModel):
    upstream: UpstreamConfig = Field(default_factory=UpstreamConfig)
    deployment: DeploymentConfig = Field(default_factory=DeploymentConfig)
    optimizations: list[OptimizationEntry] = Field(default_factory=list)
    redis: RedisConfig = Field(default_factory=RedisConfig)
    metrics: MetricsConfig = Field(default_factory=MetricsConfig)
    headers: HeadersConfig = Field(default_factory=HeadersConfig)
    failsafe: FailsafeConfig = Field(default_factory=FailsafeConfig)

    @model_validator(mode="after")
    def _validate_optimization_graph(self) -> "CondenseConfig":
        ids = [entry.id for entry in self.optimizations]
        if len(ids) != len(set(ids)):
            duplicates = sorted({entry_id for entry_id in ids if ids.count(entry_id) > 1})
            dup_str = ", ".join(duplicates)
            raise ValueError(f"Duplicate optimization ids: {dup_str}")

        known_ids = set(ids)
        enabled_entries = [entry for entry in self.optimizations if entry.enabled]
        enabled_ids = {entry.id for entry in enabled_entries}
        for entry in enabled_entries:
            # Allow dependencies that point to disabled entries; they'll be ignored
            # by the scheduler for this runtime config.
            missing = [dep for dep in entry.depends_on if dep not in known_ids]
            if missing:
                miss = ", ".join(missing)
                raise ValueError(
                    f"Optimization {entry.id!r} depends on unknown optimization(s): {miss}"
                )

        graph = {
            entry.id: {dep for dep in entry.depends_on if dep in enabled_ids}
            for entry in enabled_entries
        }
        in_degree = {node: len(deps) for node, deps in graph.items()}
        queue = [node for node, degree in in_degree.items() if degree == 0]

        visited = 0
        while queue:
            node = queue.pop(0)
            visited += 1
            for other, deps in graph.items():
                if node in deps:
                    in_degree[other] -= 1
                    if in_degree[other] == 0:
                        queue.append(other)

        if visited != len(graph):
            raise ValueError("Optimization dependencies contain a cycle")

        return self

    def optimization_entry(
        self, optimization_type: Literal["cache", "provider_cache", "routing", "budget"]
    ) -> OptimizationEntry | None:
        for entry in self.optimizations:
            if entry.type == optimization_type:
                return entry
        return None

    def cache_config(self) -> CacheConfig:
        entry = self.optimization_entry("cache")
        if entry is None:
            return CacheConfig(enabled=False)
        cfg = CacheConfig.model_validate(entry.config)
        return cfg.model_copy(update={"enabled": entry.enabled})

    def provider_cache_config(self) -> ProviderCacheConfig:
        entry = self.optimization_entry("provider_cache")
        if entry is None:
            return ProviderCacheConfig(enabled=False)
        cfg = ProviderCacheConfig.model_validate(entry.config)
        return cfg.model_copy(update={"enabled": entry.enabled})

    def routing_config(self) -> RoutingConfig:
        entry = self.optimization_entry("routing")
        if entry is None:
            return RoutingConfig(enabled=False)
        cfg = RoutingConfig.model_validate(entry.config)
        return cfg.model_copy(update={"enabled": entry.enabled})

    def budget_config(self) -> BudgetConfig:
        entry = self.optimization_entry("budget")
        if entry is None:
            return BudgetConfig(enabled=False)
        cfg = BudgetConfig.model_validate(entry.config)
        return cfg.model_copy(update={"enabled": entry.enabled})
