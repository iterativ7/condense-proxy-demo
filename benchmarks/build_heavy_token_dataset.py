#!/usr/bin/env python3
"""Build token-heavy coding + language benchmark JSONL from converted public tasks."""

from __future__ import annotations

import argparse
import copy
import json
import random
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from benchmarks.gemini_config import CONVERTED_DIR, GEMINI_PROXY_MODEL, HEAVY_UNIQUE_DATASET

CODING_SOURCES = ("humaneval_50.jsonl", "mbpp_50.jsonl")
LANGUAGE_SOURCES = ("hellaswag_50.jsonl", "glue_sst2_50.jsonl", "glue_cola_50.jsonl")
CODING_MAX_TOKENS = 2048


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


def _prompt_chars(row: dict[str, Any]) -> int:
    messages = row.get("request", {}).get("messages", [])
    if not messages:
        return 0
    return len(str(messages[0].get("content", "")))


def _prepare_row(row: dict[str, Any], domain: str) -> dict[str, Any]:
    out = copy.deepcopy(row)
    req = out.setdefault("request", {})
    req["model"] = GEMINI_PROXY_MODEL
    meta = out.setdefault("metadata", {})
    meta["domain"] = domain
    meta["prompt_chars"] = _prompt_chars(out)
    if domain == "coding":
        req["max_tokens"] = CODING_MAX_TOKENS
    return out


def _pick_heavy(sources: tuple[str, ...], per_source: int, *, domain: str) -> list[dict[str, Any]]:
    picked: list[dict[str, Any]] = []
    for name in sources:
        path = CONVERTED_DIR / name
        if not path.exists():
            raise FileNotFoundError(
                f"Missing {path}. See benchmarks/README.md (regenerate source datasets)."
            )
        rows = sorted(_load_jsonl(path), key=_prompt_chars, reverse=True)[:per_source]
        for row in rows:
            picked.append(_prepare_row(row, domain))
    return picked


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Token-heavy coding + language benchmark JSONL.")
    parser.add_argument("--coding-per-source", type=int, default=8)
    parser.add_argument("--language-per-source", type=int, default=5)
    parser.add_argument("--output-unique", type=Path, default=HEAVY_UNIQUE_DATASET)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    coding = _pick_heavy(CODING_SOURCES, args.coding_per_source, domain="coding")
    language = _pick_heavy(LANGUAGE_SOURCES, args.language_per_source, domain="language")
    unique = coding + language

    n_unique = _write_jsonl(args.output_unique, unique)
    print(f"Unique: {n_unique} rows -> {args.output_unique}")
    print(f"  coding: {len(coding)} ({', '.join(CODING_SOURCES)})")
    print(f"  language: {len(language)} ({', '.join(LANGUAGE_SOURCES)})")
    print(f"  model: {GEMINI_PROXY_MODEL}; coding max_tokens={CODING_MAX_TOKENS}")

    chars = [_prompt_chars(r) for r in unique]
    print(f"  prompt chars: min={min(chars)} median={sorted(chars)[len(chars)//2]} max={max(chars)}")


if __name__ == "__main__":
    main()
