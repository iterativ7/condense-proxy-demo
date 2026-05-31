"""Tests for RTK content-type auto-detection.

Each test feeds realistic content snippets to ``detect_content_type``
and verifies the correct RTK filter name is returned.
"""

import pytest

from condense.compression.backends.rtk_detect import detect_content_type


# -----------------------------------------------------------------------
# Cargo test output
# -----------------------------------------------------------------------

class TestCargoTest:
    def test_running_tests(self):
        text = (
            "   Compiling myproject v0.1.0\n"
            "    Finished `test` profile\n"
            "     Running unittests src/main.rs\n"
            "\n"
            "running 3 tests\n"
            "test tests::test_add ... ok\n"
            "test tests::test_sub ... ok\n"
            "test tests::test_mul ... FAILED\n"
            "\n"
            "test result: FAILED. 2 passed; 1 failed; 0 ignored\n"
        )
        assert detect_content_type(text) == "cargo-test"

    def test_test_result_only(self):
        text = "test result: ok. 42 passed; 0 failed; 0 ignored\n"
        assert detect_content_type(text) == "cargo-test"


# -----------------------------------------------------------------------
# Pytest output
# -----------------------------------------------------------------------

class TestPytest:
    def test_pytest_header(self):
        text = (
            "============================= test session starts ==============================\n"
            "platform linux -- Python 3.12.0, pytest-8.0.0\n"
            "collected 15 items\n"
            "\n"
            "tests/test_foo.py::test_bar PASSED\n"
            "tests/test_foo.py::test_baz FAILED\n"
            "\n"
            "================================ 1 failed, 14 passed ===========================\n"
        )
        assert detect_content_type(text) == "pytest"

    def test_pytest_short(self):
        text = (
            "===== test session starts =====\n"
            "tests/test_x.py::test_one PASSED\n"
            "===== 1 passed =====\n"
        )
        assert detect_content_type(text) == "pytest"


# -----------------------------------------------------------------------
# Go test output
# -----------------------------------------------------------------------

class TestGoTest:
    def test_go_pass(self):
        text = (
            "=== RUN   TestAdd\n"
            "--- PASS: TestAdd (0.00s)\n"
            "=== RUN   TestSub\n"
            "--- PASS: TestSub (0.00s)\n"
            "PASS\n"
            "ok  \tmypackage\t0.003s\n"
        )
        assert detect_content_type(text) == "go-test"

    def test_go_fail(self):
        text = (
            "--- FAIL: TestDiv (0.00s)\n"
            "    main_test.go:42: expected 5, got 0\n"
            "FAIL\n"
            "FAIL\tmypackage\t0.002s\n"
        )
        assert detect_content_type(text) == "go-test"


# -----------------------------------------------------------------------
# Vitest / Jest output
# -----------------------------------------------------------------------

class TestVitest:
    def test_vitest(self):
        text = (
            " ✓ src/utils.test.ts (3 tests) 12ms\n"
            " ✗ src/api.test.ts (1 test) 45ms\n"
            "\n"
            " Test Files  1 failed | 1 passed (2)\n"
            "      Tests  1 failed | 3 passed (4)\n"
        )
        assert detect_content_type(text) == "vitest"

    def test_jest(self):
        text = (
            "PASS src/utils.test.js\n"
            "  ✓ should add numbers (3 ms)\n"
            "  ✓ should subtract numbers (1 ms)\n"
            "\n"
            "Tests:  2 passed, 2 total\n"
        )
        assert detect_content_type(text) == "vitest"


# -----------------------------------------------------------------------
# Git diff
# -----------------------------------------------------------------------

class TestGitDiff:
    def test_unified_diff(self):
        text = (
            "diff --git a/src/main.py b/src/main.py\n"
            "index 1234567..abcdefg 100644\n"
            "--- a/src/main.py\n"
            "+++ b/src/main.py\n"
            "@@ -10,6 +10,8 @@ def main():\n"
            "     print('hello')\n"
            "+    print('world')\n"
            "+    return 0\n"
            "     pass\n"
        )
        assert detect_content_type(text) == "git-diff"

    def test_hunk_markers_only(self):
        text = (
            "--- a/file.txt\n"
            "+++ b/file.txt\n"
            "@@ -1,3 +1,4 @@\n"
            " line1\n"
            "+newline\n"
            " line2\n"
        )
        assert detect_content_type(text) == "git-diff"


# -----------------------------------------------------------------------
# Git status
# -----------------------------------------------------------------------

class TestGitStatus:
    def test_long_format(self):
        text = (
            "On branch main\n"
            "Your branch is up to date with 'origin/main'.\n"
            "\n"
            "Changes not staged for commit:\n"
            "  modified:   src/main.py\n"
            "  modified:   tests/test_main.py\n"
            "\n"
            "Untracked files:\n"
            "  newfile.txt\n"
        )
        assert detect_content_type(text) == "git-status"

    def test_short_format(self):
        text = (
            " M src/main.py\n"
            " M tests/test_main.py\n"
            "?? newfile.txt\n"
            "A  staged.py\n"
        )
        assert detect_content_type(text) == "git-status"


