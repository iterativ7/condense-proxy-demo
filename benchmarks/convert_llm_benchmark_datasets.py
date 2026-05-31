#!/usr/bin/env python3
"""Convert downloaded llm_benchmarks data into run_paired.py JSONL format."""

from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path
from typing import Any, Callable, Iterable

try:
    import pyarrow.parquet as pq
except ImportError as exc:  # pragma: no cover
    raise SystemExit("pyarrow is required: pip install pyarrow") from exc

from benchmarks.transforms.dolly import transform_dolly_row

RAW_ROOT = Path("benchmarks/datasets/llm_benchmarks")
OUT_ROOT = Path("benchmarks/datasets/converted")
DOLLY_ROOT = RAW_ROOT / "databricks_dolly_15k"
# Request body model (overridden at run time via --baseline-model / --proxy-model)
DEFAULT_MODEL = "gemini/gemini-2.5-flash"
DEFAULT_LIMIT = 50


def _write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
            count += 1
    return count


def _bench_row(
    case_id: str,
    prompt: str,
    *,
    final_answer: str | None = None,
    scoring: str = "label",
    source: str,
    split: str,
    model: str = DEFAULT_MODEL,
    extra_meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    reference: dict[str, Any] = {"scoring": scoring}
    if final_answer is not None:
        reference["final_answer"] = final_answer
    metadata: dict[str, Any] = {"source": source, "split": split}
    if extra_meta:
        metadata.update(extra_meta)
    return {
        "id": case_id,
        "request": {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0,
        },
        "reference": reference,
        "metadata": metadata,
    }


def _take(items: list[Any], limit: int, seed: int) -> list[Any]:
    if len(items) <= limit:
        return items
    rng = random.Random(seed)
    indices = sorted(rng.sample(range(len(items)), limit))
    return [items[i] for i in indices]


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            stripped = line.strip()
            if stripped:
                rows.append(json.loads(stripped))
    return rows


def _read_parquet_rows(path: Path) -> list[dict[str, Any]]:
    table = pq.read_table(path)
    return table.to_pylist()


def convert_humaneval(raw_dir: Path, limit: int, model: str, seed: int) -> list[dict[str, Any]]:
    path = raw_dir / "HumanEval.jsonl"
    if not path.exists():
        raise FileNotFoundError(path)
    rows: list[dict[str, Any]] = []
    for item in _take(_read_jsonl(path), limit, seed):
        task_id = str(item["task_id"]).replace("/", "_")
        prompt = (
            "Complete the following Python function. "
            "Return only valid Python code for the function body.\n\n"
            f"{item['prompt']}"
        )
        rows.append(
            _bench_row(
                f"humaneval_{task_id}",
                prompt,
                final_answer=item["entry_point"],
                scoring="code_entry",
                source="openai/human-eval",
                split="test",
                model=model,
                extra_meta={"task_id": item["task_id"]},
            )
        )
    return rows


def convert_hellaswag(raw_path: Path, limit: int, model: str, seed: int) -> list[dict[str, Any]]:
    if not raw_path.exists():
        raise FileNotFoundError(raw_path)
    rows: list[dict[str, Any]] = []
    for idx, item in enumerate(_take(_read_jsonl(raw_path), limit, seed)):
        endings = item["endings"]
        choices = "\n".join(f"{chr(65 + i)}. {endings[i]}" for i in range(len(endings)))
        prompt = (
            "Pick the most plausible continuation. Reply with only the letter (A, B, C, or D).\n\n"
            f"Context: {item['ctx']}\n\n"
            f"Choices:\n{choices}"
        )
        label = int(item["label"])
        rows.append(
            _bench_row(
                f"hellaswag_{idx:04d}",
                prompt,
                final_answer=chr(65 + label),
                scoring="choice",
                source="rowanz/hellaswag",
                split=str(item.get("split", "val")),
                model=model,
            )
        )
    return rows


def _glue_label_name(task: str, label: Any) -> str:
    if task == "sst2":
        return "positive" if int(label) == 1 else "negative"
    if task in {"cola", "mrpc", "qqp", "rte", "wnli"}:
        return "yes" if int(label) == 1 else "no"
    return str(label)


def _glue_prompt(task: str, row: dict[str, Any]) -> str:
    if task == "sst2":
        return (
            "Classify the sentiment as positive or negative. "
            "Reply with only one word: positive or negative.\n\n"
            f"Sentence: {row['sentence']}"
        )
    if task == "cola":
        return (
            "Is this sentence linguistically acceptable? "
            "Reply with only one word: yes or no.\n\n"
            f"Sentence: {row['sentence']}"
        )
    if task in {"mrpc", "qqp", "rte", "wnli"}:
        return (
            "Are these two sentences equivalent? "
            "Reply with only one word: yes or no.\n\n"
            f"Sentence 1: {row['sentence1']}\n"
            f"Sentence 2: {row['sentence2']}"
        )
    raise ValueError(f"Unsupported GLUE task: {task}")


def convert_glue_task(
    glue_root: Path, task: str, limit: int, model: str, seed: int
) -> list[dict[str, Any]]:
    val_path = glue_root / task / "validation-00000-of-00001.parquet"
    if not val_path.exists():
        raise FileNotFoundError(val_path)
    rows: list[dict[str, Any]] = []
    for idx, item in enumerate(_take(_read_parquet_rows(val_path), limit, seed)):
        label_name = _glue_label_name(task, item["label"])
        rows.append(
            _bench_row(
                f"glue_{task}_{idx:04d}",
                _glue_prompt(task, item),
                final_answer=label_name,
                scoring="label",
                source="nyu-mll/glue",
                split="validation",
                model=model,
                extra_meta={"task": task},
            )
        )
    return rows


def convert_mbpp(limit: int, model: str, seed: int) -> list[dict[str, Any]]:
    try:
        from huggingface_hub import hf_hub_download
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("huggingface_hub required for MBPP") from exc

    parquet_path = Path(
        hf_hub_download(
            repo_id="google-research-datasets/mbpp",
            filename="sanitized/test-00000-of-00001.parquet",
            repo_type="dataset",
        )
    )
    rows: list[dict[str, Any]] = []
    for idx, item in enumerate(_take(_read_parquet_rows(parquet_path), limit, seed)):
        text = item.get("text") or item.get("prompt") or ""
        prompt = (
            "Write a Python function that solves the task. "
            "Return only Python code.\n\n"
            f"{text}"
        )
        rows.append(
            _bench_row(
                f"mbpp_{idx:04d}",
                prompt,
                scoring="latency_only",
                source="google-research-datasets/mbpp",
                split="test",
                model=model,
                extra_meta={"task_id": item.get("task_id")},
            )
        )
    return rows


def convert_dolly(
    dolly_root: Path,
    limit: int,
    model: str,
    seed: int,
) -> list[dict[str, Any]]:
    try:
        from datasets import load_from_disk
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("datasets is required for Dolly conversion: pip install datasets") from exc

    if not dolly_root.exists():
        raise FileNotFoundError(
            f"{dolly_root} not found. Download first with "
            "load_dataset('databricks/databricks-dolly-15k') + save_to_disk(...)."
        )

    loaded = load_from_disk(str(dolly_root))
    if hasattr(loaded, "keys"):
        if "train" not in loaded:
            raise ValueError(f"Expected a 'train' split in {dolly_root}")
        dataset = loaded["train"]
    else:
        dataset = loaded

    items = _take(list(dataset), limit, seed)
    rows: list[dict[str, Any]] = []
    for idx, item in enumerate(items):
        rows.append(
            transform_dolly_row(
                item,
                case_id=f"dolly_{idx:05d}",
                model=model,
            )
        )
    return rows


CONVERTERS: dict[str, Callable[..., list[dict[str, Any]]]] = {
    "humaneval": lambda limit, model, seed: convert_humaneval(
        RAW_ROOT / "coding" / "humaneval", limit, model, seed
    ),
    "hellaswag": lambda limit, model, seed: convert_hellaswag(
        RAW_ROOT / "language" / "hellaswag" / "github" / "hellaswag_val.jsonl",
        limit,
        model,
        seed,
    ),
    "glue_sst2": lambda limit, model, seed: convert_glue_task(
        RAW_ROOT / "language" / "glue", "sst2", limit, model, seed
    ),
    "glue_cola": lambda limit, model, seed: convert_glue_task(
        RAW_ROOT / "language" / "glue", "cola", limit, model, seed
    ),
    "glue_mrpc": lambda limit, model, seed: convert_glue_task(
        RAW_ROOT / "language" / "glue", "mrpc", limit, model, seed
    ),
    "glue_qqp": lambda limit, model, seed: convert_glue_task(
        RAW_ROOT / "language" / "glue", "qqp", limit, model, seed
    ),
    "mbpp": convert_mbpp,
    "dolly": lambda limit, model, seed: convert_dolly(DOLLY_ROOT, limit, model, seed),
}


def build_suite(per_dataset: int, model: str, seed: int) -> list[dict[str, Any]]:
    suite: list[dict[str, Any]] = []
    for name in ("humaneval", "hellaswag", "glue_sst2", "glue_cola", "mbpp"):
        rows = CONVERTERS[name](per_dataset, model, seed)
        suite.extend(rows)
    return suite


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Convert llm_benchmarks raw data to benchmark JSONL.")
    parser.add_argument(
        "--datasets",
        nargs="+",
        default=list(CONVERTERS.keys()),
        choices=[*CONVERTERS.keys(), "suite"],
        help="Which datasets to convert (or 'suite' for a mixed file).",
    )
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT, help="Max rows per dataset.")
    parser.add_argument("--suite-per-dataset", type=int, default=10, help="Rows per dataset in suite.")
    parser.add_argument("--output-dir", type=Path, default=OUT_ROOT)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--seed", type=int, default=42)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.limit <= 0:
        raise SystemExit("--limit must be positive")

    manifest: dict[str, Any] = {"datasets": {}, "output_dir": str(args.output_dir)}

    try:
        for name in args.datasets:
            if name == "suite":
                rows = build_suite(args.suite_per_dataset, args.model, args.seed)
                out = args.output_dir / f"suite_{args.suite_per_dataset * 5}.jsonl"
            else:
                rows = CONVERTERS[name](args.limit, args.model, args.seed)
                out = args.output_dir / f"{name}_{args.limit}.jsonl"
            count = _write_jsonl(out, rows)
            manifest["datasets"][name] = {"path": str(out), "count": count}
            print(f"Wrote {count} rows -> {out}")
    except (FileNotFoundError, OSError, ValueError, RuntimeError) as exc:
        print(f"Conversion failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc

    manifest_path = args.output_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"Manifest: {manifest_path}")


if __name__ == "__main__":
    main()
