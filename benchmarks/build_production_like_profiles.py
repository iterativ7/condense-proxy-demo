#!/usr/bin/env python3
"""Build production-like benchmark profiles from converted public datasets."""

from __future__ import annotations

import argparse
import copy
import json
import random
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from benchmarks.gemini_config import CONVERTED_DIR, GEMINI_PROXY_MODEL, HEAVY_UNIQUE_DATASET

CONVERTED = CONVERTED_DIR
HEAVY_UNIQUE = HEAVY_UNIQUE_DATASET


@dataclass(frozen=True)
class ProfileSpec:
    name: str
    total_rows: int
    unique_ratio: float
    hotset_ratio: float
    avg_session_len: int


PROFILES: tuple[ProfileSpec, ...] = (
    ProfileSpec(
        name="support_faq_high_repeat",
        total_rows=160,
        unique_ratio=0.2,
        hotset_ratio=0.2,
        avg_session_len=6,
    ),
    ProfileSpec(
        name="mixed_app_medium_repeat",
        total_rows=120,
        unique_ratio=0.5,
        hotset_ratio=0.35,
        avg_session_len=4,
    ),
    ProfileSpec(
        name="mostly_unique_low_repeat",
        total_rows=80,
        unique_ratio=0.85,
        hotset_ratio=0.6,
        avg_session_len=3,
    ),
)


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            stripped = line.strip()
            if stripped:
                rows.append(json.loads(stripped))
    return rows


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    return len(rows)


def _fingerprint_row(row: dict[str, Any]) -> str:
    request = row.get("request", {})
    payload = {
        "model": request.get("model"),
        "messages": request.get("messages"),
        "temperature": request.get("temperature"),
        "max_tokens": request.get("max_tokens"),
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def _prepare_row(base: dict[str, Any], *, profile: str, idx: int, session_id: str, turn: int) -> dict[str, Any]:
    row = copy.deepcopy(base)
    request = row.setdefault("request", {})
    request["model"] = GEMINI_PROXY_MODEL
    metadata = row.setdefault("metadata", {})
    base_id = str(base.get("id", f"case_{idx:04d}"))
    row["id"] = f"{profile}_{idx:04d}_{base_id}"
    metadata["traffic_profile"] = profile
    metadata["session_id"] = session_id
    metadata["session_turn"] = turn
    return row


def _build_profile_rows(unique_rows: list[dict[str, Any]], spec: ProfileSpec, seed: int) -> list[dict[str, Any]]:
    if not unique_rows:
        raise ValueError("No source rows available.")
    rng = random.Random(seed)
    pool = list(unique_rows)
    rng.shuffle(pool)

    unique_target = max(1, min(spec.total_rows, int(round(spec.total_rows * spec.unique_ratio))))
    repeat_target = spec.total_rows - unique_target

    unique_seed_rows = pool[: min(unique_target, len(pool))]
    if len(unique_seed_rows) < unique_target:
        unique_seed_rows.extend(rng.choices(pool, k=unique_target - len(unique_seed_rows)))

    hotset_size = max(1, min(len(unique_seed_rows), int(round(len(unique_seed_rows) * spec.hotset_ratio))))
    hotset = unique_seed_rows[:hotset_size]

    repeated_rows: list[dict[str, Any]] = []
    if repeat_target > 0:
        weights = [max(1.0, (hotset_size - i)) for i in range(hotset_size)]
        repeated_rows = rng.choices(hotset, weights=weights, k=repeat_target)

    combined = unique_seed_rows + repeated_rows
    rng.shuffle(combined)

    # Session-like ordering: grouped contiguous turns per pseudo session.
    session_rows: list[dict[str, Any]] = []
    cursor = 0
    session_idx = 0
    while cursor < len(combined):
        session_len = max(1, int(rng.gauss(spec.avg_session_len, 1.0)))
        end = min(len(combined), cursor + session_len)
        session_id = f"{spec.name}_s{session_idx:03d}"
        for turn, base in enumerate(combined[cursor:end], start=1):
            session_rows.append(
                _prepare_row(
                    base,
                    profile=spec.name,
                    idx=len(session_rows),
                    session_id=session_id,
                    turn=turn,
                )
            )
        cursor = end
        session_idx += 1

    return session_rows


def _ensure_heavy_unique_exists() -> None:
    if HEAVY_UNIQUE.exists():
        return
    cmd = [sys.executable, str(ROOT / "benchmarks/build_heavy_token_dataset.py")]
    result = subprocess.run(cmd, cwd=ROOT)
    if result.returncode != 0 or not HEAVY_UNIQUE.exists():
        raise SystemExit("Unable to generate heavy source dataset. Run build_heavy_token_dataset.py first.")


def _load_source_pool() -> list[dict[str, Any]]:
    _ensure_heavy_unique_exists()
    return _load_jsonl(HEAVY_UNIQUE)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build production-like traffic profiles from public datasets.")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=CONVERTED,
        help="Directory for generated profile JSONL files.",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=42,
        help="Random seed for deterministic profile generation.",
    )
    parser.add_argument(
        "--total-rows",
        type=int,
        default=None,
        help="Override rows per profile (applies to all profiles).",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    source_rows = _load_source_pool()

    rel_source = HEAVY_UNIQUE.relative_to(ROOT) if HEAVY_UNIQUE.is_relative_to(ROOT) else HEAVY_UNIQUE
    manifest: dict[str, Any] = {
        "source": str(rel_source).replace("\\", "/"),
        "profiles": {},
    }

    for i, spec in enumerate(PROFILES):
        effective = spec if args.total_rows is None else ProfileSpec(
            name=spec.name,
            total_rows=args.total_rows,
            unique_ratio=spec.unique_ratio,
            hotset_ratio=spec.hotset_ratio,
            avg_session_len=spec.avg_session_len,
        )
        rows = _build_profile_rows(source_rows, effective, seed=args.seed + i)
        out = args.output_dir / f"profile_{effective.name}.jsonl"
        count = _write_jsonl(out, rows)
        unique_fps = len({_fingerprint_row(r) for r in rows})
        rel_out = out.relative_to(ROOT) if out.is_relative_to(ROOT) else out
        manifest["profiles"][effective.name] = {
            "path": str(rel_out).replace("\\", "/"),
            "rows": count,
            "unique_requests": unique_fps,
            "repeat_ratio_pct": round(((count - unique_fps) / count) * 100, 2) if count else 0.0,
            "config": {
                "total_rows": effective.total_rows,
                "unique_ratio": effective.unique_ratio,
                "hotset_ratio": effective.hotset_ratio,
                "avg_session_len": effective.avg_session_len,
            },
        }
        print(
            f"{effective.name}: wrote {count} rows, unique={unique_fps}, "
            f"repeat_ratio={manifest['profiles'][effective.name]['repeat_ratio_pct']}% -> {out}"
        )

    manifest_path = args.output_dir / "profile_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"Manifest: {manifest_path}")


if __name__ == "__main__":
    main()
