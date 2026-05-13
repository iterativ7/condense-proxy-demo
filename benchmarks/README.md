# Benchmarks

This directory contains a v1 offline benchmark harness for paired runs:

- Baseline: direct OpenAI-compatible chat-completions endpoint.
- Condense: `POST /v1/chat/completions` through the proxy.

The runner writes per-case raw results to `results.jsonl` and an aggregate `report.json`.

## Dataset

Download the default 50-case GSM8K test sample:

```bash
poetry run python benchmarks/download_dataset.py --limit 50
```

Output:

```text
benchmarks/datasets/gsm8k_test_50.jsonl
```

Each row uses this contract:

```json
{
  "id": "gsm8k_000",
  "request": {
    "model": "ollama/gemma3:4b",
    "messages": [{"role": "user", "content": "Question here"}],
    "temperature": 0
  },
  "reference": {
    "answer": "Full reference answer with #### final answer",
    "final_answer": "42"
  },
  "metadata": {
    "source": "openai/gsm8k",
    "split": "test"
  }
}
```

The runner also accepts legacy rows that are raw chat-completion POST bodies, like `benchmarks/datasets/sample.jsonl`.

## Start Condense

Start the proxy with one preset at a time. Restart the proxy between presets because config is loaded on process start and the in-memory cache should be reset for predictable results.

```bash
poetry run condense start \
  --config benchmarks/presets/minimal.yaml \
  --host 127.0.0.1 \
  --port 8080
```

Repeat with:

- `benchmarks/presets/minimal.yaml`: ForwardStep only, closest to passthrough.
- `benchmarks/presets/cache_only.yaml`: exact-match cache only.
- `benchmarks/presets/full.yaml`: cache, provider-cache injection, routing, and budget.

Docker is optional. If you use Docker, make sure the mounted `condense.yaml` matches the preset you intend to test.

## Run Paired Benchmark

In another terminal:

```bash
poetry run python benchmarks/run_paired.py \
  --dataset benchmarks/datasets/gsm8k_test_50.jsonl \
  --baseline-url http://127.0.0.1:11434/v1/chat/completions \
  --proxy-url http://127.0.0.1:8080/v1/chat/completions \
  --out-dir benchmarks/runs/minimal \
  --baseline-model gemma3:4b \
  --proxy-model ollama/gemma3:4b
```

For cache-hot measurements, start `cache_only.yaml` and use:

```bash
poetry run python benchmarks/run_paired.py \
  --dataset benchmarks/datasets/gsm8k_test_50.jsonl \
  --baseline-url http://127.0.0.1:11434/v1/chat/completions \
  --proxy-url http://127.0.0.1:8080/v1/chat/completions \
  --out-dir benchmarks/runs/cache-only-hot \
  --baseline-model gemma3:4b \
  --proxy-model ollama/gemma3:4b \
  --prime-proxy-cache
```

`--prime-proxy-cache` sends one unmeasured proxy request before the measured proxy request for each case. Without it, the first run over a fresh cache should report misses; repeating the same dataset against the same running proxy should show hits.

## Report Fields

Each run writes three files:

- **`results.jsonl`** — one row per case (request bodies, responses, latencies, usage, quality scores, cache headers, optional priming record).
- **`report.json`** — structured aggregate (see schema below).
- **`REPORT.md`** — human-readable markdown auto-generated from `report.json`.

`report.json` schema:

- **`latency`** — per-side stats blocks (`count`, `mean`, `p50`, `p95`, `p99`, `min`, `max`) for `baseline`, `proxy`, `proxy_cache_hit`, `proxy_cache_miss`. Plus paired per-case delta (p50/mean), `p50_speedup_factor`, and back-compat median fields.
- **`tokens`** — `totals` (sum of `usage.prompt_tokens`/`completion_tokens`/`total_tokens` per side, plus `*_savings_pct`), `generation_savings` (cache-aware estimate that credits cache hits with avoiding the baseline's `completion_tokens` and subtracts cache-miss proxy generations and any priming generations), `paired_delta` (per-case proxy − baseline distribution), and `prime_overhead` (calls/tokens/latency consumed by `--prime-proxy-cache`).
- **`cost`** — when pricing is configured via `--price-input-per-1k`/`--price-output-per-1k`: `baseline_cost`, `proxy_cost`, `prime_cost`, `proxy_cost_with_prime`, `cost_savings_usd`, `cost_savings_pct`.
- **`cache`** — hit/miss counts and hit rate from `X-Condense-Cache-Hit`, plus type counts from `X-Condense-Cache-Type`.
- **`quality`** — baseline/proxy pass rates, `agreement_rate_proxy_vs_baseline` (fraction of rows where proxy and baseline produced the same extracted answer), and proxy pass rate broken down by cache hits vs misses.
- **`errors`** — request failures and non-2xx counts per side.

Local providers may omit usage or return zeros. `token_metrics_available` is `false` in that case; do not infer token savings from missing usage.

### Stronger answer extraction

Quality scoring tries multiple strategies in order: GSM8K `#### X` marker, `\boxed{X}`, labelled forms (`final answer: X`, `the answer is X`, `= X`), and last-number fallback. Output is normalized for commas, currency, percent signs, and trailing punctuation. There is no LLM judge.

### Live progress

While a run is in flight, `progress.json` is written to the run's `--out-dir` after every case and contains `completed`, `total`, `case_id`, and `updated_at`. Tail it with `Get-Content path\to\progress.json -Wait`.

## Re-aggregating an existing run

If a run already wrote `results.jsonl`, you can rebuild a richer `report.json` and `REPORT.md` without re-running the model:

```bash
python benchmarks/recompute_report.py benchmarks/runs/<run-dir> \
  --price-input-per-1k 0.075 \
  --price-output-per-1k 0.30 \
  --preset-label "Minimal (gemma3:4b, 50 cases)"
```

This re-extracts answers with the upgraded scorer, recomputes percentiles/totals, and writes a fresh markdown summary.

## Cross-run comparison

`benchmarks/compare_runs.py` builds a side-by-side `SUMMARY.md` across multiple run directories:

```bash
python benchmarks/compare_runs.py \
  --output benchmarks/runs/SUMMARY.md \
  --title "Condense Proxy — 50-case GSM8K Benchmark Summary" \
  benchmarks/runs/local-minimal-gemma-50 \
  benchmarks/runs/local-cache-only-gemma-50-primed \
  benchmarks/runs/local-full-gemma-50
```

## Auth Notes

Use `--authorization "Bearer ..."` when benchmarking cloud providers. The same header is sent to both baseline and proxy requests.

Different auth headers create different Condense cache namespaces, so keep auth stable across cache runs if you want comparable cache behavior.
