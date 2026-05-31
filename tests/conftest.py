"""Shared test fixtures."""

import threading
import time
from collections import defaultdict

import pytest
import httpx
from unittest.mock import AsyncMock

from condense.cache.memory import InMemoryCache
from condense.config.schema import CondenseConfig
from condense.metrics.postgres_store import WINDOW_TO_SECONDS
from condense.session.store import SessionStore
from condense.config.loader import reset_config_cache


@pytest.fixture
def default_config():
    """A default CondenseConfig for testing."""
    return CondenseConfig()


@pytest.fixture
def cache_backend():
    """An in-memory cache backend for testing."""
    return InMemoryCache(max_size=100, default_ttl=60)


@pytest.fixture
def session_store():
    """A session store for testing."""
    return SessionStore()


@pytest.fixture
def mock_http_client():
    """A mock httpx.AsyncClient."""
    client = AsyncMock(spec=httpx.AsyncClient)
    return client


@pytest.fixture(autouse=True)
def reset_config():
    """Reset config cache between tests."""
    reset_config_cache()
    yield
    reset_config_cache()


@pytest.fixture(autouse=True)
def reset_strategy_cache():
    """Reset cache strategy instances between tests to prevent state leaks."""
    from condense.pipeline.steps.cache_step import _strategy_cache
    _strategy_cache.clear()
    yield
    _strategy_cache.clear()


