# Benchmark Report — Cache-only, primed (gemma3:4b, 50 cases)

_Cases: **50**  •  Started: 2026-05-10T13:19:15.696425+00:00  •  Completed: 2026-05-10T15:53:29.292459+00:00_

- **Dataset:** `C:\Users\Abcom\Downloads\condense-proxy-demo\benchmarks\datasets\gsm8k_test_50.jsonl`
- **Baseline:** `gemma3:4b` @ `http://127.0.0.1:11434/v1/chat/completions`
- **Proxy:** `ollama/gemma3:4b` @ `http://127.0.0.1:8080/v1/chat/completions`

## Headlines

| Metric | Value |
|---|---|
| Cache hit rate | **100%** (49/49) |
| Latency p50 speedup | **10,723.5x** (99.991% faster) |
| Token total savings | **25.587%** |
| Output token savings (est., generation only) | **100%** |
| Cost saved | **$2.202** (28.755%) |
| Quality (baseline → proxy) | **92% → 78%** |
| Quality agreement (proxy=baseline) | **83.33%** |

## Latency (ms)

| Side | count | mean | p50 | p95 | p99 | min | max |
|---|---:|---:|---:|---:|---:|---:|---:|
| baseline | 49 | 98,544.0 | 87,975.2 | 218,376.0 | 230,539.8 | 28,778.1 | 231,529.9 |
| proxy | 49 | 13.296 | 8.204 | 28.501 | 31.874 | 5.822 | 34.437 |
| proxy (cache hits) | 49 | 13.296 | 8.204 | 28.501 | 31.874 | 5.822 | 34.437 |
| proxy (cache misses) | 1 | 300,032.6 | 300,032.6 | 300,032.6 | 300,032.6 | 300,032.6 | 300,032.6 |

- **Paired delta (proxy − baseline) per case:** p50 **-87,950.8 ms**, mean **-96,559.6 ms**
- **p50 speedup:** **10,723.5x**  •  **p50 savings:** **99.991%**

## Tokens

| | Baseline | Proxy | Savings % |
|---|---:|---:|---:|
| Input | 3,365 | 3,582 | -6.449% |
| Output | 24,680 | 17,287 | 29.955% |
| Total | 28,045 | 20,869 | 25.587% |

**Generation savings (cache effect, estimated):**

- Cache hits: **49**
- Proxy completion tokens generated (cache misses only): **0**
- Completion tokens avoided by cache (est.): **23,532**
- **Estimated output-token savings (incl. priming):** **100%**

**Paired (proxy − baseline) total tokens per case:** p50 -71.5, mean -128.5, min -878, max 331

## Cost (USD)

_Pricing per 1K tokens — input: **$0.075**, output: **$0.3**_

| | Cost |
|---|---:|
| Baseline (direct) | $7.656 |
| Proxy | $5.455 |
| Proxy priming overhead | $0 |
| **Proxy total (with prime)** | **$5.455** |
| **Cost saved** | **$2.202 (28.755%)** |

## Quality

| Metric | Value |
|---|---:|
| Baseline pass rate | 92% |
| Proxy pass rate | 78% |
| Proxy − baseline | -14 pts |
| Agreement (proxy answer == baseline answer) | 83.33% |
| Proxy pass rate on cache hits | 79.59% |
| Proxy pass rate on cache misses | 0% |

## Cache

- Hit rate: **100%** (49/49)
- Types: {'exact': 49}

## Errors

- baseline errors: 1, non-2xx: 0
- proxy errors: 1, non-2xx: 0

---

_Methodology: paired baseline (direct) vs Condense proxy on the same prompts. Token totals are the sum of OpenAI-compatible `usage` fields. Latency percentiles are computed from per-request HTTP round-trip times. Output-token savings (est.) credits cache hits with avoiding the baseline's completion tokens, then subtracts proxy generations on misses and any priming generations._
