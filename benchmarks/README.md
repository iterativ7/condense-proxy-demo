Condense benchmarks (Gemini)

This folder runs paired tests: the same prompts go to direct Gemini (baseline) and to the Condense proxy. You get latency, cache hits, billed tokens, and cost in USD.

Run every command from the repository root. The proxy config lives at condense.gemini.yaml (port 8090).


WHAT IS IN benchmarks/

  benchmarks/
    README.md
    gemini_config.py          paths, models, pricing
    gemini_runner.py          start/stop Condense, call run_paired
    run_paired.py             core paired runner
    run_gemini_profile_matrix.py
    summarize_profile_matrix.py
    build_production_like_profiles.py
    build_heavy_token_dataset.py
    convert_llm_benchmark_datasets.py
    download_llm_benchmark_datasets.py
    compare_runs.py
    recompute_report.py
    datasets/
      converted/              JSONL cases (in git)
      llm_benchmarks/         raw downloads (not in git)
    presets/                  baseline + feature modes
    runs/                     outputs (not in git)


BEFORE YOU START

You need Python 3.11+, the repo installed (poetry install or pip install -e .), and a Gemini API key from Google AI Studio.

Create a file named .env in the repo root (never commit it):

  GEMINI_API_KEY=your_key_here
  CONDENSE_CONFIG=condense.gemini.yaml

Load it before long runs.

Linux or macOS:

  set -a && source .env && set +a

Windows PowerShell:

  Get-Content .env | ForEach-Object {
    if ($_ -match '^\s*([^#][^=]*)=(.*)$') { Set-Item -Path "env:$($matches[1].Trim())" -Value $matches[2].Trim() }
  }

If you plan to rebuild datasets from scratch, also install pyarrow and huggingface_hub.


HOW TO RUN (IN ORDER)

Step 1 — Install

  poetry install

Step 2 — Check datasets (benchmarks/datasets/converted/)

The matrix reads profile JSONL files. These should already be in the repo.

  profile_manifest.json
    Index for run_gemini_profile_matrix.py. Lists each profile path and stats.

  profile_support_faq_high_repeat.jsonl
    High repeat traffic (~80% repeats). 160 rows. FAQ / support style.

  profile_mixed_app_medium_repeat.jsonl
    Medium repeat (~50%). 120 rows.

  profile_mostly_unique_low_repeat.jsonl
    Mostly unique prompts (~15% repeats). 80 rows.

  heavy_coding_language_40.jsonl
    Source pool of long coding and language prompts (~31 unique rows).
    Used only when rebuilding profiles, not read directly by the matrix.

Each line in a profile file is one JSON object with:

  id          unique case id
  request     model, messages, temperature, optional max_tokens
  reference   optional scoring (choice, latency_only, …)
  metadata    traffic_profile, session_id, session_turn

Profiles are built from the heavy file with session ordering and controlled repeat ratios. Open profile_manifest.json for exact unique_request counts and repeat_ratio_pct.

Quick check:

  ls benchmarks/datasets/converted/profile_*.jsonl
  ls benchmarks/datasets/converted/profile_manifest.json

More detail: benchmarks/datasets/converted/README.md

If profile files are missing, do Step 3d below, or:

  python benchmarks/build_production_like_profiles.py

Step 3 — Rebuild datasets (optional)

Skip this if Step 2 files are already there.

3a — Download raw data into benchmarks/datasets/llm_benchmarks/ (~4.5 GB, not in git)

  python benchmarks/download_llm_benchmark_datasets.py

See benchmarks/datasets/llm_benchmarks/README.md for the folder layout.

3b — Convert raw data to per-task JSONL in benchmarks/datasets/converted/

  python benchmarks/convert_llm_benchmark_datasets.py --limit 50

This creates humaneval_50.jsonl, mbpp_50.jsonl, hellaswag_50.jsonl, glue_sst2_50.jsonl, glue_cola_50.jsonl. They are build inputs only; you do not need them in git if heavy and profile files already exist.

3c — Build the heavy source pool

  python benchmarks/build_heavy_token_dataset.py

Writes heavy_coding_language_40.jsonl (longest prompts, gemini/gemini-2.5-flash).

3d — Build production-like profiles

  python benchmarks/build_production_like_profiles.py

Writes profile_*.jsonl and profile_manifest.json.

Or run the whole data pipeline:

  make benchmark-data

Step 4 — Presets (benchmarks/presets/)

The matrix supports baseline modes and new feature modes. Each run starts Condense with one YAML file, then runs run_paired.py.

  gemini_minimal.yaml      mode: minimal
    Passthrough only. No Condense cache. Use as a baseline for overhead.

  gemini_cache_only.yaml   mode: cache_only
    Exact-match memory cache only.

  gemini_full.yaml         mode: full
    Cache, provider cache, routing, and budget.

  gemini_full_compression.yaml         mode: full_compression
    Full mode + compression step.

  gemini_full_ml_routing.yaml          mode: full_ml_routing
    Full mode + ML routing (`model_routing`).

  gemini_full_compression_ml_routing.yaml  mode: full_compression_ml_routing
    Full mode + compression + ML routing.

All presets use Gemini upstream and listen on 127.0.0.1:8090.

More detail: benchmarks/presets/README.md

Step 5 — Smoke test

One mode, four rows, about two minutes:

  python benchmarks/run_gemini_profile_matrix.py --limit 4 --modes cache_only full_compression

Output goes under benchmarks/runs/profile-matrix/.

Step 6 — Full matrix

Recommended production rerun order:

  1) Baseline rerun (3 modes): minimal, cache_only, full
  2) Extended rerun (new features): full_compression, full_ml_routing, full_compression_ml_routing

