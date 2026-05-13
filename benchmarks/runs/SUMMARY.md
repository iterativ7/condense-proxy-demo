# Condense Proxy — 50-case GSM8K Benchmark Summary

Side-by-side comparison of paired baseline (direct upstream) vs Condense proxy.

| Metric | Minimal (gemma3:4b, 50 cases) | Cache-only, primed (gemma3:4b, 50 cases) | Full preset (gemma3:4b, 50 cases) |
|---|---|---|---|
| Cases | 50 | 50 | 50 |
| Cache hit rate | 0% | 100% | 0% |
| Latency p50 (baseline) | 84,210.0 ms | 87,975.2 ms | 84,618.7 ms |
| Latency p50 (proxy) | 50,345.9 ms | 8.204 ms | 49,507.6 ms |
| Latency p95 (proxy) | 177,174.1 ms | 28.501 ms | 174,914.9 ms |
| Latency p99 (proxy) | 221,509.2 ms | 31.874 ms | 221,436.6 ms |
| p50 speedup factor | 1.673x | 10723.456x | 1.709x |
| Tokens — input total (baseline) | 3,365 | 3,365 | 3,365 |
| Tokens — input total (proxy) | 3,582 | 3,582 | 3,582 |
| Tokens — output total (baseline) | 24,680 | 24,680 | 24,680 |
| Tokens — output total (proxy) | 17,287 | 17,287 | 17,287 |
| Tokens — total (baseline) | 28,045 | 28,045 | 28,045 |
| Tokens — total (proxy) | 20,869 | 20,869 | 20,869 |
| Token total savings % | 25.587% | 25.587% | 25.587% |
| Output savings (est., generation only) | 29.955% | 100% | 29.955% |
| Completion tokens avoided by cache (est.) | 0 | 23,532 | 0 |
| Quality — baseline pass rate | 92% | 92% | 92% |
| Quality — proxy pass rate | 78% | 78% | 78% |
| Quality — agreement | 83.33% | 83.33% | 83.33% |
| Cost (baseline) USD | $7.6564 | $7.6564 | $7.6564 |
| Cost (proxy + prime) USD | $5.4547 | $5.4547 | $5.4547 |
| Cost saved USD | $2.2016 | $2.2016 | $2.2016 |
| Cost savings % | 28.755% | 28.755% | 28.755% |

## Notes

- **Token total savings %** is computed from sum of `usage.total_tokens`. When the proxy serves from cache, the response still contains the original `usage` block, so the difference here is largely about cached vs fresh outputs, not necessarily compute saved.
- **Output savings (est., generation only)** is the most honest savings metric: it credits cache hits with avoiding the baseline's completion tokens, and subtracts proxy generations on cache misses and any priming generations (when the runner tracked them).
- **Quality agreement** is the fraction of cases where the proxy's extracted answer equals the baseline's extracted answer (regardless of correctness).
- **Latency** percentiles are end-to-end HTTP round-trip times from the client.

---

## Session handoff — 2026-05-10 (start here tomorrow)

### Where we ended

- All **three** 50-case GSM8K runs on **local Ollama `gemma3:4b`** are **done**: `local-minimal-gemma-50`, `local-cache-only-gemma-50-primed`, `local-full-gemma-50`.
- The benchmark harness was **upgraded**: richer `report.json` (latency p50/p95/p99, token **totals**, **paired** deltas, **generation-savings** estimate for cache, **cost** via `--price-input-per-1k` / `--price-output-per-1k`), per-run **`REPORT.md`**, **`progress.json`** during runs, **`benchmarks/recompute_report.py`**, **`benchmarks/compare_runs.py`** (this file’s table).
- **Words mean:** **proxy = Condense** (port 8080). **Baseline** = **direct Ollama** (11434), no Condense.

### What the Ollama runs showed

- **Cache-only + primed:** **100%** exact-cache hits on measured proxy calls → huge **latency** win; **output / generation savings** row **~100%** and **~23.5k completion tokens avoided** (see table).
- **Minimal vs full:** **0%** cache hits; proxy **~1.7×** faster than baseline on p50 latency; **token and cost % rows matched across all three modes** (same model, temperature 0).
- **Quality:** **92%** / **78%** baseline vs proxy pass rate; **~83%** agreement (proxy answer = baseline’s extracted answer) — same on all three runs.

### How to read it (don’t oversell local-only results)

- The steady **token / cost % vs baseline** is **not a proven “Condense-only” win**: Condense forwards via **LiteLLM**; baseline hits Ollama **directly** — different stacks can change **`usage`** and replies. Treat that as **two paths compared**, not magic, unless you add a **fair baseline** (same client on both sides).
- **Provider cache** in the **full** preset is for **OpenAI / Anthropic–style** prompt caching; it **does not apply** to **local Ollama**. Full run still tested the **pipeline**, not vendor prompt-cache discounts.
- **Routing** on Ollama used the **same** model → no real routing cost story; **cloud** + different models is where routing can show savings.
- **Next step agreed in chat:** run a **small-N** paired benchmark against a **cloud** OpenAI-compatible API (Bearer key, same URL for baseline + proxy, `full` or cloud-tuned preset) to exercise **provider cache + routing + real dollars**.

### Key files

- `benchmarks/run_paired.py` — runner
- `benchmarks/recompute_report.py` — rebuild `report.json` + `REPORT.md` from `results.jsonl`
- `benchmarks/compare_runs.py` — rebuild this `SUMMARY.md`
- `benchmarks/README.md` — docs
- `benchmarks/_orchestrate_two_readme_passes.py` — local two-preset orchestration
- Each run: `benchmarks/runs/<name>/results.jsonl`, `report.json`, `REPORT.md`

### Quick commands (repo root, PowerShell)

```powershell
python benchmarks\run_paired.py `
  --dataset benchmarks\datasets\gsm8k_test_50.jsonl `
  --baseline-url http://127.0.0.1:11434/v1/chat/completions `
  --proxy-url http://127.0.0.1:8080/v1/chat/completions `
  --out-dir benchmarks\runs\my-run `
  --baseline-model gemma3:4b --proxy-model ollama/gemma3:4b `
  --prime-proxy-cache `
  --price-input-per-1k 0.075 --price-output-per-1k 0.30 `
  --preset-label "Cache-only, primed (gemma3:4b)"

Get-Content benchmarks\runs\my-run\progress.json -Wait

python benchmarks\compare_runs.py --output benchmarks\runs\SUMMARY.md `
  --title "Condense Proxy — 50-case GSM8K Benchmark Summary" `
  benchmarks\runs\local-minimal-gemma-50 `
  benchmarks\runs\local-cache-only-gemma-50-primed `
  benchmarks\runs\local-full-gemma-50
```
