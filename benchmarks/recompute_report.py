#!/usr/bin/env python3
"""Recompute report.json and REPORT.md from an existing results.jsonl.

Useful for re-aggregating with newer metrics (totals, percentiles, cost,
generation savings) without re-running the model.

Examples:
    python benchmarks/recompute_report.py benchmarks/runs/local-minimal-gemma-50
    python benchmarks/recompute_report.py benchmarks/runs/local-minimal-gemma-50 \
        --price-input-per-1k 0.075 --price-output-per-1k 0.30 \
        --preset-label "Minimal (gemma3:4b, 50 cases)"
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

# Local import (this script lives next to run_paired.py).
HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from run_paired import (  # noqa: E402
    _build_report,
    _quality_result,
    write_report_md,
)


def _load_results(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{line_no} invalid JSON: {exc}") from exc
    return rows


def _backfill_token_fields(call: dict[str, Any]) -> None:
    """Older results.jsonl lacks top-level prompt/completion/total_tokens; lift from usage."""
    if not isinstance(call, dict):
        return
    usage = call.get("usage") or {}
    for field in ("prompt_tokens", "completion_tokens", "total_tokens"):
        if call.get(field) is None and isinstance(usage.get(field), int | float):
            call[field] = int(usage[field])


def _backfill_quality(rows: list[dict[str, Any]]) -> None:
    """Recompute quality blocks with the upgraded extractor for fairness."""
    for row in rows:
        ref = row.get("reference") or {}
        baseline_text = (row.get("baseline") or {}).get("assistant_text", "")
        proxy_text = (row.get("proxy") or {}).get("assistant_text", "")
        row["quality"] = {
            "baseline": _quality_result(ref, baseline_text),
            "proxy": _quality_result(ref, proxy_text),
        }


def _ensure_prime_field(rows: list[dict[str, Any]]) -> None:
    for row in rows:
        if "prime" not in row or not isinstance(row["prime"], dict):
            row["prime"] = {"attempted": False}


def main() -> int:
    parser = argparse.ArgumentParser(description="Recompute report.json + REPORT.md from results.jsonl")
    parser.add_argument("run_dir", type=Path, help="Run directory containing results.jsonl")
    parser.add_argument("--results", type=Path, default=None, help="Override path to results.jsonl")
    parser.add_argument("--price-input-per-1k", type=float, default=0.0)
    parser.add_argument("--price-output-per-1k", type=float, default=0.0)
    parser.add_argument("--preset-label", default=None)
    parser.add_argument("--baseline-model", default=None)
    parser.add_argument("--proxy-model", default=None)
    parser.add_argument("--baseline-url", default=None)
    parser.add_argument("--proxy-url", default=None)
    args = parser.parse_args()

    run_dir: Path = args.run_dir
    if not run_dir.exists():
        raise SystemExit(f"Run dir not found: {run_dir}")

    results_path = args.results or run_dir / "results.jsonl"
    if not results_path.exists():
        raise SystemExit(f"results.jsonl not found: {results_path}")

    rows = _load_results(results_path)
    if not rows:
        raise SystemExit(f"No rows in {results_path}")

    for row in rows:
        _backfill_token_fields(row.get("baseline") or {})
        _backfill_token_fields(row.get("proxy") or {})
    _ensure_prime_field(rows)
    _backfill_quality(rows)

    existing_report_path = run_dir / "report.json"
    existing_run_block = {}
    if existing_report_path.exists():
        try:
            existing_run_block = json.loads(existing_report_path.read_text(encoding="utf-8")).get("run", {})
        except json.JSONDecodeError:
            existing_run_block = {}

    args_ns = SimpleNamespace(
        dataset=existing_run_block.get("dataset", str(results_path)),
        out_dir=run_dir,
        baseline_url=args.baseline_url or existing_run_block.get("baseline_url", "n/a"),
        proxy_url=args.proxy_url or existing_run_block.get("proxy_url", "n/a"),
        baseline_model=args.baseline_model or existing_run_block.get("baseline_model"),
        proxy_model=args.proxy_model or existing_run_block.get("proxy_model"),
        price_input_per_1k=args.price_input_per_1k,
        price_output_per_1k=args.price_output_per_1k,
        preset_label=args.preset_label,
    )

    started_at = existing_run_block.get("started_at", "unknown")
    completed_at = existing_run_block.get("completed_at", "unknown")

    report = _build_report(rows, args_ns, started_at, completed_at)

    out_json = run_dir / "report.json"
    out_md = run_dir / "REPORT.md"
    out_json.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    write_report_md(out_md, report)

    print(f"Wrote {out_json}")
    print(f"Wrote {out_md}")
    print(
        "Summary: "
        f"cases={report['run']['case_count']} "
        f"p50_baseline_ms={report['latency']['baseline'].get('p50')} "
        f"p50_proxy_ms={report['latency']['proxy'].get('p50')} "
        f"speedup={report['latency'].get('p50_speedup_factor')}x "
        f"cache_hit_rate={report['cache'].get('cache_hit_rate')} "
        f"token_savings_pct={report['tokens']['totals'].get('total_savings_pct')}%"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
