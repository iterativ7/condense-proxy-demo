"""Run README's remaining paired benchmarks: cache_only (primed) then full.

Starts Condense as a child process so the server lifetime matches the benchmark.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
DATASET = ROOT / "benchmarks/datasets/gsm8k_test_50.jsonl"
LOG_PATH = Path(os.environ.get("TEMP", "/tmp")) / "condense_bench_two_readme_py.log"


def log(msg: str) -> None:
    line = f"{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())} {msg}"
    print(line, flush=True)
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOG_PATH.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def wait_health(timeout_s: int = 120) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            r = httpx.get("http://127.0.0.1:8080/health", timeout=2.0)
            if r.status_code == 200:
                return True
        except httpx.HTTPError:
            pass
        time.sleep(1)
    return False


def start_condense(config_rel: str) -> subprocess.Popen[bytes]:
    cfg = ROOT / config_rel
    env = os.environ.copy()
    env["CONDENSE_CONFIG"] = str(cfg)
    return subprocess.Popen(
        [sys.executable, "-m", "condense", "start", "--config", config_rel],
        cwd=ROOT,
        env=env,
    )


def stop_process(proc: subprocess.Popen[bytes]) -> None:
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


def run_paired(out_dir: str, extra_args: list[str]) -> int:
    cmd = [
        sys.executable,
        str(ROOT / "benchmarks/run_paired.py"),
        "--dataset",
        str(DATASET),
        "--baseline-url",
        "http://127.0.0.1:11434/v1/chat/completions",
        "--proxy-url",
        "http://127.0.0.1:8080/v1/chat/completions",
        "--out-dir",
        str(ROOT / "benchmarks/runs" / out_dir),
        "--baseline-model",
        "gemma3:4b",
        "--proxy-model",
        "ollama/gemma3:4b",
        *extra_args,
    ]
    log("RUN " + " ".join(cmd))
    r = subprocess.run(cmd, cwd=ROOT)
    return int(r.returncode)


def main() -> int:
    LOG_PATH.write_text("", encoding="utf-8")
    passes: list[tuple[str, str, list[str]]] = [
        ("benchmarks/presets/cache_only.yaml", "local-cache-only-gemma-50-primed", ["--prime-proxy-cache"]),
        ("benchmarks/presets/full.yaml", "local-full-gemma-50", []),
    ]
    for cfg, out_name, extras in passes:
        log(f"=== Starting Condense {cfg} ===")
        proc = start_condense(cfg)
        try:
            if not wait_health():
                log("ERROR: Condense did not become healthy on :8080")
                return 1
            log("Condense healthy; running paired benchmark")
            code = run_paired(out_name, extras)
            if code != 0:
                log(f"ERROR: run_paired exited {code}")
                return code
        finally:
            log("Stopping Condense")
            stop_process(proc)
            time.sleep(2)
    log("All passes completed OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
