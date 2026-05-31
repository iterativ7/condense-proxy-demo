"""Gemini benchmark defaults, paths, and official API pricing."""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
BENCHMARKS_DIR = REPO_ROOT / "benchmarks"
CONVERTED_DIR = BENCHMARKS_DIR / "datasets" / "converted"
RUNS_DIR = BENCHMARKS_DIR / "runs"
PRESETS_DIR = BENCHMARKS_DIR / "presets"
ENV_FILE = REPO_ROOT / ".env"

PROFILE_MANIFEST = CONVERTED_DIR / "profile_manifest.json"
HEAVY_UNIQUE_DATASET = CONVERTED_DIR / "heavy_coding_language_40.jsonl"
DEFAULT_MATRIX_OUT = RUNS_DIR / "profile-matrix"

# Models (LiteLLM / OpenAI-compatible names)
GEMINI_BASELINE_MODEL = "gemini-2.5-flash"
GEMINI_PROXY_MODEL = "gemini/gemini-2.5-flash"

# Per 1K tokens (derived from per-1M list prices)
GEMINI_PRICE_INPUT_PER_1K = 0.30 / 1000
GEMINI_PRICE_OUTPUT_PER_1K = 2.50 / 1000

GEMINI_PRICING_SOURCE = "https://ai.google.dev/gemini-api/docs/pricing"
GEMINI_PRICING_MODEL = "gemini-2.5-flash"
GEMINI_PRICING_NOTE = (
    "Paid tier list prices for gemini-2.5-flash (text input/output). "
    "Does not include batch discounts or Google context caching."
)

BASELINE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
PROXY_URL = "http://127.0.0.1:8090/v1/chat/completions"
HEALTH_URL = "http://127.0.0.1:8090/health"

CONDENSE_MODES: tuple[tuple[str, str], ...] = (
    ("minimal", "benchmarks/presets/gemini_minimal.yaml"),
    ("cache_only", "benchmarks/presets/gemini_cache_only.yaml"),
    ("full", "benchmarks/presets/gemini_full.yaml"),
    ("full_compression", "benchmarks/presets/gemini_full_compression.yaml"),
    ("full_ml_routing", "benchmarks/presets/gemini_full_ml_routing.yaml"),
    ("full_compression_ml_routing", "benchmarks/presets/gemini_full_compression_ml_routing.yaml"),
)
