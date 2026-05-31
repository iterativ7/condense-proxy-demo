#!/usr/bin/env python3
"""Run benchmarks from Dolly data loaded at runtime.

This script loads Dolly from a local HF `save_to_disk()` path, transforms rows
to benchmark JSONL on the fly, then invokes `benchmarks/run_paired.py`.
"""

from __future__ import annotations

import argparse
import json
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from benchmarks.transforms.dolly import transform_dolly_row

DEFAULT_DOLLY_PATH = Path("benchmarks/datasets/llm_benchmarks/databricks_dolly_15k")
DEFAULT_MODEL = "gemini/gemini-2.5-flash"
DEFAULT_TMP_DIR = Path("benchmarks/runs/tmp")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Load Dolly from disk at runtime, emit temporary benchmark JSONL, "
            "and invoke benchmarks/run_paired.py."
        )
    )
    parser.add_argument(
        "--dolly-path",
        type=Path,
        default=DEFAULT_DOLLY_PATH,
        help="Path created by datasets.save_to_disk() for databricks-dolly-15k.",
    )
    parser.add_argument(
        "--model",
        default=DEFAULT_MODEL,
        help="Model value written into generated benchmark request rows.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Optional cap on Dolly rows to emit (default: all rows).",
    )
    parser.add_argument(
        "--keep-jsonl",
        action="store_true",
        help="Keep temporary emitted JSONL instead of deleting it after run.",
    )
    parser.add_argument(
        "--emit-only",
        action="store_true",
        help="Only emit JSONL and print run_paired command; do not execute benchmark.",
    )
    parser.add_argument(
        "--tmp-dir",
        type=Path,
        default=DEFAULT_TMP_DIR,
        help="Directory for temporary emitted benchmark JSONL.",
    )
    parser.add_argument(
        "run_paired_args",
        nargs=argparse.REMAINDER,
        help="Arguments forwarded to benchmarks/run_paired.py (prefix with --).",
    )
    return parser.parse_args()


def _load_dolly_rows(dolly_path: Path) -> list[dict[str, Any]]:
    try:
        from datasets import load_from_disk
    except ImportError as exc:  # pragma: no cover
        raise SystemExit("datasets is required: pip install datasets") from exc

    if not dolly_path.exists():
        raise SystemExit(f"Dolly path not found: {dolly_path}")

    loaded = load_from_disk(str(dolly_path))
    if hasattr(loaded, "keys"):
        if "train" not in loaded:
            raise SystemExit(f"Expected 'train' split in {dolly_path}")
        dataset = loaded["train"]
    else:
        dataset = loaded
    return list(dataset)


def _strip_remainder_prefix(args: list[str]) -> list[str]:
    return args[1:] if args and args[0] == "--" else args


def _contains_forbidden_forwarded_flags(forwarded: list[str]) -> bool:
    forbidden = {"--dataset"}
    return any(flag in forbidden for flag in forwarded)


def _emit_runtime_jsonl(
    dolly_rows: list[dict[str, Any]],
    *,
    out_path: Path,
    model: str,
    limit: int | None,
) -> int:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with out_path.open("w", encoding="utf-8") as handle:
        for idx, item in enumerate(dolly_rows):
            if limit is not None and idx >= limit:
                break
            row = transform_dolly_row(item, case_id=f"dolly_{idx:05d}", model=model)
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
            count += 1
    return count


def main() -> None:
    args = _parse_args()
    forwarded = _strip_remainder_prefix(list(args.run_paired_args))

    if _contains_forbidden_forwarded_flags(forwarded):
        raise SystemExit("Do not pass --dataset in forwarded args; it is managed by this wrapper.")

    if args.limit is not None and args.limit <= 0:
        raise SystemExit("--limit must be positive when provided.")

    all_rows = _load_dolly_rows(args.dolly_path)
    args.tmp_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        suffix=".jsonl",
        prefix="dolly_runtime_",
        dir=args.tmp_dir,
        delete=False,
    ) as tmp:
        tmp_path = Path(tmp.name)

    emitted = _emit_runtime_jsonl(
        all_rows,
        out_path=tmp_path,
        model=args.model,
        limit=args.limit,
    )
    print(f"Emitted {emitted} Dolly benchmark rows -> {tmp_path}")

    cmd = [sys.executable, "benchmarks/run_paired.py", "--dataset", str(tmp_path), *forwarded]
    print("Run command:", " ".join(shlex.quote(part) for part in cmd))

    if args.emit_only:
        return

    try:
        result = subprocess.run(cmd, check=False)
        if result.returncode != 0:
            raise SystemExit(result.returncode)
    finally:
        if not args.keep_jsonl and tmp_path.exists():
            tmp_path.unlink()
            print(f"Removed temp file: {tmp_path}")


if __name__ == "__main__":
    main()

