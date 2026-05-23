#!/usr/bin/env python3
"""Build a side-by-side comparison SUMMARY.md across multiple run directories.

Usage:
    python benchmarks/compare_runs.py \
        --output benchmarks/runs/SUMMARY.md \
        benchmarks/runs/gemini-minimal-suite50 \
        benchmarks/runs/gemini-cache-only-suite50-primed \
        benchmarks/runs/gemini-full-suite50
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def _load(p: Path) -> dict[str, Any]:
    return json.loads((p / "report.json").read_text(encoding="utf-8"))


def _pct(v: Any) -> str:
    if v is None:
        return "n/a"
    if isinstance(v, float):
        s = f"{v:.3f}".rstrip("0").rstrip(".")
        return f"{s}%"
    return f"{v}%"


def _ms(v: Any) -> str:
    if v is None:
        return "n/a"
    if isinstance(v, int | float):
        if abs(v) >= 1000:
            return f"{v:,.1f} ms"
        text = f"{v:.3f}".rstrip("0").rstrip(".")
        return f"{text} ms"
    return str(v)


def _num(v: Any) -> str:
    if v is None:
        return "n/a"
    if isinstance(v, float):
        if abs(v) >= 1000:
            return f"{v:,.1f}"
        s = f"{v:.3f}".rstrip("0").rstrip(".")
        return s
    if isinstance(v, int):
        return f"{v:,}"
    return str(v)


def _usd(v: Any) -> str:
    if v is None:
        return "n/a"
    return f"${v:,.4f}".rstrip("0").rstrip(".")


def build_summary(run_dirs: list[Path], title: str = "Benchmark Summary") -> str:
    reports = []
    for d in run_dirs:
        try:
            reports.append((d, _load(d)))
        except FileNotFoundError:
            print(f"warning: {d}/report.json not found, skipping")
            continue

    if not reports:
        return "_No reports found._\n"

    headers = ["Metric"] + [
        (r["run"].get("preset_label") or d.name) for d, r in reports
    ]

    rows: list[list[str]] = []

    def add(label: str, fn) -> None:
        rows.append([label] + [fn(r) for _, r in reports])

    add("Cases", lambda r: str(r["run"]["case_count"]))
    add("Cache hit rate", lambda r: _pct((r["cache"].get("cache_hit_rate") or 0) * 100))
    add("Latency p50 (baseline)", lambda r: _ms(r["latency"]["baseline"].get("p50")))
    add("Latency p50 (proxy)", lambda r: _ms(r["latency"]["proxy"].get("p50")))
    add("Latency p95 (proxy)", lambda r: _ms(r["latency"]["proxy"].get("p95")))
    add("Latency p99 (proxy)", lambda r: _ms(r["latency"]["proxy"].get("p99")))
    add("p50 speedup factor", lambda r: f"{r['latency'].get('p50_speedup_factor')}x" if r["latency"].get("p50_speedup_factor") is not None else "n/a")
    add("Tokens — input total (baseline)", lambda r: _num(r["tokens"]["totals"].get("baseline_input_tokens")))
    add("Tokens — input total (proxy)", lambda r: _num(r["tokens"]["totals"].get("proxy_input_tokens")))
    add("Tokens — output total (baseline)", lambda r: _num(r["tokens"]["totals"].get("baseline_output_tokens")))
    add("Tokens — output total (proxy)", lambda r: _num(r["tokens"]["totals"].get("proxy_output_tokens")))
    add("Tokens — total (baseline)", lambda r: _num(r["tokens"]["totals"].get("baseline_total_tokens")))
    add("Tokens — total (proxy)", lambda r: _num(r["tokens"]["totals"].get("proxy_total_tokens")))
    add("Token total savings %", lambda r: _pct(r["tokens"]["totals"].get("total_savings_pct")))
    add("Output savings (est., generation only)", lambda r: _pct(r["tokens"]["generation_savings"].get("completion_tokens_savings_pct_est")))
    add("Completion tokens avoided by cache (est.)", lambda r: _num(r["tokens"]["generation_savings"].get("completion_tokens_avoided_by_cache_est")))
    add("Quality — baseline pass rate", lambda r: _pct((r["quality"].get("baseline_quality_pass_rate") or 0) * 100))
    add("Quality — proxy pass rate", lambda r: _pct((r["quality"].get("proxy_quality_pass_rate") or 0) * 100))
    add("Quality — agreement", lambda r: _pct((r["quality"].get("agreement_rate_proxy_vs_baseline") or 0) * 100))
    add("Cost (baseline) USD", lambda r: _usd(r["cost"].get("baseline_cost")) if r["cost"].get("configured") else "n/a")
    add("Cost (proxy + prime) USD", lambda r: _usd(r["cost"].get("proxy_cost_with_prime")) if r["cost"].get("configured") else "n/a")
    add("Cost saved USD", lambda r: _usd(r["cost"].get("cost_savings_usd")) if r["cost"].get("configured") else "n/a")
    add("Cost savings %", lambda r: _pct(r["cost"].get("cost_savings_pct")) if r["cost"].get("configured") else "n/a")

    # Build markdown
    out: list[str] = []
    out.append(f"# {title}")
    out.append("")
    out.append("Side-by-side comparison of paired baseline (direct upstream) vs Condense proxy.")
    out.append("")
    out.append("| " + " | ".join(headers) + " |")
    out.append("|" + "|".join(["---"] * len(headers)) + "|")
    for row in rows:
        out.append("| " + " | ".join(row) + " |")
    out.append("")
    out.append("## Notes")
    out.append("")
    out.append("- **Token total savings %** is computed from sum of `usage.total_tokens`. "
               "When the proxy serves from cache, the response still contains the original "
               "`usage` block, so the difference here is largely about cached vs fresh outputs, "
               "not necessarily compute saved.")
    out.append("- **Output savings (est., generation only)** is the most honest savings metric: "
               "it credits cache hits with avoiding the baseline's completion tokens, and "
               "subtracts proxy generations on cache misses and any priming generations "
               "(when the runner tracked them).")
    out.append("- **Quality agreement** is the fraction of cases where the proxy's extracted "
               "answer equals the baseline's extracted answer (regardless of correctness).")
    out.append("- **Latency** percentiles are end-to-end HTTP round-trip times from the client.")
    out.append("")
    return "\n".join(out)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("run_dirs", nargs="+", type=Path)
    parser.add_argument("--output", type=Path, default=Path("benchmarks/runs/SUMMARY.md"))
    parser.add_argument("--title", default="Benchmark Summary")
    args = parser.parse_args()

    md = build_summary(list(args.run_dirs), args.title)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(md, encoding="utf-8")
    print(f"Wrote {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
