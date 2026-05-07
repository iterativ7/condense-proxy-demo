"""Prometheus text format metrics export."""

from condense.metrics.tracker import MetricsTracker


def render_prometheus_metrics(tracker: MetricsTracker) -> str:
    """Render metrics in Prometheus text exposition format."""
    snap = tracker.snapshot()
    lines = []

    def _gauge(name: str, help_text: str, value):
        lines.append(f"# HELP {name} {help_text}")
        lines.append(f"# TYPE {name} gauge")
        lines.append(f"{name} {value}")

    def _counter(name: str, help_text: str, value):
        lines.append(f"# HELP {name} {help_text}")
        lines.append(f"# TYPE {name} counter")
        lines.append(f"{name} {value}")

    _counter("condense_requests_total", "Total number of requests processed", snap.total_requests)
    _counter("condense_cache_hits_total", "Total number of cache hits", snap.cache_hits)
    _counter("condense_cache_misses_total", "Total number of cache misses", snap.cache_misses)
    _gauge("condense_cache_hit_rate", "Cache hit rate percentage", round(tracker.cache_hit_rate, 2))
    _counter("condense_savings_usd_total", "Total savings in USD", round(snap.total_savings_usd, 6))
    _counter("condense_cost_usd_total", "Total cost in USD", round(snap.total_cost_usd, 6))
    _counter("condense_requests_routed_total", "Total requests routed to cheaper models", snap.requests_routed)
    _counter("condense_requests_rejected_total", "Total requests rejected (budget)", snap.requests_rejected)
    _counter("condense_pipeline_errors_total", "Total pipeline errors (caught by failsafe)", snap.pipeline_errors)
    _gauge("condense_uptime_seconds", "Proxy uptime in seconds", round(snap.uptime_seconds, 1))

    return "\n".join(lines) + "\n"