class FakePostgresMetricsStore:
    """In-memory stand-in for Postgres metrics store during unit tests."""

    _stores: dict[str, dict[str, list[dict]]] = {}

    def __init__(self, dsn: str):
        self._dsn = dsn
        self._lock = threading.Lock()
        store = self._stores.setdefault(
            dsn,
            {
                "events": [],
                "optimization_events": [],
            },
        )
        self._events = store["events"]
        self._optimization_events = store["optimization_events"]

    def close(self) -> None:
        return None

    def record_request(self, request_metrics: dict) -> None:
        now = time.time()
        event = {
            "recorded_at": now,
            "cache_hit": bool(request_metrics.get("cache_hit")),
            "savings_usd": float(request_metrics.get("savings_usd") or 0.0),
            "cost_usd": float(request_metrics.get("cost_usd") or 0.0),
            "prompt_tokens": int(request_metrics.get("prompt_tokens") or 0),
            "completion_tokens": int(request_metrics.get("completion_tokens") or 0),
            "total_tokens": int(request_metrics.get("total_tokens") or 0),
            "tokens_saved_estimate": int(request_metrics.get("tokens_saved_estimate") or 0),
            "routed": bool(request_metrics.get("routed")),
            "rejected": bool(request_metrics.get("rejected")),
            "ttfb_ms": float(request_metrics.get("ttfb_ms") or 0.0),
            "stream_duration_ms": float(request_metrics.get("stream_duration_ms") or 0.0),
        }
        with self._lock:
            self._events.append(event)
            for update in request_metrics.get("optimization_updates") or []:
                self._optimization_events.append(
                    {
                        "recorded_at": now,
                        "optimization_id": str(update.get("optimization_id") or "unknown"),
                        "technique": update.get("technique"),
                        "action": update.get("action"),
                        "savings_usd": float(update.get("savings_usd") or 0.0),
                        "tokens_saved": int(update.get("tokens_saved") or 0),
                        "details": dict(update.get("details") or {}),
                    }
                )

    def _filter_window(self, items: list[dict], window: str) -> list[dict]:
        seconds = WINDOW_TO_SECONDS.get(window, WINDOW_TO_SECONDS["7d"])
        if seconds is None:
            return list(items)
        cutoff = time.time() - seconds
        return [item for item in items if float(item.get("recorded_at", 0.0)) >= cutoff]

    def summary(self) -> dict:
        with self._lock:
            events = list(self._events)
        total_requests = len(events)
        cache_hits = sum(1 for event in events if event["cache_hit"])
        cache_misses = total_requests - cache_hits
        total_savings_usd = sum(event["savings_usd"] for event in events)
        avg_ttfb_samples = [event["ttfb_ms"] for event in events if event["ttfb_ms"] > 0]
        avg_stream_samples = [
            event["stream_duration_ms"] for event in events if event["stream_duration_ms"] > 0
        ]
        uptime_seconds = (time.time() - events[0]["recorded_at"]) if events else 0.0
        return {
            "totals": {
                "total_requests": total_requests,
                "cache_hits": cache_hits,
                "cache_misses": cache_misses,
                "total_savings_usd": round(total_savings_usd, 6),
                "total_cost_usd": round(sum(event["cost_usd"] for event in events), 6),
                "total_prompt_tokens": sum(event["prompt_tokens"] for event in events),
                "total_completion_tokens": sum(event["completion_tokens"] for event in events),
                "total_tokens": sum(event["total_tokens"] for event in events),
                "total_tokens_saved_estimate": sum(event["tokens_saved_estimate"] for event in events),
                "requests_routed": sum(1 for event in events if event["routed"]),
                "requests_rejected": sum(1 for event in events if event["rejected"]),
                "pipeline_errors": 0,
            },
            "rates": {
                "cache_hit_rate": round((cache_hits / total_requests * 100.0) if total_requests else 0.0, 2),
                "avg_savings_per_request_usd": round((total_savings_usd / total_requests) if total_requests else 0.0, 6),
                "avg_ttfb_ms": round(sum(avg_ttfb_samples) / len(avg_ttfb_samples), 2) if avg_ttfb_samples else 0.0,
                "avg_stream_duration_ms": round(sum(avg_stream_samples) / len(avg_stream_samples), 2) if avg_stream_samples else 0.0,
            },
            "uptime_seconds": round(uptime_seconds, 1),
        }

    def summary_v2(self, *, enabled_tabs: list[str], window: str = "7d") -> dict:
        with self._lock:
            events = self._filter_window(self._events, window)
            optimization_events = self._filter_window(self._optimization_events, window)
            all_optimization_events = list(self._optimization_events)
        optimization_aggregates: dict[str, dict] = {}
        for event in optimization_events:
            optimization_id = event["optimization_id"]
            aggregate = optimization_aggregates.setdefault(
                optimization_id,
                {
                    "optimization_id": optimization_id,
                    "events": 0,
                    "total_savings_usd": 0.0,
                    "total_tokens_saved": 0,
                    "tokens_saved": 0,
                    "last_technique": None,
                    "last_action": None,
                    "last_details": {},
                },
            )
            aggregate["events"] += 1
            aggregate["total_savings_usd"] += event["savings_usd"]
            aggregate["total_tokens_saved"] += event["tokens_saved"]
            aggregate["tokens_saved"] = aggregate["total_tokens_saved"]

        latest_by_optimization: dict[str, dict] = {}
        for event in all_optimization_events:
            latest_by_optimization[event["optimization_id"]] = event
        for optimization_id, latest in latest_by_optimization.items():
            aggregate = optimization_aggregates.setdefault(
                optimization_id,
                {
                    "optimization_id": optimization_id,
                    "events": 0,
                    "total_savings_usd": 0.0,
                    "total_tokens_saved": 0,
                    "tokens_saved": 0,
                    "last_technique": None,
                    "last_action": None,
                    "last_details": {},
                },
            )
            aggregate["last_technique"] = latest.get("technique")
            aggregate["last_action"] = latest.get("action")
            aggregate["last_details"] = dict(latest.get("details") or {})

        for optimization_id in enabled_tabs:
            optimization_aggregates.setdefault(
                optimization_id,
                {
                    "optimization_id": optimization_id,
                    "events": 0,
                    "total_savings_usd": 0.0,
                    "total_tokens_saved": 0,
                    "tokens_saved": 0,
                    "last_technique": None,
                    "last_action": None,
                    "last_details": {},
                },
            )

        bucket_by_day: dict[str, dict] = defaultdict(
            lambda: {"total_requests": 0, "total_savings_usd": 0.0, "total_tokens_saved_estimate": 0}
        )
        for event in events:
            bucket = time.strftime("%Y-%m-%dT00:00:00Z", time.gmtime(event["recorded_at"]))
            bucket_entry = bucket_by_day[bucket]
            bucket_entry["total_requests"] += 1
            bucket_entry["total_savings_usd"] += event["savings_usd"]
            bucket_entry["total_tokens_saved_estimate"] += event["tokens_saved_estimate"]

        optimization_series_by_day: dict[str, dict[str, dict]] = defaultdict(
            lambda: defaultdict(lambda: {"events": 0, "total_savings_usd": 0.0, "total_tokens_saved": 0})
        )
        for event in optimization_events:
            optimization_id = event["optimization_id"]
            bucket = time.strftime("%Y-%m-%dT00:00:00Z", time.gmtime(event["recorded_at"]))
            point = optimization_series_by_day[optimization_id][bucket]
            point["events"] += 1
            point["total_savings_usd"] += event["savings_usd"]
            point["total_tokens_saved"] += event["tokens_saved"]

        uptime_seconds = (time.time() - self._events[0]["recorded_at"]) if self._events else 0.0
        return {
            "overall": {
                "total_savings_usd": round(sum(event["savings_usd"] for event in events), 6),
                "total_tokens_saved_estimate": sum(event["tokens_saved_estimate"] for event in events),
                "total_requests": len(events),
                "uptime_seconds": round(uptime_seconds, 1),
            },
            "window": window if window in WINDOW_TO_SECONDS else "7d",
            "enabled_tabs": enabled_tabs,
            "optimizations": sorted(
                [entry for entry in optimization_aggregates.values() if entry["optimization_id"] != "forward"],
                key=lambda entry: entry["optimization_id"],
            ),
            "series": [
                {
                    "bucket": bucket,
                    "total_requests": values["total_requests"],
                    "total_savings_usd": round(values["total_savings_usd"], 6),
                    "total_tokens_saved_estimate": values["total_tokens_saved_estimate"],
                }
                for bucket, values in sorted(bucket_by_day.items())
            ],
            "optimization_series": [
                {
                    "optimization_id": optimization_id,
                    "points": [
                        {
                            "bucket": bucket,
                            "events": point["events"],
                            "total_savings_usd": round(point["total_savings_usd"], 6),
                            "total_tokens_saved": point["total_tokens_saved"],
                        }
                        for bucket, point in sorted(points.items())
                    ],
                }
                for optimization_id, points in sorted(optimization_series_by_day.items())
                if optimization_id != "forward"
            ],
        }


@pytest.fixture(autouse=True)
def mock_postgres_metrics_store(monkeypatch):
    """Use in-memory fake Postgres store for unit tests."""
    monkeypatch.setattr(
        "condense.server.app.PostgresMetricsStore",
        FakePostgresMetricsStore,
    )
    FakePostgresMetricsStore._stores.clear()


@pytest.fixture
def sample_request():
    """A sample OpenAI-compatible chat request."""
    return {
        "model": "gpt-4o",
        "messages": [
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": "Hello, how are you?"},
        ],
        "temperature": 0,
    }


@pytest.fixture
def sample_response():
    """A sample OpenAI-compatible chat response."""
    return {
        "id": "chatcmpl-test123",
        "object": "chat.completion",
        "created": 1700000000,
        "model": "gpt-4o",
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": "Hello! I'm doing well, thank you!",
                },
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": 20,
            "completion_tokens": 10,
            "total_tokens": 30,
        },
    }
