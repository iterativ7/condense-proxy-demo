#!/usr/bin/env python3
"""Run a repeatable two-service Condense A/B benchmark.

This helper starts:
1) semantic-only Condense service
2) no-optimization Condense service

Then it runs a Dolly benchmark slice where baseline=service(2) and
proxy=service(1), and writes standard run artifacts (results/report/REPORT).
"""

from __future__ import annotations

import argparse
import json
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SEMANTIC_CONFIG = REPO_ROOT / "condense.local.yaml"
DEFAULT_NOOPT_CONFIG = REPO_ROOT / "condense.local.no_opt.yaml"


def _timestamp() -> str:
    return time.strftime("%Y%m%dT%H%M%S")


def _parse_port(url: str) -> int:
    parsed = urlparse(url)
    if parsed.port is None:
        raise ValueError(f"Cannot parse port from url: {url}")
    return int(parsed.port)


def _pids_on_port(port: int) -> list[int]:
    result = subprocess.run(
        ["lsof", "-ti", f"tcp:{port}"],
        check=False,
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
    )
    if result.returncode != 0 or not result.stdout.strip():
        return []
    pids: list[int] = []
    for line in result.stdout.splitlines():
        line = line.strip()
        if line.isdigit():
            pids.append(int(line))
    return pids


def _kill_port(port: int) -> None:
    pids = _pids_on_port(port)
    if not pids:
        return
    for pid in pids:
        try:
            Path(f"/proc/{pid}")
        except Exception:
            pass
        try:
            import os

            os.kill(pid, signal.SIGTERM)
        except OSError:
            continue


def _wait_healthy(health_url: str, timeout_s: float = 60.0) -> None:
    started = time.time()
    last_err = ""
    with httpx.Client(timeout=5.0) as client:
        while time.time() - started < timeout_s:
            try:
                resp = client.get(health_url)
                if resp.status_code == 200:
                    return
                last_err = f"status={resp.status_code}"
            except Exception as exc:  # noqa: BLE001
                last_err = str(exc)
            time.sleep(0.5)
    raise TimeoutError(f"Service not healthy at {health_url} ({last_err})")


def _start_condense(config_path: Path) -> subprocess.Popen[str]:
    cmd = [str(REPO_ROOT / ".venv" / "bin" / "condense"), "start", "--config", str(config_path)]
    return subprocess.Popen(  # noqa: S603
        cmd,
        cwd=str(REPO_ROOT),
        # Avoid deadlock from unconsumed PIPE when services log heavily.
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        text=True,
    )


def _stop_proc(proc: subprocess.Popen[str] | None) -> None:
    if proc is None or proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=5)


def _run_benchmark(
    *,
    limit: int,
    out_dir: Path,
    baseline_url: str,
    proxy_url: str,
    baseline_model: str,
    proxy_model: str,
    max_retries: int,
    concurrency: int,
) -> None:
    cmd = [
        str(REPO_ROOT / ".venv" / "bin" / "python"),
        "benchmarks/run_paired_dolly_runtime.py",
        "--limit",
        str(limit),
        "--",
        "--out-dir",
        str(out_dir),
        "--preset-label",
        f"dolly condense ab {limit}",
        "--baseline-url",
        f"{baseline_url}/v1/chat/completions",
        "--proxy-url",
        f"{proxy_url}/v1/chat/completions",
        "--baseline-model",
        baseline_model,
        "--proxy-model",
        proxy_model,
        "--max-retries",
        str(max_retries),
        "--concurrency",
        str(concurrency),
    ]
    result = subprocess.run(cmd, cwd=str(REPO_ROOT), check=False)  # noqa: S603
    if result.returncode != 0:
        raise RuntimeError(f"Benchmark command failed with code {result.returncode}")


def _print_summary(report_path: Path) -> None:
    report = json.loads(report_path.read_text(encoding="utf-8"))
    cache = report.get("cache", {})
    latency = report.get("latency", {})
    tokens = report.get("tokens", {}).get("totals", {})
    cost = report.get("cost", {})
    print("\nA/B summary")
    print(f"- report: {report_path}")
    print(
        f"- cache_hit_rate: {cache.get('cache_hit_rate')} "
        f"({cache.get('cache_hit_count')}/{cache.get('cache_observed_count')})"
    )
    print(
        f"- p50_latency_ms baseline={latency.get('baseline', {}).get('p50')} "
        f"proxy={latency.get('proxy', {}).get('p50')}"
    )
    print(
        f"- total_tokens baseline={tokens.get('baseline_total_tokens')} "
        f"proxy={tokens.get('proxy_total_tokens')} "
        f"savings_pct={tokens.get('total_savings_pct')}"
    )
    print(
        f"- cost_savings_usd={cost.get('cost_savings_usd')} "
        f"cost_savings_pct={cost.get('cost_savings_pct')}"
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run two-service Condense A/B benchmark.")
    parser.add_argument("--limit", type=int, default=10, help="Number of Dolly rows to benchmark.")
    parser.add_argument(
        "--semantic-config",
        type=Path,
        default=DEFAULT_SEMANTIC_CONFIG,
        help="Condense config for semantic-only service.",
    )
    parser.add_argument(
        "--noopt-config",
        type=Path,
        default=DEFAULT_NOOPT_CONFIG,
        help="Condense config for no-optimization service.",
    )
    parser.add_argument(
        "--semantic-url",
        default="http://127.0.0.1:8090",
        help="Semantic service base URL.",
    )
    parser.add_argument(
        "--noopt-url",
        default="http://127.0.0.1:8091",
        help="No-opt service base URL.",
    )
    parser.add_argument(
        "--model",
        default="ollama/gemma3:4b",
        help="Model name for both baseline/proxy requests.",
    )
    parser.add_argument(
        "--max-retries",
        type=int,
        default=1,
        help="Retries per request in run_paired.",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=1,
        help="Concurrent benchmark case workers passed to run_paired.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("benchmarks/runs") / f"dolly-two-condense-ab-{_timestamp()}",
        help="Output directory for report artifacts.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.limit <= 0:
        raise SystemExit("--limit must be positive")
    if args.concurrency <= 0:
        raise SystemExit("--concurrency must be positive")
    if not args.semantic_config.exists():
        raise SystemExit(f"semantic config not found: {args.semantic_config}")
    if not args.noopt_config.exists():
        raise SystemExit(f"no-opt config not found: {args.noopt_config}")

    semantic_port = _parse_port(args.semantic_url)
    noopt_port = _parse_port(args.noopt_url)
    _kill_port(semantic_port)
    _kill_port(noopt_port)

    semantic_proc: subprocess.Popen[str] | None = None
    noopt_proc: subprocess.Popen[str] | None = None
    try:
        semantic_proc = _start_condense(args.semantic_config)
        noopt_proc = _start_condense(args.noopt_config)
        _wait_healthy(f"{args.semantic_url}/health")
        _wait_healthy(f"{args.noopt_url}/health")

        _run_benchmark(
            limit=args.limit,
            out_dir=args.output_dir,
            baseline_url=args.noopt_url,
            proxy_url=args.semantic_url,
            baseline_model=args.model,
            proxy_model=args.model,
            max_retries=args.max_retries,
            concurrency=args.concurrency,
        )
        report_path = args.output_dir / "report.json"
        _print_summary(report_path)
        return 0
    finally:
        _stop_proc(semantic_proc)
        _stop_proc(noopt_proc)


if __name__ == "__main__":
    raise SystemExit(main())

