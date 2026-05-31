#!/usr/bin/env python3
"""Compare steady-state metrics across two profile-matrix run roots."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from benchmarks.summarize_profile_matrix import _extract_entry, _fmt_num, _load_entries


def _index(entries: list[dict[str, Any]]) -> dict[tuple[str, str], dict[str, Any]]:
    return {(e["profile"], e["mode"]): e for e in entries}


def _render(
    left_label: str,
    right_label: str,
    left: list[dict[str, Any]],
    right: list[dict[str, Any]],
) -> str:
    left_map = _index(left)
    right_map = _index(right)
    keys = sorted(set(left_map) | set(right_map))

    lines: list[str] = []
    lines.append(f"# Matrix comparison: {left_label} vs {right_label}")
    lines.append("")
    lines.append(
        "Steady-state deltas (right minus left). "
        "Positive cost savings % delta means right mode saved more vs baseline."
    )
    lines.append("")
    lines.append(
        "| Profile | Mode | "
        f"Left steady cost % | Right steady cost % | Delta cost % | "
        f"Left cache hit % | Right cache hit % | Delta cache % |"
    )
    lines.append("|---|---|---:|---:|---:|---:|---:|---:|")

    for profile, mode in keys:
        l = left_map.get((profile, mode))
        r = right_map.get((profile, mode))
        l_cost = l.get("steady_cost_savings_pct") if l else None
        r_cost = r.get("steady_cost_savings_pct") if r else None
        l_cache = l.get("steady_cache_hit_rate") if l else None
        r_cache = r.get("steady_cache_hit_rate") if r else None
        delta_cost = None
        delta_cache = None
        if isinstance(l_cost, (int, float)) and isinstance(r_cost, (int, float)):
            delta_cost = float(r_cost) - float(l_cost)
        if isinstance(l_cache, (int, float)) and isinstance(r_cache, (int, float)):
            delta_cache = (float(r_cache) - float(l_cache)) * 100.0

        lines.append(
            f"| {profile} | {mode} | "
            f"{_fmt_num(l_cost, '%') if l else 'n/a'} | "
            f"{_fmt_num(r_cost, '%') if r else 'n/a'} | "
            f"{_fmt_num(delta_cost, '%') if delta_cost is not None else 'n/a'} | "
            f"{_fmt_num((l_cache or 0) * 100, '%') if l else 'n/a'} | "
            f"{_fmt_num((r_cache or 0) * 100, '%') if r else 'n/a'} | "
            f"{_fmt_num(delta_cache, '%') if delta_cache is not None else 'n/a'} |"
        )

    lines.append("")
    return "\n".join(lines) + "\n"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Compare two matrix run roots.")
    parser.add_argument("--left-root", type=Path, required=True)
    parser.add_argument("--right-root", type=Path, required=True)
    parser.add_argument("--left-label", default="left")
    parser.add_argument("--right-label", default="right")
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if not args.left_root.exists():
        raise SystemExit(f"Left root not found: {args.left_root}")
    if not args.right_root.exists():
        raise SystemExit(f"Right root not found: {args.right_root}")

    left_entries = _load_entries(args.left_root)
    right_entries = _load_entries(args.right_root)
    if not left_entries:
        raise SystemExit(f"No reports in left root: {args.left_root}")
    if not right_entries:
        raise SystemExit(f"No reports in right root: {args.right_root}")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    text = _render(args.left_label, args.right_label, left_entries, right_entries)
    args.output.write_text(text, encoding="utf-8")

    payload = {
        "left": args.left_label,
        "right": args.right_label,
        "left_root": str(args.left_root),
        "right_root": str(args.right_root),
        "left_entries": len(left_entries),
        "right_entries": len(right_entries),
    }
    args.output.with_suffix(".json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {args.output}")


if __name__ == "__main__":
    main()
