# Benchmark Report — Full preset (gemma3:4b, 50 cases)

_Cases: **50**  •  Started: 2026-05-10T15:53:39.290427+00:00  •  Completed: 2026-05-10T18:19:56.873456+00:00_

- **Dataset:** `C:\Users\Abcom\Downloads\condense-proxy-demo\benchmarks\datasets\gsm8k_test_50.jsonl`
- **Baseline:** `gemma3:4b` @ `http://127.0.0.1:11434/v1/chat/completions`
- **Proxy:** `ollama/gemma3:4b` @ `http://127.0.0.1:8080/v1/chat/completions`

## Headlines

| Metric | Value |
|---|---|
| Cache hit rate | **0%** (0/49) |
| Latency p50 speedup | **1.709x** (41.493% faster) |
| Token total savings | **25.587%** |
| Output token savings (est., generation only) | **29.955%** |
| Cost saved | **$2.202** (28.755%) |
| Quality (baseline → proxy) | **92% → 78%** |
| Quality agreement (proxy=baseline) | **83.33%** |

## Latency (ms)

| Side | count | mean | p50 | p95 | p99 | min | max |
|---|---:|---:|---:|---:|---:|---:|---:|
| baseline | 49 | 96,107.3 | 84,618.7 | 222,360.7 | 228,504.7 | 28,418.5 | 229,154.7 |
| proxy | 49 | 70,768.3 | 49,507.6 | 174,914.9 | 221,436.6 | 19,245.8 | 224,851.0 |
| proxy (cache hits) | 0 | n/a | n/a | n/a | n/a | n/a | n/a |
| proxy (cache misses) | 50 | 75,353.7 | 50,556.4 | 200,345.6 | 263,195.2 | 19,245.8 | 300,035.7 |

- **Paired delta (proxy − baseline) per case:** p50 **-11,035.5 ms**, mean **-24,831.7 ms**
- **p50 speedup:** **1.709x**  •  **p50 savings:** **41.493%**

## Tokens

| | Baseline | Proxy | Savings % |
|---|---:|---:|---:|
| Input | 3,365 | 3,582 | -6.449% |
| Output | 24,680 | 17,287 | 29.955% |
| Total | 28,045 | 20,869 | 25.587% |

**Generation savings (cache effect, estimated):**

- Cache hits: **0**
- Proxy completion tokens generated (cache misses only): **17,287**
- Completion tokens avoided by cache (est.): **0**
- **Estimated output-token savings (incl. priming):** **29.955%**

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
| Proxy pass rate on cache hits | 0% |
| Proxy pass rate on cache misses | 78% |

## Cache

- Hit rate: **0%** (0/49)
- Types: {'none': 49}

## Errors

- baseline errors: 1, non-2xx: 0
- proxy errors: 1, non-2xx: 0

---

_Methodology: paired baseline (direct) vs Condense proxy on the same prompts. Token totals are the sum of OpenAI-compatible `usage` fields. Latency percentiles are computed from per-request HTTP round-trip times. Output-token savings (est.) credits cache hits with avoiding the baseline's completion tokens, then subtracts proxy generations on misses and any priming generations._
