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

  gemini_full_compression.yaml
    Matrix label: full_compression
    Full mode plus compression step (Fusion backend).

  gemini_full_ml_routing.yaml
    Matrix label: full_ml_routing
    Full mode plus ML routing (`model_routing` with RouteLLM `bert` strategy).

  gemini_full_compression_ml_routing.yaml
    Matrix label: full_compression_ml_routing
    Full mode with both compression and ML routing enabled.

Proxy: 127.0.0.1:8090
Upstream: Gemini (GEMINI_API_KEY)

Run steps: benchmarks/README.md Step 4 and Steps 5–6.
