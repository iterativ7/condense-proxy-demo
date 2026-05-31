"""RTK content-type auto-detection.

Inspects the first ~1 KB of tool-output text and returns the RTK filter
name that best matches.  This runs **before** shelling out to ``rtk pipe``
so we can:

1. Log *which* filter was selected (Philosophy 4: TRANSPARENT).
2. Pass ``--filter=<name>`` explicitly for deterministic behaviour.
3. Skip the subprocess entirely when no filter matches.

The heuristics here mirror RTK's own ``auto_detect_filter`` (Rust) and
9router's ``autodetect.js`` (JS).  We intentionally keep this thin —
all actual filtering is done by the RTK binary.
"""

from __future__ import annotations

import re

# How many characters of the input to inspect for detection.
_DETECT_WINDOW = 1024


def detect_content_type(text: str) -> str | None:
    """Return the RTK filter name for *text*, or ``None`` if unrecognised.

    Only the first ~1 KB is inspected for performance.

    Returns one of:
        ``"cargo-test"``, ``"pytest"``, ``"go-test"``, ``"vitest"``,
        ``"mypy"``, ``"grep"``, ``"find"``, ``"git-diff"``,
        ``"git-status"``, ``"git-log"``, ``"tsc"``, ``"log"``,
        ``"ruff-check"``, ``"prettier"``,
        or ``None``.
    """
    if not text or not text.strip():
        return None

    head = text[:_DETECT_WINDOW]

    # ── Test output ────────────────────────────────────────────────
    # Cargo test: "running X test(s)" or "test result:"
    if re.search(r"running \d+ tests?", head) or "test result:" in head:
        return "cargo-test"

    # Pytest: "====" header + "passed" / "failed" / "PASSED" / "FAILED"
    if "====" in head and re.search(r"(passed|failed|error)", head, re.IGNORECASE):
        # Distinguish from generic separator — look for pytest markers
        if re.search(r"(test session starts|pytest|\.py::|PASSED|FAILED|ERROR)", head):
            return "pytest"

    # Go test: "--- PASS" / "--- FAIL" / "ok  " / "FAIL\t"
    if re.search(r"--- (PASS|FAIL)", head) or re.search(r"^(ok\s{2,}|FAIL\t)", head, re.MULTILINE):
        return "go-test"

    # Vitest/Jest: "✓" or "✗" or "PASS " or "FAIL " with test suite patterns
    if re.search(r"(Tests?:\s+\d+|Test Suites?:|✓|✗|PASS\s|FAIL\s)", head):
        return "vitest"

    # ── Linter / type-checker output ──────────────────────────────
    # MyPy: "file.py:line: error:" pattern
    if re.search(r"\.py:\d+: error:", head):
        return "mypy"

    # TSC: "file.ts(line,col): error TS"
    if re.search(r"\.\w+\(\d+,\d+\): error TS", head):
        return "tsc"

    # Ruff check (JSON): starts with "[" and contains "code" + "message"
    if head.lstrip().startswith("[") and '"code"' in head and '"message"' in head:
        return "ruff-check"

    # Prettier: "Checking formatting..." or "[warn]" with file paths
    if "Checking formatting" in head or re.search(r"\[warn\].*\.\w+$", head, re.MULTILINE):
        return "prettier"

    # ── Git output ────────────────────────────────────────────────
    # Git diff: unified diff headers
    if re.search(r"^diff --git ", head, re.MULTILINE) or re.search(r"^@@\s.*@@", head, re.MULTILINE):
        return "git-diff"

    # Git status: "On branch" or "Changes not staged" or status short format (M/A/D/? prefixes)
    if re.search(r"^(On branch|Changes|Untracked|Your branch)", head, re.MULTILINE):
        return "git-status"
    # Short format: lines starting with " M ", "?? ", "A  ", "M  " etc.
    if re.search(r"^[MADRCU?! ]{1,2}\s+\S", head, re.MULTILINE):
        short_lines = [l for l in head.split("\n")[:20] if re.match(r"^[MADRCU?! ]{1,2}\s+\S", l)]
        if len(short_lines) >= 3:
            return "git-status"

    # Git log: "commit <sha>" lines
    if re.search(r"^commit [0-9a-f]{7,}$", head, re.MULTILINE):
        return "git-log"

    # ── Search / file listing ─────────────────────────────────────
    # Grep/rg: "file:line:" or "file:line:col:" pattern — many matches
    # Must look like file paths (contain . or /) to avoid matching timestamps
    grep_matches = re.findall(r"^[^\s:]*[./][^\s:]*:\d+:", head, re.MULTILINE)
    if len(grep_matches) >= 3:
        return "grep"

    # Find/fd: lots of file paths (lines starting with ./ or /)
    path_lines = re.findall(r"^[./].*\S", head, re.MULTILINE)
    if len(path_lines) >= 5:
        return "find"

    # ── Generic log deduplication ─────────────────────────────────
    # Repeated timestamp-prefixed lines
    ts_lines = re.findall(
        r"^\d{4}[-/]\d{2}[-/]\d{2}[T ]\d{2}:\d{2}", head, re.MULTILINE
    )
    if len(ts_lines) >= 5:
        return "log"

    return None
