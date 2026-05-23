"""Per-request savings tracking and aggregation."""

import time
import threading
from dataclasses import dataclass, field


@dataclass
class MetricsSnapshot:
    """Point-in-time snapshot of aggregated metrics."""
    total_requests: int = 0
    cache_hits: int = 0
    cache_misses: int = 0
    total_savings_usd: float = 0.0
    total_cost_usd: float = 0.0
    total_prompt_tokens: int = 0
    total_completion_tokens: int = 0
    total_tokens: int = 0
    total_tokens_saved_estimate: int = 0
    requests_routed: int = 0
    requests_rejected: int = 0
    pipeline_errors: int = 0
    uptime_seconds: float = 0.0


class MetricsTracker:
    """Thread-safe metrics aggregation."""

    def __init__(self):
        self._lock = threading.Lock()
        self._start_time = time.time()
        self._total_requests = 0
        self._cache_hits = 0
        self._cache_misses = 0
        self._total_savings_usd = 0.0
        self._total_cost_usd = 0.0
        self._total_prompt_tokens = 0
        self._total_completion_tokens = 0
        self._total_tokens = 0
        self._total_tokens_saved_estimate = 0
        self._requests_routed = 0
        self._requests_rejected = 0
        self._pipeline_errors = 0
        self._latencies: list[float] = []

    def record_request(
        self,
        cache_hit: bool = False,
        savings_usd: float = 0.0,
        cost_usd: float = 0.0,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        total_tokens: int = 0,
        tokens_saved_estimate: int = 0,
        routed: bool = False,
        rejected: bool = False,
        latency_ms: float = 0.0,
    ) -> None:
        """Record metrics for a single request."""
        with self._lock:
            self._total_requests += 1
            if cache_hit:
                self._cache_hits += 1
            else:
                self._cache_misses += 1
            self._total_savings_usd += savings_usd
            self._total_cost_usd += cost_usd
            self._total_prompt_tokens += max(prompt_tokens, 0)
            self._total_completion_tokens += max(completion_tokens, 0)
            self._total_tokens += max(total_tokens, 0)
            self._total_tokens_saved_estimate += max(tokens_saved_estimate, 0)
            if routed:
                self._requests_routed += 1
            if rejected:
                self._requests_rejected += 1
            if latency_ms > 0:
                self._latencies.append(latency_ms)
                # Keep only last 1000 latencies
                if len(self._latencies) > 1000:
                    self._latencies = self._latencies[-1000:]

    def record_error(self) -> None:
        """Record a pipeline error."""
        with self._lock:
            self._pipeline_errors += 1

    def snapshot(self) -> MetricsSnapshot:
        """Get a point-in-time snapshot of metrics."""
        with self._lock:
            return MetricsSnapshot(
                total_requests=self._total_requests,
                cache_hits=self._cache_hits,
                cache_misses=self._cache_misses,
                total_savings_usd=self._total_savings_usd,
                total_cost_usd=self._total_cost_usd,
                total_prompt_tokens=self._total_prompt_tokens,
                total_completion_tokens=self._total_completion_tokens,
                total_tokens=self._total_tokens,
                total_tokens_saved_estimate=self._total_tokens_saved_estimate,
                requests_routed=self._requests_routed,
                requests_rejected=self._requests_rejected,
                pipeline_errors=self._pipeline_errors,
                uptime_seconds=time.time() - self._start_time,
            )

    @property
    def cache_hit_rate(self) -> float:
        """Return cache hit rate as a percentage."""
        total = self._cache_hits + self._cache_misses
        if total == 0:
            return 0.0
        return (self._cache_hits / total) * 100

    @property
    def avg_savings_per_request_usd(self) -> float:
        """Return average savings per request in USD."""
        if self._total_requests == 0:
            return 0.0
        return self._total_savings_usd / self._total_requests
