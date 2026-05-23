#!/usr/bin/env python3
"""Download Coding and Language datasets referenced in leobeeson/llm_benchmarks.

Coding (all subcategories):
  - HumanEval, MBPP, CodeXGLUE (official google/* + microsoft method generation)

Language (GLUE + HellaSwag):
  - nyu-mll/glue (all task configs)
  - Rowan/hellaswag
"""

from __future__ import annotations

import argparse
import gzip
import json
import shutil
import sys
import urllib.error
import urllib.request
from pathlib import Path

try:
    from huggingface_hub import snapshot_download
except ImportError as exc:  # pragma: no cover
    raise SystemExit("huggingface_hub is required: pip install huggingface_hub") from exc


ROOT = Path("benchmarks/datasets/llm_benchmarks")

HUMANEVAL_URL = (
    "https://github.com/openai/human-eval/raw/master/data/HumanEval.jsonl.gz"
)
HELLASWAG_FILES = {
    "hellaswag_val.jsonl": (
        "https://raw.githubusercontent.com/rowanz/hellaswag/master/data/hellaswag_val.jsonl"
    ),
    "hellaswag_test.jsonl": (
        "https://raw.githubusercontent.com/rowanz/hellaswag/master/data/hellaswag_test.jsonl"
    ),
}

CODEXGLUE_REPOS = [
    "google/code_x_glue_cc_clone_detection_big_clone_bench",
    "google/code_x_glue_cc_clone_detection_poj104",
    "google/code_x_glue_cc_cloze_testing_all",
    "google/code_x_glue_cc_cloze_testing_maxmin",
    "google/code_x_glue_cc_code_completion_line",
    "google/code_x_glue_cc_code_completion_token",
    "google/code_x_glue_cc_code_refinement",
    "google/code_x_glue_cc_code_to_code_trans",
    "google/code_x_glue_cc_defect_detection",
    "google/code_x_glue_ct_code_to_text",
    "google/code_x_glue_tc_nl_code_search_adv",
    "google/code_x_glue_tc_text_to_code",
    "google/code_x_glue_tt_text_to_text",
    "microsoft/codexglue_method_generation",
]

GLUE_CONFIGS = [
    "cola",
    "sst2",
    "mrpc",
    "qqp",
    "stsb",
    "mnli",
    "mnli_matched",
    "mnli_mismatched",
    "qnli",
    "rte",
    "wnli",
    "ax",
]


def _download_url(url: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        print(f"  skip (exists): {dest}")
        return
    print(f"  download: {url}")
    with urllib.request.urlopen(url, timeout=120) as response:
        dest.write_bytes(response.read())


def _download_humaneval(out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    gz_path = out_dir / "HumanEval.jsonl.gz"
    jsonl_path = out_dir / "HumanEval.jsonl"
    _download_url(HUMANEVAL_URL, gz_path)
    if not jsonl_path.exists():
        with gzip.open(gz_path, "rb") as src, jsonl_path.open("wb") as dst:
            shutil.copyfileobj(src, dst)
        print(f"  extracted: {jsonl_path}")


def _download_hellaswag_github(out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    for name, url in HELLASWAG_FILES.items():
        _download_url(url, out_dir / name)


def _hf_snapshot(repo_id: str, local_dir: Path) -> None:
    local_dir.mkdir(parents=True, exist_ok=True)
    marker = local_dir / ".download_complete"
    if marker.exists():
        print(f"  skip (exists): {repo_id}")
        return
    print(f"  huggingface: {repo_id}")
    path = snapshot_download(
        repo_id=repo_id,
        repo_type="dataset",
        local_dir=str(local_dir),
    )
    marker.write_text(json.dumps({"repo_id": repo_id, "path": path}, indent=2))
    print(f"  saved: {path}")


def download_coding(root: Path, skip_codexglue: bool) -> None:
    coding = root / "coding"
    print("\n[coding] HumanEval")
    _download_humaneval(coding / "humaneval")

    print("\n[coding] MBPP")
    _hf_snapshot("google-research-datasets/mbpp", coding / "mbpp")

    if skip_codexglue:
        print("\n[coding] CodeXGLUE skipped (--skip-codexglue)")
        return

    print("\n[coding] CodeXGLUE (14 official HF datasets)")
    codex_dir = coding / "codexglue"
    for repo_id in CODEXGLUE_REPOS:
        short = repo_id.split("/", 1)[1]
        _hf_snapshot(repo_id, codex_dir / short)


def download_language(root: Path) -> None:
    language = root / "language"

    print("\n[language] GLUE (all task configs in nyu-mll/glue)")
    _hf_snapshot("nyu-mll/glue", language / "glue")

    print("\n[language] HellaSwag (GitHub raw + HF snapshot)")
    _download_hellaswag_github(language / "hellaswag" / "github")
    _hf_snapshot("Rowan/hellaswag", language / "hellaswag" / "huggingface")


def write_manifest(root: Path) -> None:
    manifest = {
        "source_repo": "https://github.com/leobeeson/llm_benchmarks",
        "sections": {
            "coding": {
                "humaneval": str(root / "coding" / "humaneval"),
                "mbpp": str(root / "coding" / "mbpp"),
                "codexglue": [r for r in CODEXGLUE_REPOS],
            },
            "language": {
                "glue_configs": GLUE_CONFIGS,
                "hellaswag": str(root / "language" / "hellaswag"),
            },
        },
    }
    path = root / "manifest.json"
    path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"\nWrote manifest: {path}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Download Coding + Language datasets from leobeeson/llm_benchmarks README."
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT,
        help="Root directory for downloaded data.",
    )
    parser.add_argument(
        "--coding-only",
        action="store_true",
        help="Download only Coding section datasets.",
    )
    parser.add_argument(
        "--language-only",
        action="store_true",
        help="Download only Language section (GLUE + HellaSwag).",
    )
    parser.add_argument(
        "--skip-codexglue",
        action="store_true",
        help="Skip large CodeXGLUE HF snapshots (HumanEval + MBPP still download).",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    root = args.output
    root.mkdir(parents=True, exist_ok=True)

    if args.coding_only and args.language_only:
        raise SystemExit("Use at most one of --coding-only and --language-only")

    try:
        if not args.language_only:
            download_coding(root, skip_codexglue=args.skip_codexglue)
        if not args.coding_only:
            download_language(root)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        print(f"Download failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc

    write_manifest(root)
    print(f"\nDone. Datasets under: {root.resolve()}")


if __name__ == "__main__":
    main()
