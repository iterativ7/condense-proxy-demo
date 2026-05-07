"""Pydantic models for condense.yaml configuration."""

from typing import List, Optional
from pydantic import BaseModel, Field


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


class RoutingConfig(BaseModel):
    enabled: bool = False
    rules: List[RoutingRule] = Field(default_factory=list)


class BudgetConfig(BaseModel):
    enabled: bool = True
    max_session_cost_usd: float = 10.0
    max_turns_per_session: int = 100
    loop_detection_window: int = 5


class OptimizationsConfig(BaseModel):
    cache: CacheConfig = Field(default_factory=CacheConfig)
    provider_cache: ProviderCacheConfig = Field(default_factory=ProviderCacheConfig)
    routing: RoutingConfig = Field(default_factory=RoutingConfig)
    budget: BudgetConfig = Field(default_factory=BudgetConfig)


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
    optimizations: OptimizationsConfig = Field(default_factory=OptimizationsConfig)
    redis: RedisConfig = Field(default_factory=RedisConfig)
    metrics: MetricsConfig = Field(default_factory=MetricsConfig)
    headers: HeadersConfig = Field(default_factory=HeadersConfig)
    failsafe: FailsafeConfig = Field(default_factory=FailsafeConfig)
