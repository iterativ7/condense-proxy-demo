#!/usr/bin/env python3
"""Run minimal, cache-only, and full Condense modes on production-like profile datasets."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from benchmarks.gemini_config import CONDENSE_MODES, DEFAULT_MATRIX_OUT, PROFILE_MANIFEST, REPO_ROOT
from benchmarks.gemini_runner import (
    require_api_key,
    run_paired_benchmark,
    start_condense,
    stop_condense,
    wait_for_health,
)


def load_profiles(manifest_path: Path) -> list[tuple[str, Path]]:
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    profiles = payload.get("profiles", {})
    loaded: list[tuple[str, Path]] = []
    for name in sorted(profiles):
        path = Path(str(profiles[name]["path"]))
        if not path.is_absolute():
            path = REPO_ROOT / path
        loaded.append((name, path))
    return loaded


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run Condense modes (minimal/cache_only/full) across profile datasets."
    )
    parser.add_argument("--profile-manifest", type=Path, default=PROFILE_MANIFEST)
    parser.add_argument("--out-root", type=Path, default=DEFAULT_MATRIX_OUT)
    parser.add_argument("--limit", type=int, default=None, help="Cap rows per run (smoke test).")
    parser.add_argument(
        "--modes",
        nargs="*",
        choices=[name for name, _ in CONDENSE_MODES],
        default=[name for name, _ in CONDENSE_MODES],
    )
    parser.add_argument(
        "--skip-existing",
        action="store_true",
        help="Skip cells that already have report.json in out-root.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    api_key = require_api_key()

    if not args.profile_manifest.exists():
        raise SystemExit(
            "Profile manifest not found. Run: python benchmarks/build_production_like_profiles.py"
        )

    profiles = load_profiles(args.profile_manifest)
    if not profiles:
        raise SystemExit("No profiles found in manifest.")

    mode_map = dict(CONDENSE_MODES)
    selected_modes = [(name, mode_map[name]) for name in args.modes if name in mode_map]

    args.out_root.mkdir(parents=True, exist_ok=True)

    for mode_name, config_rel in selected_modes:
        for profile_name, dataset in profiles:
            if not dataset.exists():
                raise SystemExit(f"Missing dataset: {dataset}")

            out_dir = args.out_root / f"{profile_name}__{mode_name}"
            if args.skip_existing and (out_dir / "report.json").exists():
                print(f"Skipping existing run: {out_dir}")
                continue
            proc = start_condense(config_rel)
            try:
                if not wait_for_health():
                    raise SystemExit(f"Condense did not become healthy for mode={mode_name}")
                code = run_paired_benchmark(
                    dataset=dataset,
                    out_dir=out_dir,
                    preset_label=f"Profile={profile_name} Mode={mode_name}",
                    api_key=api_key,
                    limit=args.limit,
                    prime_unique=True,
                )
                if code != 0:
                    return code
            finally:
                stop_condense(proc)
                time.sleep(3)

    print(f"Completed matrix runs: {args.out_root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
