#!/usr/bin/env python3
"""Aggregate profile-matrix benchmark runs into one production-style summary."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from benchmarks.gemini_config import DEFAULT_MATRIX_OUT


def _fmt_num(value: Any, suffix: str = "") -> str:
    if value is None:
        return "n/a"
    if isinstance(value, float):
        if 0 < abs(value) < 0.01:
            text = f"{value:.6f}"
        elif abs(value) >= 1000:
            text = f"{value:,.1f}"
        else:
            text = f"{value:.3f}"
        return text.rstrip("0").rstrip(".") + suffix
    if isinstance(value, int):
        return f"{value:,}{suffix}"
    return f"{value}{suffix}"


def _extract_entry(run_dir: Path) -> dict[str, Any]:
    report = json.loads((run_dir / "report.json").read_text(encoding="utf-8"))
    name = run_dir.name
    profile, mode = name.split("__", 1) if "__" in name else (name, "unknown")
    cost = report.get("cost", {})
    steady = report.get("steady_state", {})
    cache = report.get("cache", {})
    lat = report.get("latency", {})
    tokens = report.get("tokens", {}).get("totals", {})
    return {
        "profile": profile,
        "mode": mode,
        "run_dir": str(run_dir),
        "rows": report.get("run", {}).get("case_count"),
        "cache_hit_rate": cache.get("cache_hit_rate"),
        "latency_savings_pct": lat.get("p50_savings_pct"),
        "cost_savings_pct": cost.get("cost_savings_pct"),
        "cost_savings_usd": cost.get("cost_savings_usd"),
        "token_total_savings_pct": tokens.get("total_savings_pct"),
        "steady_rows": steady.get("row_count"),
        "steady_cache_hit_rate": steady.get("cache_hit_rate"),
        "steady_cost_savings_pct": steady.get("cost_savings_pct"),
        "steady_cost_savings_usd": steady.get("cost_savings_usd"),
        "steady_token_savings_pct": steady.get("token_total_savings_pct_billed"),
    }


def _load_entries(out_root: Path) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for path in sorted(out_root.iterdir()):
        if not path.is_dir():
            continue
        report_path = path / "report.json"
        if report_path.exists():
            entries.append(_extract_entry(path))
    return entries


def _best_steady_cost(entries: list[dict[str, Any]]) -> tuple[dict[str, Any] | None, float | None]:
    eligible = [
        e
        for e in entries
        if isinstance(e.get("steady_cost_savings_pct"), (int, float))
    ]
    if not eligible:
        return None, None
    best = max(eligible, key=lambda e: float(e["steady_cost_savings_pct"]))
    return best, float(best["steady_cost_savings_pct"])


def _render(entries: list[dict[str, Any]], title: str) -> str:
    lines: list[str] = []
    lines.append(f"# {title}")
    lines.append("")
    lines.append(
        "Warmup-inclusive metrics show full run economics. "
        "Steady-state metrics exclude first occurrence of each unique prompt fingerprint."
    )
    lines.append("")
    lines.append("## Warmup-inclusive")
    lines.append("")
    lines.append("| Profile | Mode | Rows | Cache hit % | Cost savings % | Cost savings USD | Token savings % | Latency savings % |")
    lines.append("|---|---|---:|---:|---:|---:|---:|---:|")
    for e in entries:
        lines.append(
            f"| {e['profile']} | {e['mode']} | {_fmt_num(e['rows'])} | "
            f"{_fmt_num((e['cache_hit_rate'] or 0) * 100, '%')} | "
            f"{_fmt_num(e['cost_savings_pct'], '%')} | "
            f"${_fmt_num(e['cost_savings_usd'])} | "
            f"{_fmt_num(e['token_total_savings_pct'], '%')} | "
            f"{_fmt_num(e['latency_savings_pct'], '%')} |"
        )

    lines.append("")
    lines.append("## Steady-state (production-like ongoing traffic)")
    lines.append("")
    lines.append("| Profile | Mode | Steady rows | Steady cache hit % | Steady cost savings % | Steady cost savings USD | Steady token savings % |")
    lines.append("|---|---|---:|---:|---:|---:|---:|")
    for e in entries:
        lines.append(
            f"| {e['profile']} | {e['mode']} | {_fmt_num(e['steady_rows'])} | "
            f"{_fmt_num((e['steady_cache_hit_rate'] or 0) * 100, '%')} | "
            f"{_fmt_num(e['steady_cost_savings_pct'], '%')} | "
            f"${_fmt_num(e['steady_cost_savings_usd'])} | "
            f"{_fmt_num(e['steady_token_savings_pct'], '%')} |"
        )

    best, best_pct = _best_steady_cost(entries)
    lines.append("")
    lines.append("## 40%+ savings verdict")
    lines.append("")
    if best is None:
        lines.append("- No steady-state cost savings metrics were available.")
    else:
        reached = best_pct is not None and best_pct >= 40.0
        lines.append(
            f"- Best steady-state cost savings: **{_fmt_num(best_pct, '%')}** "
            f"({best['profile']} / {best['mode']})."
        )
        lines.append(
            f"- 40%+ target reached: **{'YES' if reached else 'NO'}**."
        )
    lines.append("")
    return "\n".join(lines) + "\n"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Summarize profile-matrix benchmark runs.")
    parser.add_argument("--runs-root", type=Path, default=DEFAULT_MATRIX_OUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_MATRIX_OUT / "SUMMARY.md")
    parser.add_argument(
        "--title",
        default="Production-like benchmark summary",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if not args.runs_root.exists():
        raise SystemExit(f"Runs root not found: {args.runs_root}")

    entries = _load_entries(args.runs_root)
    if not entries:
        raise SystemExit(f"No run reports found in: {args.runs_root}")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(_render(entries, args.title), encoding="utf-8")
    print(f"Wrote {args.output}")


if __name__ == "__main__":
    main()
