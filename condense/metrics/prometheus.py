"""Prometheus text format metrics export."""

from typing import Any


def render_prometheus_metrics(summary: dict[str, Any]) -> str:
    """Render metrics in Prometheus text exposition format."""
    totals = summary.get("totals", {})
    rates = summary.get("rates", {})
    uptime_seconds = float(summary.get("uptime_seconds") or 0.0)
    lines = []

    def _gauge(name: str, help_text: str, value):
        lines.append(f"# HELP {name} {help_text}")
        lines.append(f"# TYPE {name} gauge")
        lines.append(f"{name} {value}")

    def _counter(name: str, help_text: str, value):
        lines.append(f"# HELP {name} {help_text}")
        lines.append(f"# TYPE {name} counter")
        lines.append(f"{name} {value}")

    _counter("condense_requests_total", "Total number of requests processed", int(totals.get("total_requests", 0)))
    _counter("condense_cache_hits_total", "Total number of cache hits", int(totals.get("cache_hits", 0)))
    _counter("condense_cache_misses_total", "Total number of cache misses", int(totals.get("cache_misses", 0)))
    _gauge("condense_cache_hit_rate", "Cache hit rate percentage", round(float(rates.get("cache_hit_rate", 0.0)), 2))
    _counter("condense_savings_usd_total", "Total savings in USD", round(float(totals.get("total_savings_usd", 0.0)), 6))
    _counter("condense_cost_usd_total", "Total cost in USD", round(float(totals.get("total_cost_usd", 0.0)), 6))
    _counter("condense_requests_routed_total", "Total requests routed to cheaper models", int(totals.get("requests_routed", 0)))
    _counter("condense_requests_rejected_total", "Total requests rejected (budget)", int(totals.get("requests_rejected", 0)))
    _counter("condense_pipeline_errors_total", "Total pipeline errors (caught by failsafe)", int(totals.get("pipeline_errors", 0)))
    _gauge("condense_uptime_seconds", "Proxy uptime in seconds", round(uptime_seconds, 1))

    return "\n".join(lines) + "\n"