# -----------------------------------------------------------------------
# Git log
# -----------------------------------------------------------------------

class TestGitLog:
    def test_full_log(self):
        text = (
            "commit abc1234567890abcdef1234567890abcdef123456\n"
            "Author: Dev <dev@example.com>\n"
            "Date:   Mon Jan 1 12:00:00 2026 +0000\n"
            "\n"
            "    Fix the thing\n"
            "\n"
            "commit def4567890abcdef1234567890abcdef1234567890\n"
            "Author: Dev <dev@example.com>\n"
            "Date:   Sun Dec 31 11:00:00 2025 +0000\n"
            "\n"
            "    Initial commit\n"
        )
        assert detect_content_type(text) == "git-log"


# -----------------------------------------------------------------------
# Grep / ripgrep
# -----------------------------------------------------------------------

class TestGrep:
    def test_grep_results(self):
        text = (
            "src/main.py:10:def foo():\n"
            "src/main.py:25:def bar():\n"
            "src/utils.py:3:import foo\n"
            "src/utils.py:42:    foo()\n"
        )
        assert detect_content_type(text) == "grep"

    def test_too_few_matches(self):
        text = "src/main.py:10:def foo():\n"
        assert detect_content_type(text) != "grep"


# -----------------------------------------------------------------------
# Find / fd
# -----------------------------------------------------------------------

class TestFind:
    def test_file_paths(self):
        text = (
            "./src/main.py\n"
            "./src/utils.py\n"
            "./src/config.py\n"
            "./tests/test_main.py\n"
            "./tests/test_utils.py\n"
            "./README.md\n"
        )
        assert detect_content_type(text) == "find"


# -----------------------------------------------------------------------
# MyPy
# -----------------------------------------------------------------------

class TestMypy:
    def test_mypy_errors(self):
        text = (
            "src/main.py:10: error: Incompatible return value type\n"
            "src/main.py:25: error: Argument 1 has incompatible type\n"
            'src/utils.py:3: error: Module "foo" has no attribute "bar"\n'
            "Found 3 errors in 2 files\n"
        )
        assert detect_content_type(text) == "mypy"


# -----------------------------------------------------------------------
# TypeScript (tsc)
# -----------------------------------------------------------------------

class TestTsc:
    def test_tsc_errors(self):
        text = (
            "src/app.ts(10,5): error TS2322: Type 'string' is not assignable to type 'number'.\n"
            "src/utils.ts(3,1): error TS2305: Module '\"./foo\"' has no exported member 'bar'.\n"
        )
        assert detect_content_type(text) == "tsc"


# -----------------------------------------------------------------------
# Log deduplication
# -----------------------------------------------------------------------

class TestLogDedup:
    def test_timestamped_logs(self):
        text = (
            "2026-05-31T12:00:01 INFO  Starting server\n"
            "2026-05-31T12:00:02 INFO  Loading config\n"
            "2026-05-31T12:00:03 DEBUG Connected to DB\n"
            "2026-05-31T12:00:04 INFO  Server ready\n"
            "2026-05-31T12:00:05 INFO  Request received\n"
            "2026-05-31T12:00:06 INFO  Request processed\n"
        )
        assert detect_content_type(text) == "log"


# -----------------------------------------------------------------------
# Ruff check (JSON)
# -----------------------------------------------------------------------

class TestRuffCheck:
    def test_ruff_json(self):
        text = (
            '[{"code": "E501", "message": "Line too long", "filename": "src/main.py", "row": 10},'
            ' {"code": "F401", "message": "Unused import", "filename": "src/utils.py", "row": 1}]'
        )
        assert detect_content_type(text) == "ruff-check"


# -----------------------------------------------------------------------
# Prettier
# -----------------------------------------------------------------------

class TestPrettier:
    def test_prettier_check(self):
        text = (
            "Checking formatting...\n"
            "[warn] src/app.tsx\n"
            "[warn] src/utils.ts\n"
            "[warn] Code style issues found. Run Prettier to fix.\n"
        )
        assert detect_content_type(text) == "prettier"


# -----------------------------------------------------------------------
# Edge cases
# -----------------------------------------------------------------------

class TestEdgeCases:
    def test_empty_string(self):
        assert detect_content_type("") is None

    def test_whitespace_only(self):
        assert detect_content_type("   \n\n  ") is None

    def test_plain_text(self):
        assert detect_content_type("Hello, this is just a normal message.") is None

    def test_short_code(self):
        text = "def foo():\n    return 42\n"
        assert detect_content_type(text) is None

    def test_json_without_ruff_markers(self):
        text = '{"key": "value", "count": 42}'
        assert detect_content_type(text) is None