Baseline rerun command:

  python benchmarks/run_gemini_profile_matrix.py \
    --modes minimal cache_only full \
    --out-root benchmarks/runs/profile-matrix-v2-baseline

Extended rerun command:

  python benchmarks/run_gemini_profile_matrix.py \
    --modes full_compression full_ml_routing full_compression_ml_routing \
    --out-root benchmarks/runs/profile-matrix-v2-extended

Each matrix is three profiles times selected modes. Plan for roughly 30 to 90+ minutes per matrix depending on network and API speed.

The runner reads profile_manifest.json, applies each preset, primes each unique prompt once, and retries transient failures with a short delay between cases.

Step 7 — Summarize

Baseline summary:

  python benchmarks/summarize_profile_matrix.py \
    --runs-root benchmarks/runs/profile-matrix-v2-baseline \
    --output benchmarks/runs/profile-matrix-v2-baseline/SUMMARY.md \
    --title "Production benchmark v2 baseline modes"

Extended summary:

  python benchmarks/summarize_profile_matrix.py \
    --runs-root benchmarks/runs/profile-matrix-v2-extended \
    --output benchmarks/runs/profile-matrix-v2-extended/SUMMARY.md \
    --title "Production benchmark v2 feature modes"

Step 8 — Read results (benchmarks/runs/)

Run folders are not committed. After Step 7, open:

  benchmarks/runs/profile-matrix-v2-baseline/SUMMARY.md
  benchmarks/runs/profile-matrix-v2-extended/SUMMARY.md

Each cell is a folder named profile__mode, for example:

  support_faq_high_repeat__cache_only/
  mixed_app_medium_repeat__full/
  mostly_unique_low_repeat__minimal/

Inside each cell:

  REPORT.md       read this first for one run
  report.json     numbers for scripts
  results.jsonl   per-request log
  progress.json   optional; safe to delete when done

Reading savings:

  Warmup-inclusive numbers include every row and every baseline call. First time a prompt appears counts here.

  Steady-state numbers exclude the first occurrence of each unique prompt. Use these for production-like repeat traffic.

  minimal mode should not show cache savings.

  cache_only and full should show high cache hit rates on repeat-heavy profiles in steady-state.

Pricing comes from benchmarks/gemini_config.py (gemini-2.5-flash list prices). Google context-cache discounts are not modeled separately.


ROOT SCRIPTS (benchmarks/)

  gemini_config.py
    Shared paths, model names, USD rates, API URLs.

  gemini_runner.py
    Loads .env, health check, starts and stops Condense, wraps run_paired.

  run_paired.py
    One dataset, one output directory, baseline vs proxy.

  run_gemini_profile_matrix.py
    Main entry: all profiles × all presets.

  summarize_profile_matrix.py
    Builds runs/profile-matrix/SUMMARY.md.

  build_production_like_profiles.py
    Builds profile JSONL files.

  build_heavy_token_dataset.py
    Builds heavy_coding_language_40.jsonl.

  download_llm_benchmark_datasets.py
    Fills datasets/llm_benchmarks/.

  convert_llm_benchmark_datasets.py
    Raw data to *_50.jsonl in converted/.

  compare_runs.py
    Compare any two or more run directories.

  compare_matrix_summary.py
    Compare steady-state metrics between two matrix run roots (v2 baseline vs extended).

  recompute_report.py
    Rebuild REPORT.md from results.jsonl.


MAKE TARGETS

  make benchmark-build      build profiles only (Step 3d)
  make benchmark-run        build profiles + full matrix (Steps 3d and 6)
  make benchmark-summary    Step 7
  make benchmark            build, run, and summarize
  make benchmark-data       full dataset pipeline (Steps 3a–3d)
  make benchmark-lint       ruff on benchmarks/

Production v2 rerun (post-merge codebase):

  make benchmark-v2-baseline   Step 6 baseline matrix only
  make benchmark-v2-extended   Step 6 extended feature matrix only
  make benchmark-v2-summary    Step 7 for both roots
  make benchmark-v2-compare    baseline vs extended steady-state table
  make benchmark-v2            all of the above in order

Compare baseline vs extended (after both matrices finish):

  python benchmarks/compare_matrix_summary.py \
    --left-root benchmarks/runs/profile-matrix-v2-baseline \
    --right-root benchmarks/runs/profile-matrix-v2-extended \
    --left-label baseline_v2 \
    --right-label extended_v2 \
    --output benchmarks/runs/profile-matrix-v2-COMPARISON.md


MANUAL SINGLE RUN

With Condense already running on port 8090:

  python benchmarks/run_paired.py \
    --dataset benchmarks/datasets/converted/profile_support_faq_high_repeat.jsonl \
    --out-dir benchmarks/runs/manual-smoke \
    --preset-label "manual smoke" \
    --prime-proxy-cache-unique \
    --limit 5


COMPARE TWO RUNS

  python benchmarks/compare_runs.py \
    --output benchmarks/runs/SUMMARY.md \
    --title "Custom comparison" \
    benchmarks/runs/profile-matrix/support_faq_high_repeat__cache_only \
    benchmarks/runs/profile-matrix/support_faq_high_repeat__full


TROUBLESHOOTING

  GEMINI_API_KEY is missing
    Add the key to .env and load it (see BEFORE YOU START).

  Profile manifest not found
    Run: python benchmarks/build_production_like_profiles.py

  Missing profile_*.jsonl
    Same as above.

  Missing humaneval_50.jsonl when building heavy
    Run Step 3a and 3b (download and convert).

  Condense health timeout
    Port 8090 may be in use. Stop other Condense processes.

  DNS or 429 errors on long runs
    Re-run only the failed profile__mode folder.

  Negative warmup cost savings
    Expected. Read steady-state rows in SUMMARY.md instead.
