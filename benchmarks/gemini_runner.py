"""Shared helpers for Gemini benchmark orchestration scripts."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Sequence

import httpx

from benchmarks.gemini_config import (
    BASELINE_URL,
    ENV_FILE,
    GEMINI_BASELINE_MODEL,
    GEMINI_PRICE_INPUT_PER_1K,
    GEMINI_PRICE_OUTPUT_PER_1K,
    GEMINI_PROXY_MODEL,
    HEALTH_URL,
    PROXY_URL,
    REPO_ROOT,
)


def load_dotenv(path: Path = ENV_FILE) -> None:
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())


def require_api_key() -> str:
    load_dotenv()
    api_key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not api_key:
        raise SystemExit("GEMINI_API_KEY is missing. Set it in .env or your shell environment.")
    return api_key


def wait_for_health(timeout_s: int = 120) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            if httpx.get(HEALTH_URL, timeout=3.0).status_code == 200:
                return True
        except httpx.HTTPError:
            pass
        time.sleep(1)
    return False


def start_condense(config_rel: str) -> subprocess.Popen[bytes]:
    env = os.environ.copy()
    env["CONDENSE_CONFIG"] = str(REPO_ROOT / config_rel)
    return subprocess.Popen(
        [
            sys.executable,
            "-m",
            "condense",
            "start",
            "--config",
            config_rel,
            "--host",
            "127.0.0.1",
            "--port",
            "8090",
        ],
        cwd=REPO_ROOT,
        env=env,
    )


def stop_condense(proc: subprocess.Popen[bytes]) -> None:
    if proc.poll() is not None:
        return
    if sys.platform == "win32":
        proc.terminate()
    else:
        proc.send_signal(signal.SIGTERM)
    try:
        proc.wait(timeout=30)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=10)


def run_paired_benchmark(
    *,
    dataset: Path,
    out_dir: Path,
    preset_label: str,
    api_key: str,
    limit: int | None = None,
    prime_unique: bool = True,
    extra_args: Sequence[str] | None = None,
) -> int:
    cmd = [
        sys.executable,
        str(REPO_ROOT / "benchmarks/run_paired.py"),
        "--dataset",
        str(dataset),
        "--baseline-url",
        BASELINE_URL,
        "--proxy-url",
        PROXY_URL,
        "--out-dir",
        str(out_dir),
        "--baseline-model",
        GEMINI_BASELINE_MODEL,
        "--proxy-model",
        GEMINI_PROXY_MODEL,
        "--authorization",
        f"Bearer {api_key}",
        "--preset-label",
        preset_label,
        "--price-input-per-1k",
        str(GEMINI_PRICE_INPUT_PER_1K),
        "--price-output-per-1k",
        str(GEMINI_PRICE_OUTPUT_PER_1K),
        "--max-retries",
        "5",
        "--retry-backoff-s",
        "2.0",
        "--request-delay-s",
        "0.35",
        "--timeout",
        "600",
    ]
    if prime_unique:
        cmd.append("--prime-proxy-cache-unique")
    if limit is not None:
        cmd.extend(["--limit", str(limit)])
    if extra_args:
        cmd.extend(extra_args)
    return int(subprocess.run(cmd, cwd=REPO_ROOT).returncode)
