Gemini benchmark presets

run_gemini_profile_matrix.py starts Condense with one of these files per run.


FILES

  gemini_minimal.yaml
    Matrix label: minimal
    No optimizations. Passthrough only.

  gemini_cache_only.yaml
    Matrix label: cache_only
    Exact-match memory cache.

  gemini_full.yaml
    Matrix label: full
    Cache, provider cache, routing, budget.

Proxy: 127.0.0.1:8090
Upstream: Gemini (GEMINI_API_KEY)

Run steps: benchmarks/README.md Step 4 and Steps 5–6.
