#!/usr/bin/env python3
"""Download a small GSM8K test split for paired Condense benchmarks."""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Iterable


GSM8K_TEST_URL = (
    "https://raw.githubusercontent.com/openai/grade-school-math/"
    "master/grade_school_math/data/test.jsonl"
)
DEFAULT_OUTPUT = Path("benchmarks/datasets/gsm8k_test_50.jsonl")
DEFAULT_MODEL = "ollama/gemma3:4b"


def _extract_final_answer(answer: str) -> str:
    """Extract the GSM8K final answer after the canonical #### marker."""
    if "####" in answer:
        return answer.rsplit("####", 1)[1].strip()

    numbers = re.findall(r"-?\d+(?:,\d{3})*(?:\.\d+)?", answer)
    return numbers[-1].strip() if numbers else ""


def _iter_source_rows(url: str) -> Iterable[dict]:
    with urllib.request.urlopen(url, timeout=60) as response:
        for raw_line in response:
            line = raw_line.decode("utf-8").strip()
            if line:
                yield json.loads(line)


def download_dataset(url: str, output: Path, limit: int, model: str) -> int:
    output.parent.mkdir(parents=True, exist_ok=True)

    count = 0
    with output.open("w", encoding="utf-8") as out:
        for source_row in _iter_source_rows(url):
            if count >= limit:
                break

            question = source_row["question"]
            answer = source_row["answer"]
            row = {
                "id": f"gsm8k_{count:03d}",
                "request": {
                    "model": model,
                    "messages": [{"role": "user", "content": question}],
                    "temperature": 0,
                },
                "reference": {
                    "answer": answer,
                    "final_answer": _extract_final_answer(answer),
                },
                "metadata": {
                    "source": "openai/gsm8k",
                    "split": "test",
                },
            }
            out.write(json.dumps(row, ensure_ascii=False) + "\n")
            count += 1

    return count


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Download GSM8K test examples as benchmark JSONL.")
    parser.add_argument("--limit", type=int, default=50, help="Number of examples to write.")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="Output JSONL path.")
    parser.add_argument("--model", default=DEFAULT_MODEL, help="Model to place in each request body.")
    parser.add_argument("--url", default=GSM8K_TEST_URL, help="Source GSM8K test JSONL URL.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.limit <= 0:
        raise SystemExit("--limit must be positive")

    try:
        count = download_dataset(args.url, args.output, args.limit, args.model)
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        print(f"Failed to download dataset: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc

    print(f"Wrote {count} GSM8K examples to {args.output}")


if __name__ == "__main__":
    main()
