"""SQLite-backed persistent metrics store for dashboard summaries."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any


WINDOW_TO_SECONDS: dict[str, int | None] = {
    "24h": 24 * 60 * 60,
    "7d": 7 * 24 * 60 * 60,
    "30d": 30 * 24 * 60 * 60,
    "all_time": None,
}


class SqliteMetricsStore:
    """Thread-safe SQLite metrics recorder + query service."""

    def __init__(self, db_path: Path):
        self._db_path = db_path
        self._lock = threading.Lock()
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
            self._init_schema()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def _init_schema(self) -> None:
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS metrics_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                recorded_at REAL NOT NULL,
                cache_hit INTEGER NOT NULL,
                savings_usd REAL NOT NULL,
                cost_usd REAL NOT NULL,
                prompt_tokens INTEGER NOT NULL,
                completion_tokens INTEGER NOT NULL,
                total_tokens INTEGER NOT NULL,
                tokens_saved_estimate INTEGER NOT NULL,
                routed INTEGER NOT NULL,
                rejected INTEGER NOT NULL,
                latency_ms REAL NOT NULL,
                ttfb_ms REAL NOT NULL,
                stream_duration_ms REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_metrics_events_recorded_at
                ON metrics_events(recorded_at);

            CREATE TABLE IF NOT EXISTS metrics_optimization_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                request_event_id INTEGER NOT NULL,
                recorded_at REAL NOT NULL,
                optimization_id TEXT NOT NULL,
                technique TEXT,
                action TEXT,
                savings_usd REAL NOT NULL,
                tokens_saved INTEGER NOT NULL,
                details_json TEXT NOT NULL,
                FOREIGN KEY(request_event_id) REFERENCES metrics_events(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_metrics_opt_events_recorded_at
                ON metrics_optimization_events(recorded_at);
            CREATE INDEX IF NOT EXISTS idx_metrics_opt_events_opt_id
                ON metrics_optimization_events(optimization_id);
            """
        )
        self._conn.commit()

    def record_request(self, request_metrics: dict[str, Any]) -> None:
        now = time.time()
        updates = request_metrics.get("optimization_updates") or []
        with self._lock:
            cursor = self._conn.execute(
                """
                INSERT INTO metrics_events (
                    recorded_at,
                    cache_hit,
                    savings_usd,
                    cost_usd,
                    prompt_tokens,
                    completion_tokens,
                    total_tokens,
                    tokens_saved_estimate,
                    routed,
                    rejected,
                    latency_ms,
                    ttfb_ms,
                    stream_duration_ms
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    now,
                    1 if request_metrics.get("cache_hit") else 0,
                    float(request_metrics.get("savings_usd") or 0.0),
                    float(request_metrics.get("cost_usd") or 0.0),
                    int(request_metrics.get("prompt_tokens") or 0),
                    int(request_metrics.get("completion_tokens") or 0),
                    int(request_metrics.get("total_tokens") or 0),
                    int(request_metrics.get("tokens_saved_estimate") or 0),
                    1 if request_metrics.get("routed") else 0,
                    1 if request_metrics.get("rejected") else 0,
                    float(request_metrics.get("latency_ms") or 0.0),
                    float(request_metrics.get("ttfb_ms") or 0.0),
                    float(request_metrics.get("stream_duration_ms") or 0.0),
                ),
            )
            request_event_id = cursor.lastrowid
            for update in updates:
                self._conn.execute(
                    """
                    INSERT INTO metrics_optimization_events (
                        request_event_id,
                        recorded_at,
                        optimization_id,
                        technique,
                        action,
                        savings_usd,
                        tokens_saved,
                        details_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        request_event_id,
                        now,
                        str(update.get("optimization_id") or "unknown"),
                        update.get("technique"),
                        update.get("action"),
                        float(update.get("savings_usd") or 0.0),
                        int(update.get("tokens_saved") or 0),
                        json.dumps(update.get("details") or {}),
                    ),
                )
            self._conn.commit()

    @staticmethod
    def _window_clause(window: str) -> tuple[str, tuple[Any, ...]]:
        seconds = WINDOW_TO_SECONDS.get(window, WINDOW_TO_SECONDS["7d"])
        if seconds is None:
            return "", ()
        cutoff = time.time() - seconds
        return "WHERE recorded_at >= ?", (cutoff,)

    @staticmethod
    def _bucket_format(window: str) -> str:
        if window == "24h":
            return "%Y-%m-%dT%H:00:00Z"
        return "%Y-%m-%dT00:00:00Z"

    def summary(self) -> dict[str, Any]:
        with self._lock:
            row = self._conn.execute(
                """
                SELECT
                    COUNT(*) AS total_requests,
                    COALESCE(SUM(cache_hit), 0) AS cache_hits,
                    COALESCE(SUM(CASE WHEN cache_hit = 0 THEN 1 ELSE 0 END), 0) AS cache_misses,
                    COALESCE(SUM(savings_usd), 0.0) AS total_savings_usd,
                    COALESCE(SUM(cost_usd), 0.0) AS total_cost_usd,
                    COALESCE(SUM(prompt_tokens), 0) AS total_prompt_tokens,
                    COALESCE(SUM(completion_tokens), 0) AS total_completion_tokens,
                    COALESCE(SUM(total_tokens), 0) AS total_tokens,
                    COALESCE(SUM(tokens_saved_estimate), 0) AS total_tokens_saved_estimate,
                    COALESCE(SUM(routed), 0) AS requests_routed,
                    COALESCE(SUM(rejected), 0) AS requests_rejected,
                    COALESCE(AVG(NULLIF(ttfb_ms, 0)), 0.0) AS avg_ttfb_ms,
                    COALESCE(AVG(NULLIF(stream_duration_ms, 0)), 0.0) AS avg_stream_duration_ms,
                    COALESCE(MIN(recorded_at), 0.0) AS first_recorded_at
                FROM metrics_events
                """
            ).fetchone()

        total_requests = int(row["total_requests"])
        cache_hits = int(row["cache_hits"])
        cache_misses = int(row["cache_misses"])
        total_savings_usd = float(row["total_savings_usd"])
        cache_hit_rate = (cache_hits / (cache_hits + cache_misses) * 100.0) if (cache_hits + cache_misses) > 0 else 0.0
        avg_savings_per_request_usd = total_savings_usd / total_requests if total_requests > 0 else 0.0
        first_recorded_at = float(row["first_recorded_at"] or 0.0)
        uptime_seconds = max(0.0, time.time() - first_recorded_at) if first_recorded_at > 0 else 0.0
        return {
            "totals": {
                "total_requests": total_requests,
                "cache_hits": cache_hits,
                "cache_misses": cache_misses,
                "total_savings_usd": round(total_savings_usd, 6),
                "total_cost_usd": round(float(row["total_cost_usd"]), 6),
                "total_prompt_tokens": int(row["total_prompt_tokens"]),
                "total_completion_tokens": int(row["total_completion_tokens"]),
                "total_tokens": int(row["total_tokens"]),
                "total_tokens_saved_estimate": int(row["total_tokens_saved_estimate"]),
                "requests_routed": int(row["requests_routed"]),
                "requests_rejected": int(row["requests_rejected"]),
                "pipeline_errors": 0,
            },
            "rates": {
                "cache_hit_rate": round(cache_hit_rate, 2),
                "avg_savings_per_request_usd": round(avg_savings_per_request_usd, 6),
                "avg_ttfb_ms": round(float(row["avg_ttfb_ms"]), 2),
                "avg_stream_duration_ms": round(float(row["avg_stream_duration_ms"]), 2),
            },
            "uptime_seconds": round(uptime_seconds, 1),
        }

    def summary_v2(self, *, enabled_tabs: list[str], window: str = "7d") -> dict[str, Any]:
        where_clause, params = self._window_clause(window)
        bucket_format = self._bucket_format(window)
        with self._lock:
            overall = self._conn.execute(
                f"""
                SELECT
                    COUNT(*) AS total_requests,
                    COALESCE(SUM(savings_usd), 0.0) AS total_savings_usd,
                    COALESCE(SUM(tokens_saved_estimate), 0) AS total_tokens_saved_estimate
                FROM metrics_events
                {where_clause}
                """,
                params,
            ).fetchone()
            first_recorded = self._conn.execute("SELECT COALESCE(MIN(recorded_at), 0.0) AS first_recorded_at FROM metrics_events").fetchone()

            optimization_rows = self._conn.execute(
                f"""
                SELECT
                    optimization_id,
                    COUNT(*) AS events,
                    COALESCE(SUM(savings_usd), 0.0) AS total_savings_usd,
                    COALESCE(SUM(tokens_saved), 0) AS total_tokens_saved
                FROM metrics_optimization_events
                {where_clause}
                GROUP BY optimization_id
                """,
                params,
            ).fetchall()

            latest_rows = self._conn.execute(
                """
                SELECT
                    optimization_id,
                    technique,
                    action,
                    details_json
                FROM metrics_optimization_events
                WHERE id IN (
                    SELECT MAX(id) FROM metrics_optimization_events GROUP BY optimization_id
                )
                """
            ).fetchall()

            series_rows = self._conn.execute(
                f"""
                SELECT
                    strftime('{bucket_format}', recorded_at, 'unixepoch') AS bucket,
                    COUNT(*) AS total_requests,
                    COALESCE(SUM(savings_usd), 0.0) AS total_savings_usd,
                    COALESCE(SUM(tokens_saved_estimate), 0) AS total_tokens_saved_estimate
                FROM metrics_events
                {where_clause}
                GROUP BY bucket
                ORDER BY bucket
                """,
                params,
            ).fetchall()

            optimization_series_rows = self._conn.execute(
                f"""
                SELECT
                    optimization_id,
                    strftime('{bucket_format}', recorded_at, 'unixepoch') AS bucket,
                    COUNT(*) AS events,
                    COALESCE(SUM(savings_usd), 0.0) AS total_savings_usd,
                    COALESCE(SUM(tokens_saved), 0) AS total_tokens_saved
                FROM metrics_optimization_events
                {where_clause}
                GROUP BY optimization_id, bucket
                ORDER BY optimization_id, bucket
                """,
                params,
            ).fetchall()

        latest_map = {
            str(row["optimization_id"]): {
                "last_technique": row["technique"],
                "last_action": row["action"],
                "last_details": json.loads(row["details_json"]) if row["details_json"] else {},
            }
            for row in latest_rows
        }
        observed = {
            str(row["optimization_id"]): {
                "optimization_id": str(row["optimization_id"]),
                "events": int(row["events"]),
                "total_savings_usd": round(float(row["total_savings_usd"]), 6),
                "total_tokens_saved": int(row["total_tokens_saved"]),
                "tokens_saved": int(row["total_tokens_saved"]),
                **latest_map.get(str(row["optimization_id"]), {
                    "last_technique": None,
                    "last_action": None,
                    "last_details": {},
                }),
            }
            for row in optimization_rows
        }
        for optimization_id in enabled_tabs:
            observed.setdefault(
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

        optimization_series_map: dict[str, list[dict[str, Any]]] = {}
        for row in optimization_series_rows:
            optimization_id = str(row["optimization_id"])
            optimization_series_map.setdefault(optimization_id, []).append(
                {
                    "bucket": str(row["bucket"]),
                    "events": int(row["events"]),
                    "total_savings_usd": round(float(row["total_savings_usd"]), 6),
                    "total_tokens_saved": int(row["total_tokens_saved"]),
                }
            )

        first_recorded_at = float(first_recorded["first_recorded_at"] or 0.0)
        uptime_seconds = max(0.0, time.time() - first_recorded_at) if first_recorded_at > 0 else 0.0

        return {
            "overall": {
                "total_savings_usd": round(float(overall["total_savings_usd"]), 6),
                "total_tokens_saved_estimate": int(overall["total_tokens_saved_estimate"]),
                "total_requests": int(overall["total_requests"]),
                "uptime_seconds": round(uptime_seconds, 1),
            },
            "window": window if window in WINDOW_TO_SECONDS else "7d",
            "enabled_tabs": enabled_tabs,
            "optimizations": sorted(
                [entry for entry in observed.values() if entry["optimization_id"] != "forward"],
                key=lambda entry: entry["optimization_id"],
            ),
            "series": [
                {
                    "bucket": str(row["bucket"]),
                    "total_requests": int(row["total_requests"]),
                    "total_savings_usd": round(float(row["total_savings_usd"]), 6),
                    "total_tokens_saved_estimate": int(row["total_tokens_saved_estimate"]),
                }
                for row in series_rows
            ],
            "optimization_series": [
                {
                    "optimization_id": optimization_id,
                    "points": points,
                }
                for optimization_id, points in sorted(optimization_series_map.items())
                if optimization_id != "forward"
            ],
        }
