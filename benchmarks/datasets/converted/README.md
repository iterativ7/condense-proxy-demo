Converted datasets (JSONL)

One JSON object per line. These files are committed and used by the benchmark matrix.


FILES

  profile_manifest.json
    Index: profile name, path, row counts, repeat ratios.
    Read by run_gemini_profile_matrix.py.

  profile_support_faq_high_repeat.jsonl
    ~80% repeat ratio, 160 rows.

  profile_mixed_app_medium_repeat.jsonl
    ~50% repeat, 120 rows.

  profile_mostly_unique_low_repeat.jsonl
    ~15% repeat, 80 rows.

  heavy_coding_language_40.jsonl
    ~31 long unique prompts (coding + language).
    Input to build_production_like_profiles.py.


ROW SHAPE

  id          case id
  request     model, messages, temperature, optional max_tokens
  reference   optional scoring fields
  metadata    traffic_profile, session_id, session_turn


REGENERATE (from repo root)

  1. Optional: python benchmarks/download_llm_benchmark_datasets.py
  2. Optional: python benchmarks/convert_llm_benchmark_datasets.py --limit 50
  3. python benchmarks/build_heavy_token_dataset.py
  4. python benchmarks/build_production_like_profiles.py

Full walkthrough: benchmarks/README.md Step 3.

Intermediate *_50.jsonl files from convert are only needed while rebuilding heavy and profiles.
