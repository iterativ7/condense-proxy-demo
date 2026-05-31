#!/usr/bin/env python3
"""Run paired baseline (direct upstream) vs Condense proxy benchmarks."""

from __future__ import annotations

import argparse
import concurrent.futures
import copy
import hashlib
import json
import re
import statistics
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from benchmarks.gemini_config import (  # noqa: E402
    BASELINE_URL,
    CONVERTED_DIR,
    GEMINI_BASELINE_MODEL,
    GEMINI_PRICE_INPUT_PER_1K,
    GEMINI_PRICE_OUTPUT_PER_1K,
    GEMINI_PROXY_MODEL,
    PROXY_URL,
)

DEFAULT_DATASET = CONVERTED_DIR / "profile_support_faq_high_repeat.jsonl"
DEFAULT_BASELINE_URL = BASELINE_URL
DEFAULT_PROXY_URL = PROXY_URL


def _utc_timestamp() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


def _load_jsonl(path: Path, limit: int | None = None) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, start=1):
            stripped = line.strip()
            if not stripped:
                continue
            try:
                rows.append(json.loads(stripped))
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{line_no} is not valid JSON: {exc}") from exc
            if limit is not None and len(rows) >= limit:
                break
    return rows


def _normalize_case(row: dict[str, Any], index: int) -> dict[str, Any]:
    """Accept v1 benchmark rows and legacy raw chat-completion request rows."""
    if isinstance(row.get("request"), dict):
        request = copy.deepcopy(row["request"])
        case_id = str(row.get("id") or f"case_{index:03d}")
        reference = row.get("reference") if isinstance(row.get("reference"), dict) else {}
        metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    else:
        request = copy.deepcopy(row)
        case_id = str(row.get("id") or f"case_{index:03d}")
        reference = {}
        metadata = {"legacy_raw_request": True}

    return {
        "id": case_id,
        "request": request,
        "reference": reference,
        "metadata": metadata,
    }


def _apply_model(request: dict[str, Any], model: str | None) -> dict[str, Any]:
    mapped = copy.deepcopy(request)
    if model:
        mapped["model"] = model
    return mapped


def _headers(auth_header: str | None) -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    if auth_header:
        headers["Authorization"] = auth_header
    return headers


_RETRYABLE_STATUS = {429, 500, 502, 503, 504}


def _is_retryable_result(result: dict[str, Any]) -> bool:
    code = result.get("status_code")
    if code is None:
        return result.get("error") is not None
    return int(code) in _RETRYABLE_STATUS


def _post_json_once(
    client: httpx.Client,
    url: str,
    request_body: dict[str, Any],
    auth_header: str | None,
) -> dict[str, Any]:
    start = time.perf_counter()
    try:
        response = client.post(url, json=request_body, headers=_headers(auth_header))
        latency_ms = (time.perf_counter() - start) * 1000
        try:
            response_json: Any = response.json()
        except json.JSONDecodeError:
            response_json = {"raw_text": response.text}

        usage = _usage(response_json)
        return {
            "status_code": response.status_code,
            "latency_ms": round(latency_ms, 3),
            "headers": dict(response.headers),
            "x_condense_headers": {
                key: value
                for key, value in response.headers.items()
                if key.lower().startswith("x-condense-")
            },
            "response_json": response_json,
            "assistant_text": _assistant_text(response_json),
            "usage": usage,
            "prompt_tokens": _int_or_none(usage.get("prompt_tokens")),
            "completion_tokens": _int_or_none(usage.get("completion_tokens")),
            "total_tokens": _int_or_none(usage.get("total_tokens")),
            "error": None,
        }
    except httpx.HTTPError as exc:
        latency_ms = (time.perf_counter() - start) * 1000
        return {
            "status_code": None,
            "latency_ms": round(latency_ms, 3),
            "headers": {},
            "x_condense_headers": {},
            "response_json": None,
            "assistant_text": "",
            "usage": {},
            "prompt_tokens": None,
            "completion_tokens": None,
            "total_tokens": None,
            "error": str(exc),
        }


def _post_json(
    client: httpx.Client,
    url: str,
    request_body: dict[str, Any],
    auth_header: str | None,
    *,
    max_retries: int = 1,
    retry_backoff_s: float = 1.0,
) -> dict[str, Any]:
    attempts = max(1, int(max_retries))
    result: dict[str, Any] = {}
    for attempt in range(attempts):
        if attempt > 0:
            delay = retry_backoff_s * (2 ** (attempt - 1))
            time.sleep(delay)
        result = _post_json_once(client, url, request_body, auth_header)
        if not _is_retryable_result(result):
            break
    if attempts > 1 and _is_retryable_result(result):
        result["retries_exhausted"] = attempts
    return result


def _int_or_none(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float) and value >= 0:
        return int(value)
    return None


def _assistant_text(response_json: Any) -> str:
    if not isinstance(response_json, dict):
        return ""
    choices = response_json.get("choices")
    if not isinstance(choices, list) or not choices:
        return ""

    first = choices[0]
    if not isinstance(first, dict):
        return ""
    message = first.get("message")
    if isinstance(message, dict):
        content = message.get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            return " ".join(
                block.get("text", "")
                for block in content
                if isinstance(block, dict) and isinstance(block.get("text"), str)
            )

    text = first.get("text")
    return text if isinstance(text, str) else ""


def _usage(response_json: Any) -> dict[str, Any]:
    if isinstance(response_json, dict) and isinstance(response_json.get("usage"), dict):
        return response_json["usage"]
    return {}


def _valid_total_tokens(call: dict[str, Any]) -> int | None:
    """Return total_tokens if present and > 0, else None.

    Reads from the canonical top-level field first (new schema), then falls
    back to nested usage.total_tokens for older results.jsonl.
    """
    direct = call.get("total_tokens")
    if isinstance(direct, int | float) and direct > 0:
        return int(direct)
    total = call.get("usage", {}).get("total_tokens")
    if isinstance(total, int | float) and total > 0:
        return int(total)
    return None


def _token_field(call: dict[str, Any], field: str) -> int | None:
    """Get a token field from either the new top-level shape or nested usage."""
    direct = call.get(field)
    if isinstance(direct, int | float) and direct >= 0:
        return int(direct)
    nested = call.get("usage", {}).get(field)
    if isinstance(nested, int | float) and nested >= 0:
        return int(nested)
    return None


def _is_proxy_cache_hit(row: dict[str, Any]) -> bool:
    headers = {
        str(k).lower(): str(v).lower()
        for k, v in row.get("proxy", {}).get("x_condense_headers", {}).items()
    }
    return headers.get("x-condense-cache-hit") == "true"


def _request_fingerprint(request: dict[str, Any]) -> str:
    """Stable key for duplicate prompts (model + messages + temperature)."""
    payload = {
        "model": request.get("model"),
        "messages": request.get("messages"),
        "temperature": request.get("temperature"),
        "max_tokens": request.get("max_tokens"),
    }
    blob = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _condense_savings_usd(row: dict[str, Any]) -> float | None:
    raw = row.get("proxy", {}).get("x_condense_headers", {}).get("x-condense-savings-usd")
    if raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _billed_proxy_tokens(row: dict[str, Any]) -> tuple[int, int]:
    """Tokens that would be billed upstream: 0 on cache hits, full usage on misses."""
    if _is_proxy_cache_hit(row):
        return 0, 0
    prompt = _token_field(row["proxy"], "prompt_tokens") or 0
    completion = _token_field(row["proxy"], "completion_tokens") or 0
    return prompt, completion


def _median(values: list[float | int]) -> float | None:
    if not values:
        return None
    return round(float(statistics.median(values)), 3)


def _mean(values: list[float | int]) -> float | None:
    if not values:
        return None
    return round(float(statistics.fmean(values)), 3)


def _percentile(values: list[float | int], pct: float) -> float | None:
    """Linear-interpolation percentile (numpy-compatible). pct is 0..100."""
    if not values:
        return None
    if len(values) == 1:
        return round(float(values[0]), 3)
    sorted_values = sorted(float(v) for v in values)
    rank = (pct / 100.0) * (len(sorted_values) - 1)
    low = int(rank)
    high = min(low + 1, len(sorted_values) - 1)
    frac = rank - low
    return round(sorted_values[low] + (sorted_values[high] - sorted_values[low]) * frac, 3)


def _stats_block(values: list[float | int]) -> dict[str, Any]:
    return {
        "count": len(values),
        "mean": _mean(values),
        "p50": _median(values),
        "p95": _percentile(values, 95),
        "p99": _percentile(values, 99),
        "min": round(float(min(values)), 3) if values else None,
        "max": round(float(max(values)), 3) if values else None,
    }


def _percent_delta(baseline: float | int | None, proxy: float | int | None) -> float | None:
    if baseline in (None, 0) or proxy is None:
        return None
    return round(((float(proxy) - float(baseline)) / float(baseline)) * 100, 3)


def _percent_savings(baseline: float | int | None, proxy: float | int | None) -> float | None:
    """Positive % when proxy < baseline (i.e. savings)."""
    if baseline in (None, 0) or proxy is None:
        return None
    return round(((float(baseline) - float(proxy)) / float(baseline)) * 100, 3)


def _safe_div(a: float | int | None, b: float | int | None) -> float | None:
    if a is None or b in (None, 0):
        return None
    return round(float(a) / float(b), 3)


_NUMBER_RE = re.compile(r"-?\d+(?:,\d{3})*(?:\.\d+)?")


def _normalize_answer(value: str) -> str:
    normalized = value.strip().replace(",", "").replace("$", "").replace("%", "")
    while normalized.endswith((".", ":", ";", ")", "]", "}", " ")):
        normalized = normalized[:-1]
    if normalized.endswith(".0"):
        normalized = normalized[:-2]
    return normalized


def _normalize_label(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip().lower())


_CHOICE_RE = re.compile(r"\b([A-Da-d])\b")


def _last_number(text: str) -> str | None:
    numbers = _NUMBER_RE.findall(text)
    if not numbers:
        return None
    return _normalize_answer(numbers[-1])


def _extract_final_answer(text: str) -> str | None:
    """Try a sequence of strategies to extract a final numeric answer.

    Order: GSM8K marker (#### X), \\boxed{X}, "answer is X" / "final answer: X",
    last "= X" before EOS, then last number in text.
    """
    if not isinstance(text, str) or not text.strip():
        return None

    if "####" in text:
        candidate = text.rsplit("####", 1)[1]
        num = _NUMBER_RE.search(candidate)
        if num:
            return _normalize_answer(num.group(0))

    boxed = re.findall(r"\\boxed\{([^{}]+)\}", text)
    if boxed:
        num = _NUMBER_RE.search(boxed[-1])
        if num:
            return _normalize_answer(num.group(0))

    label_re = re.compile(
        r"(?:final\s+answer|the\s+answer\s+is|answer\s*[:=]|=\s*)\s*\$?\s*(-?\d+(?:,\d{3})*(?:\.\d+)?)",
        re.IGNORECASE,
    )
    matches = label_re.findall(text)
    if matches:
        return _normalize_answer(matches[-1])

    return _last_number(text)


def _scoring_mode(reference: dict[str, Any]) -> str:
    scoring = reference.get("scoring")
    if isinstance(scoring, str) and scoring.strip():
        return scoring.strip().lower()
    return "numeric"


def _reference_expected(reference: dict[str, Any]) -> str | None:
    scoring = _scoring_mode(reference)
    if scoring in {"latency_only", "none"}:
        return None

    final = reference.get("final_answer")
    if isinstance(final, str) and final.strip():
        if scoring == "code_entry":
            return final.strip()
        if scoring in {"label", "choice", "text"}:
            return _normalize_label(final) if scoring != "choice" else final.strip().upper()
        return _normalize_answer(final)

    answer = reference.get("answer")
    if isinstance(answer, str) and answer.strip():
        if scoring in {"label", "text"}:
            return _normalize_label(answer)
        return _extract_final_answer(answer)
    return None


def _extract_observed_answer(text: str, scoring: str) -> str | None:
    if not isinstance(text, str) or not text.strip():
        return None

    if scoring == "choice":
        matches = _CHOICE_RE.findall(text)
        if matches:
            return matches[-1].upper()
        digit = re.search(r"\b([0-3])\b", text)
        if digit:
            return chr(65 + int(digit.group(1)))
        return None

    if scoring in {"label", "text"}:
        first_line = text.strip().splitlines()[0] if text.strip() else ""
        cleaned = re.sub(r"[^a-zA-Z0-9 _-]", "", first_line)
        if cleaned.strip():
            return _normalize_label(cleaned)
        return _normalize_label(text)

    observed = _extract_final_answer(text)
    return observed


def _quality_result(reference: dict[str, Any], assistant_text: str) -> dict[str, Any]:
    scoring = _scoring_mode(reference)
    expected = _reference_expected(reference)
    if scoring == "code_entry" and expected:
        present = expected in assistant_text
        return {
            "available": True,
            "pass": present,
            "expected": expected,
            "observed": "entry_point_present" if present else "entry_point_missing",
            "scoring": scoring,
        }

    observed = _extract_observed_answer(assistant_text, scoring)
    if expected is None:
        return {
            "available": False,
            "pass": None,
            "expected": None,
            "observed": observed,
            "scoring": scoring,
        }
    return {
        "available": True,
        "pass": observed == expected,
        "expected": expected,
        "observed": observed,
        "scoring": scoring,
    }


def _pass_rate(rows: list[dict[str, Any]], side: str) -> float | None:
    quality = [row["quality"][side] for row in rows if row["quality"][side]["available"]]
    if not quality:
        return None
    passed = sum(1 for item in quality if item["pass"])
    return round(passed / len(quality), 4)


def _cache_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    hits = 0
    misses = 0
    cache_types: dict[str, int] = {}

    for row in rows:
        headers = {
            key.lower(): value
            for key, value in row["proxy"].get("x_condense_headers", {}).items()
        }
        hit = headers.get("x-condense-cache-hit")
        if isinstance(hit, str):
            if hit.lower() == "true":
                hits += 1
            elif hit.lower() == "false":
                misses += 1

        cache_type = headers.get("x-condense-cache-type")
        if cache_type:
            cache_types[cache_type] = cache_types.get(cache_type, 0) + 1

    total = hits + misses
    return {
        "cache_hit_count": hits,
        "cache_miss_count": misses,
        "cache_observed_count": total,
        "cache_hit_rate": round(hits / total, 4) if total else None,
        "cache_types": cache_types,
    }


def _build_report(
    rows: list[dict[str, Any]],
    args: argparse.Namespace,
    started_at: str,
    completed_at: str,
) -> dict[str, Any]:
    case_count = len(rows)

    # ---- Latency: full distribution, both sides + per cache state ----
    baseline_latencies = [
        row["baseline"]["latency_ms"]
        for row in rows
        if row["baseline"].get("status_code") is not None
        and row["baseline"].get("latency_ms") is not None
    ]
    proxy_latencies = [
        row["proxy"]["latency_ms"]
        for row in rows
        if row["proxy"].get("status_code") is not None
        and row["proxy"].get("latency_ms") is not None
    ]
    proxy_hit_latencies = [
        row["proxy"]["latency_ms"]
        for row in rows
        if _is_proxy_cache_hit(row) and row["proxy"].get("latency_ms") is not None
    ]
    proxy_miss_latencies = [
        row["proxy"]["latency_ms"]
        for row in rows
        if not _is_proxy_cache_hit(row) and row["proxy"].get("latency_ms") is not None
    ]

    baseline_lat_stats = _stats_block(baseline_latencies)
    proxy_lat_stats = _stats_block(proxy_latencies)

    paired_latency_deltas = [
        row["proxy"]["latency_ms"] - row["baseline"]["latency_ms"]
        for row in rows
        if row["baseline"].get("latency_ms") is not None
        and row["proxy"].get("latency_ms") is not None
    ]

    speedup = (
        round(baseline_lat_stats["p50"] / proxy_lat_stats["p50"], 3)
        if baseline_lat_stats["p50"] and proxy_lat_stats["p50"]
        else None
    )

    # ---- Tokens: input-only totals, paired deltas, savings ----
    token_pairs = [
        (b_input, p_input, row)
        for row in rows
        if (b_input := _token_field(row["baseline"], "prompt_tokens")) is not None
        and (p_input := _token_field(row["proxy"], "prompt_tokens")) is not None
    ]
    paired_token_deltas = [pair[1] - pair[0] for pair in token_pairs]

    baseline_input_total = sum(
        v for row in rows if (v := _token_field(row["baseline"], "prompt_tokens")) is not None
    )
    baseline_output_total = sum(
        v for row in rows if (v := _token_field(row["baseline"], "completion_tokens")) is not None
    )
    baseline_total_total = sum(
        v for row in rows if (v := _token_field(row["baseline"], "total_tokens")) is not None
    )
    proxy_input_total = sum(
        v for row in rows if (v := _token_field(row["proxy"], "prompt_tokens")) is not None
    )
    proxy_output_total = sum(
        v for row in rows if (v := _token_field(row["proxy"], "completion_tokens")) is not None
    )
    proxy_total_total = sum(
        v for row in rows if (v := _token_field(row["proxy"], "total_tokens")) is not None
    )

    proxy_input_billed = sum(_billed_proxy_tokens(row)[0] for row in rows)
    proxy_output_billed = sum(_billed_proxy_tokens(row)[1] for row in rows)
    proxy_total_billed = proxy_input_billed

    cache_hit_count = sum(1 for row in rows if _is_proxy_cache_hit(row))
    condense_savings_usd_total = round(
        sum(v for row in rows if (v := _condense_savings_usd(row)) is not None),
        6,
    )

    # Estimate tokens "actually generated" by the proxy:
    #   miss row -> proxy did call upstream -> count proxy completion_tokens
    #   hit row  -> proxy returned cached -> count 0 (no new generation)
    proxy_completion_generated = sum(
        v
        for row in rows
        if not _is_proxy_cache_hit(row)
        and (v := _token_field(row["proxy"], "completion_tokens")) is not None
    )
    # "Tokens avoided" = baseline completion tokens on rows the proxy served from cache
    tokens_avoided_by_cache = sum(
        v
        for row in rows
        if _is_proxy_cache_hit(row)
        and (v := _token_field(row["baseline"], "completion_tokens")) is not None
    )

    # Prime overhead: optional, populated by the runner (via row["prime"]).
    prime_calls = sum(
        1 for row in rows if row.get("prime", {}).get("attempted") and not row.get("prime", {}).get("skipped")
    )
    prime_input_tokens = sum(
        v
        for row in rows
        if row.get("prime", {}).get("attempted")
        and not row.get("prime", {}).get("skipped")
        and (v := _token_field(row.get("prime", {}), "prompt_tokens")) is not None
    )
    prime_output_tokens = sum(
        v
        for row in rows
        if row.get("prime", {}).get("attempted")
        and not row.get("prime", {}).get("skipped")
        and (v := _token_field(row.get("prime", {}), "completion_tokens")) is not None
    )
    prime_total_tokens = sum(
        v
        for row in rows
        if row.get("prime", {}).get("attempted")
        and not row.get("prime", {}).get("skipped")
        and (v := _token_field(row.get("prime", {}), "total_tokens")) is not None
    )
    prime_skipped = sum(1 for row in rows if row.get("prime", {}).get("skipped"))
    prime_latency_ms_total = sum(
        v
        for row in rows
        if (v := row.get("prime", {}).get("latency_ms")) is not None
    )

    # ---- Cost (USD): proxy cost vs hypothetical baseline-only cost ----
    p_in = float(getattr(args, "price_input_per_1k", 0.0) or 0.0)
    p_out = float(getattr(args, "price_output_per_1k", 0.0) or 0.0)

    def _cost(input_tokens: int, output_tokens: int) -> float:
        return round((input_tokens / 1000.0) * p_in + (output_tokens / 1000.0) * p_out, 6)

    baseline_cost = _cost(baseline_input_total, baseline_output_total)
    proxy_cost_raw = _cost(proxy_input_total, proxy_output_total)
    proxy_cost_billed = _cost(proxy_input_billed, proxy_output_billed)
    prime_cost = _cost(prime_input_tokens, prime_output_tokens)
    proxy_cost_with_prime = round(proxy_cost_billed + prime_cost, 6)
    cost_savings_usd = round(baseline_cost - proxy_cost_with_prime, 6)
    cost_savings_pct = _percent_savings(baseline_cost, proxy_cost_with_prime) if p_in or p_out else None

    # ---- Steady-state view (exclude first request per unique prompt fingerprint) ----
    seen_fingerprints: set[str] = set()
    steady_rows: list[dict[str, Any]] = []
    for row in rows:
        req = row.get("proxy_request") if isinstance(row.get("proxy_request"), dict) else row.get("baseline_request")
        if not isinstance(req, dict):
            continue
        fp = _request_fingerprint(req)
        if fp in seen_fingerprints:
            steady_rows.append(row)
        else:
            seen_fingerprints.add(fp)

    steady_baseline_input = sum(
        v for row in steady_rows if (v := _token_field(row["baseline"], "prompt_tokens")) is not None
    )
    steady_baseline_output = sum(
        v for row in steady_rows if (v := _token_field(row["baseline"], "completion_tokens")) is not None
    )
    steady_baseline_total = steady_baseline_input
    steady_proxy_input_billed = sum(_billed_proxy_tokens(row)[0] for row in steady_rows)
    steady_proxy_output_billed = sum(_billed_proxy_tokens(row)[1] for row in steady_rows)
    steady_proxy_total_billed = steady_proxy_input_billed
    steady_cache_hits = sum(1 for row in steady_rows if _is_proxy_cache_hit(row))
    steady_baseline_cost = _cost(steady_baseline_input, steady_baseline_output)
    steady_proxy_cost = _cost(steady_proxy_input_billed, steady_proxy_output_billed)
    steady_cost_savings_usd = round(steady_baseline_cost - steady_proxy_cost, 6)
    steady_cost_savings_pct = (
        _percent_savings(steady_baseline_cost, steady_proxy_cost) if p_in or p_out else None
    )
    steady_output_savings_pct_billed = _percent_savings(
        steady_baseline_output, steady_proxy_output_billed
    )

    # ---- Quality ----
    baseline_quality = _pass_rate(rows, "baseline")
    proxy_quality = _pass_rate(rows, "proxy")
    quality_agreement = _agreement_rate(rows)
    proxy_hits_pass = _pass_rate(
        [row for row in rows if _is_proxy_cache_hit(row)], "proxy"
    )
    proxy_misses_pass = _pass_rate(
        [row for row in rows if not _is_proxy_cache_hit(row)], "proxy"
    )

    return {
        "run": {
            "started_at": started_at,
            "completed_at": completed_at,
            "dataset": str(args.dataset),
            "out_dir": str(args.out_dir),
            "case_count": case_count,
            "baseline_url": args.baseline_url,
            "proxy_url": args.proxy_url,
            "baseline_model": args.baseline_model,
            "proxy_model": args.proxy_model,
            "preset_label": getattr(args, "preset_label", None),
            "price_input_per_1k": p_in,
            "price_output_per_1k": p_out,
        },
        "latency": {
            "baseline": baseline_lat_stats,
            "proxy": proxy_lat_stats,
            "proxy_cache_hit": _stats_block(proxy_hit_latencies),
            "proxy_cache_miss": _stats_block(proxy_miss_latencies),
            "paired_delta_ms_p50": _median(paired_latency_deltas),
            "paired_delta_ms_mean": _mean(paired_latency_deltas),
            "p50_speedup_factor": speedup,
            "p50_savings_pct": _percent_savings(baseline_lat_stats["p50"], proxy_lat_stats["p50"]),
            # Back-compat fields (older readers)
            "baseline_median_latency_ms": baseline_lat_stats["p50"],
            "proxy_median_latency_ms": proxy_lat_stats["p50"],
            "latency_delta_ms": (
                round(proxy_lat_stats["p50"] - baseline_lat_stats["p50"], 3)
                if baseline_lat_stats["p50"] is not None and proxy_lat_stats["p50"] is not None
                else None
            ),
            "latency_delta_percent": _percent_delta(
                baseline_lat_stats["p50"], proxy_lat_stats["p50"]
            ),
        },
        "tokens": {
            "token_metrics_available": bool(token_pairs),
            "valid_token_pair_count": len(token_pairs),
            "totals": {
                "baseline_input_tokens": baseline_input_total,
                "baseline_output_tokens": baseline_output_total,
                "baseline_total_tokens": baseline_input_total,
                "proxy_input_tokens": proxy_input_total,
                "proxy_output_tokens": proxy_output_total,
                "proxy_total_tokens": proxy_input_total,
                "proxy_input_tokens_billed": proxy_input_billed,
                "proxy_output_tokens_billed": proxy_output_billed,
                "proxy_total_tokens_billed": proxy_input_billed,
                "input_savings_pct": _percent_savings(baseline_input_total, proxy_input_total),
                "output_savings_pct": _percent_savings(baseline_output_total, proxy_output_total),
                # Keep headline savings non-negative to avoid reporting
                # provider-tokenization noise as negative "savings" on no-hit runs.
                "total_savings_pct": _percent_savings(
                    baseline_input_total, proxy_input_billed
                ),
                "total_savings_pct_raw": _percent_savings(
                    baseline_input_total, proxy_input_total
                ),
                "total_savings_pct_billed": _percent_savings(
                    baseline_input_total, proxy_input_billed
                ),
                "output_savings_pct_billed": _percent_savings(
                    baseline_output_total, proxy_output_billed + prime_output_tokens
                ),
            },
            "generation_savings": {
                "cache_hit_count": cache_hit_count,
                "proxy_generated_completion_tokens_excl_cache": proxy_completion_generated,
                "completion_tokens_avoided_by_cache_est": tokens_avoided_by_cache,
                "completion_tokens_savings_pct_est": _percent_savings(
                    baseline_output_total,
                    proxy_completion_generated + prime_output_tokens,
                ),
                "notes": (
                    "Cache-hit rows are estimated to avoid generating the baseline's "
                    "completion_tokens. 'savings_pct_est' subtracts both proxy generations "
                    "on cache misses and any priming generations."
                ),
            },
            "paired_delta": {
                "p50_proxy_minus_baseline": _median(paired_token_deltas),
                "mean_proxy_minus_baseline": _mean(paired_token_deltas),
                "min_proxy_minus_baseline": (
                    int(min(paired_token_deltas)) if paired_token_deltas else None
                ),
                "max_proxy_minus_baseline": (
                    int(max(paired_token_deltas)) if paired_token_deltas else None
                ),
            },
            "prime_overhead": {
                "prime_call_count": prime_calls,
                "prime_skipped_count": prime_skipped,
                "prime_input_tokens": prime_input_tokens,
                "prime_output_tokens": prime_output_tokens,
                "prime_total_tokens": prime_total_tokens,
                "prime_latency_ms_total": round(prime_latency_ms_total, 3),
            },
            "condense_reported_savings_usd_total": condense_savings_usd_total,
            # Back-compat fields
            "baseline_median_total_tokens": _median([pair[0] for pair in token_pairs]),
            "proxy_median_total_tokens": _median([pair[1] for pair in token_pairs]),
            "token_delta": (
                int(_median([pair[1] for pair in token_pairs]) - _median([pair[0] for pair in token_pairs]))
                if token_pairs
                else None
            ),
            "token_delta_percent": _percent_delta(
                _median([pair[0] for pair in token_pairs]) if token_pairs else None,
                _median([pair[1] for pair in token_pairs]) if token_pairs else None,
            ),
        },
        "cost": {
            "currency": "USD",
            "price_input_per_1k": p_in,
            "price_output_per_1k": p_out,
            "baseline_cost": baseline_cost,
            "proxy_cost": proxy_cost_billed,
            "proxy_cost_raw_usage": proxy_cost_raw,
            "prime_cost": prime_cost,
            "proxy_cost_with_prime": proxy_cost_with_prime,
            "cost_savings_usd": cost_savings_usd,
            "cost_savings_pct": cost_savings_pct,
            "condense_reported_savings_usd_total": condense_savings_usd_total,
            "configured": bool(p_in or p_out),
            "notes": (
                "proxy_cost counts billed upstream tokens only (0 on cache hits). "
                "prime_cost is unique warm-up calls when --prime-proxy-cache-unique is set."
            ),
        },
        "steady_state": {
            "row_count": len(steady_rows),
            "excluded_first_occurrence_count": max(0, len(rows) - len(steady_rows)),
            "cache_hit_count": steady_cache_hits,
            "cache_hit_rate": (
                round(steady_cache_hits / len(steady_rows), 4) if steady_rows else None
            ),
            "baseline_input_tokens": steady_baseline_input,
            "baseline_output_tokens": steady_baseline_output,
            "baseline_total_tokens": steady_baseline_total,
            "proxy_input_tokens_billed": steady_proxy_input_billed,
            "proxy_output_tokens_billed": steady_proxy_output_billed,
                "proxy_total_tokens_billed": steady_proxy_input_billed,
            "token_total_savings_pct_billed": _percent_savings(
                steady_baseline_total, steady_proxy_total_billed
            ),
            "output_savings_pct_billed": steady_output_savings_pct_billed,
            "baseline_cost": steady_baseline_cost,
            "proxy_cost": steady_proxy_cost,
            "cost_savings_usd": steady_cost_savings_usd,
            "cost_savings_pct": steady_cost_savings_pct,
            "notes": (
                "Steady-state excludes the first occurrence of each unique prompt fingerprint "
                "to approximate ongoing production traffic after warm-up."
            ),
        },
        "cache": _cache_metrics(rows),
        "quality": {
            "baseline_quality_pass_rate": baseline_quality,
            "proxy_quality_pass_rate": proxy_quality,
            "quality_delta": (
                round(proxy_quality - baseline_quality, 4)
                if baseline_quality is not None and proxy_quality is not None
                else None
            ),
            "agreement_rate_proxy_vs_baseline": quality_agreement,
            "proxy_pass_rate_on_cache_hits": proxy_hits_pass,
            "proxy_pass_rate_on_cache_misses": proxy_misses_pass,
        },
        "errors": {
            "baseline_error_count": sum(1 for row in rows if row["baseline"].get("error")),
            "proxy_error_count": sum(1 for row in rows if row["proxy"].get("error")),
            "baseline_non_2xx_count": sum(
                1
                for row in rows
                if row["baseline"].get("status_code") is not None
                and not 200 <= row["baseline"]["status_code"] < 300
            ),
            "proxy_non_2xx_count": sum(
                1
                for row in rows
                if row["proxy"].get("status_code") is not None
                and not 200 <= row["proxy"]["status_code"] < 300
            ),
        },
    }


def _agreement_rate(rows: list[dict[str, Any]]) -> float | None:
    """Fraction of rows where proxy answer == baseline answer (regardless of correctness)."""
    eligible = [
        row
        for row in rows
        if row["quality"]["baseline"]["available"]
        and row["quality"]["proxy"]["available"]
        and row["quality"]["baseline"]["observed"] is not None
        and row["quality"]["proxy"]["observed"] is not None
    ]
    if not eligible:
        return None
    matches = sum(
        1
        for row in eligible
        if row["quality"]["baseline"]["observed"] == row["quality"]["proxy"]["observed"]
    )
    return round(matches / len(eligible), 4)


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def _write_progress(path: Path, *, completed: int, total: int, case_id: str) -> None:
    """Small file for tailing live progress (completed/total) during long runs."""
    payload = {
        "completed": completed,
        "total": total,
        "case_id": case_id,
        "updated_at": datetime.now(UTC).isoformat(),
    }
    tmp = path.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2)
        handle.write("\n")
    tmp.replace(path)


def _fmt(value: Any, suffix: str = "", default: str = "n/a") -> str:
    if value is None:
        return default
    if isinstance(value, float):
        if abs(value) >= 1000:
            return f"{value:,.1f}{suffix}"
        if 0 < abs(value) < 0.01:
            return f"{value:.6f}".rstrip("0").rstrip(".") + suffix
        text = f"{value:.3f}".rstrip("0").rstrip(".")
        return f"{text}{suffix}"
    if isinstance(value, int):
        return f"{value:,}{suffix}"
    return f"{value}{suffix}"


def write_report_md(path: Path, report: dict[str, Any]) -> None:
    """Render a human-readable markdown summary from the structured report."""
    run_block = report["run"]
    lat = report["latency"]
    tok = report["tokens"]
    cost = report["cost"]
    steady = report.get("steady_state", {})
    cache = report["cache"]
    qual = report["quality"]
    err = report["errors"]

    label = run_block.get("preset_label") or Path(str(run_block.get("out_dir", ""))).name or "run"
    lines: list[str] = []
    lines.append(f"# Benchmark Report — {label}")
    lines.append("")

    # ---- Steady-state ----
    if steady:
        lines.append("## Steady-state (repeats only)")
        lines.append("")
        lines.append(
            f"- Rows analyzed: **{_fmt(steady.get('row_count'))}** "
            f"(excluded first occurrences: {_fmt(steady.get('excluded_first_occurrence_count'))})"
        )
        lines.append(
            f"- Cache hit rate: **{_fmt((steady.get('cache_hit_rate') or 0) * 100, '%')}** "
            f"({_fmt(steady.get('cache_hit_count'))}/{_fmt(steady.get('row_count'))})"
        )
        if cost.get("configured"):
            lines.append(
                f"- Cost saved vs baseline: **${_fmt(steady.get('cost_savings_usd'))} "
                f"({_fmt(steady.get('cost_savings_pct'), '%')})**"
            )
        lines.append(
            f"- Token total savings (billed): **{_fmt(steady.get('token_total_savings_pct_billed'), '%')}**"
        )
        lines.append(
            f"- Output token savings (billed, informational): **{_fmt(steady.get('output_savings_pct_billed'), '%')}**"
        )
        lines.append("")
    lines.append(
        f"_Cases: **{run_block['case_count']}**  •  "
        f"Started: {run_block['started_at']}  •  Completed: {run_block['completed_at']}_"
    )
    lines.append("")
    lines.append(
        f"- **Dataset:** `{run_block['dataset']}`\n"
        f"- **Baseline:** `{run_block['baseline_model']}` @ `{run_block['baseline_url']}`\n"
        f"- **Proxy:** `{run_block['proxy_model']}` @ `{run_block['proxy_url']}`"
    )
    lines.append("")

    # ---- Headlines ----
    lines.append("## Headlines")
    lines.append("")
    lines.append("| Metric | Value |")
    lines.append("|---|---|")
    lines.append(f"| Cache hit rate | **{_fmt((cache.get('cache_hit_rate') or 0) * 100, '%')}** ({cache.get('cache_hit_count', 0)}/{cache.get('cache_observed_count', 0)}) |")
    lines.append(f"| Latency p50 speedup | **{_fmt(lat.get('p50_speedup_factor'), 'x')}** ({_fmt(lat.get('p50_savings_pct'), '%')} faster) |")
    lines.append(f"| Token total savings | **{_fmt(tok['totals'].get('total_savings_pct'), '%')}** |")
    lines.append(f"| Output token savings (est., informational) | **{_fmt(tok['generation_savings'].get('completion_tokens_savings_pct_est'), '%')}** |")
    if cost.get("configured"):
        lines.append(f"| Cost saved | **${_fmt(cost.get('cost_savings_usd'))}** ({_fmt(cost.get('cost_savings_pct'), '%')}) |")
    lines.append(f"| Quality (baseline → proxy) | **{_fmt((qual.get('baseline_quality_pass_rate') or 0) * 100, '%')} → {_fmt((qual.get('proxy_quality_pass_rate') or 0) * 100, '%')}** |")
    lines.append(f"| Quality agreement (proxy=baseline) | **{_fmt((qual.get('agreement_rate_proxy_vs_baseline') or 0) * 100, '%')}** |")
    lines.append("")

    # ---- Latency ----
    lines.append("## Latency (ms)")
    lines.append("")
    lines.append("| Side | count | mean | p50 | p95 | p99 | min | max |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|---:|")
    for name, key in (("baseline", "baseline"), ("proxy", "proxy"), ("proxy (cache hits)", "proxy_cache_hit"), ("proxy (cache misses)", "proxy_cache_miss")):
        s = lat.get(key, {})
        lines.append(
            f"| {name} | {s.get('count', 0)} | {_fmt(s.get('mean'))} | {_fmt(s.get('p50'))} | "
            f"{_fmt(s.get('p95'))} | {_fmt(s.get('p99'))} | {_fmt(s.get('min'))} | {_fmt(s.get('max'))} |"
        )
    lines.append("")
    lines.append(
        f"- **Paired delta (proxy − baseline) per case:** p50 **{_fmt(lat.get('paired_delta_ms_p50'))} ms**, "
        f"mean **{_fmt(lat.get('paired_delta_ms_mean'))} ms**"
    )
    lines.append(
        f"- **p50 speedup:** **{_fmt(lat.get('p50_speedup_factor'), 'x')}**  •  "
        f"**p50 savings:** **{_fmt(lat.get('p50_savings_pct'), '%')}**"
    )
    lines.append("")

    # ---- Tokens ----
    totals = tok["totals"]
    gen = tok["generation_savings"]
    pd = tok["paired_delta"]
    prime = tok["prime_overhead"]
    lines.append("## Tokens")
    lines.append("")
    lines.append("| | Baseline | Proxy | Savings % |")
    lines.append("|---|---:|---:|---:|")
    lines.append(f"| Input | {_fmt(totals.get('baseline_input_tokens'))} | {_fmt(totals.get('proxy_input_tokens'))} | {_fmt(totals.get('input_savings_pct'), '%')} |")
    lines.append(f"| Output | {_fmt(totals.get('baseline_output_tokens'))} | {_fmt(totals.get('proxy_output_tokens'))} | {_fmt(totals.get('output_savings_pct'), '%')} |")
    lines.append(f"| Total (input-only) | {_fmt(totals.get('baseline_total_tokens'))} | {_fmt(totals.get('proxy_total_tokens'))} | {_fmt(totals.get('total_savings_pct'), '%')} |")
    lines.append(f"| Total (input-only, raw) | {_fmt(totals.get('baseline_total_tokens'))} | {_fmt(totals.get('proxy_total_tokens'))} | {_fmt(totals.get('total_savings_pct_raw'), '%')} |")
    lines.append("")
    lines.append("**Generation savings (cache effect, estimated):**")
    lines.append("")
    lines.append(f"- Cache hits: **{gen.get('cache_hit_count', 0)}**")
    lines.append(f"- Proxy completion tokens generated (cache misses only): **{_fmt(gen.get('proxy_generated_completion_tokens_excl_cache'))}**")
    lines.append(f"- Completion tokens avoided by cache (est.): **{_fmt(gen.get('completion_tokens_avoided_by_cache_est'))}**")
    lines.append(f"- **Estimated output-token savings (incl. priming):** **{_fmt(gen.get('completion_tokens_savings_pct_est'), '%')}**")
    lines.append("")
    lines.append(
        f"**Paired (proxy − baseline) input tokens per case:** "
        f"p50 {_fmt(pd.get('p50_proxy_minus_baseline'))}, "
        f"mean {_fmt(pd.get('mean_proxy_minus_baseline'))}, "
        f"min {_fmt(pd.get('min_proxy_minus_baseline'))}, "
        f"max {_fmt(pd.get('max_proxy_minus_baseline'))}"
    )
    if prime.get("prime_call_count"):
        lines.append("")
        lines.append(
            f"**Prime overhead (unmeasured proxy calls used to warm cache):** "
            f"calls={prime['prime_call_count']}, "
            f"tokens (in/out/total) = "
            f"{_fmt(prime.get('prime_input_tokens'))}/"
            f"{_fmt(prime.get('prime_output_tokens'))}/"
            f"{_fmt(prime.get('prime_total_tokens'))}, "
            f"latency total = {_fmt(prime.get('prime_latency_ms_total'))} ms"
        )
    lines.append("")

    # ---- Cost ----
    if cost.get("configured"):
        lines.append("## Cost (USD)")
        lines.append("")
        lines.append(
            f"_Pricing per 1K tokens — input: **${_fmt(cost.get('price_input_per_1k'))}**, "
            f"output: **${_fmt(cost.get('price_output_per_1k'))}**_"
        )
        lines.append("")
        lines.append("| | Cost |")
        lines.append("|---|---:|")
        lines.append(f"| Baseline (direct) | ${_fmt(cost.get('baseline_cost'))} |")
        lines.append(f"| Proxy (billed; 0 on cache hits) | ${_fmt(cost.get('proxy_cost'))} |")
        lines.append(f"| Proxy priming (unique only) | ${_fmt(cost.get('prime_cost'))} |")
        lines.append(f"| **Proxy total (billed + prime)** | **${_fmt(cost.get('proxy_cost_with_prime'))}** |")
        lines.append(f"| **Cost saved vs baseline** | **${_fmt(cost.get('cost_savings_usd'))} ({_fmt(cost.get('cost_savings_pct'), '%')})** |")
        header_savings = cost.get("condense_reported_savings_usd_total")
        if header_savings:
            lines.append(f"| Condense header savings (sum) | ${_fmt(header_savings)} |")
        lines.append("")
    else:
        lines.append("## Cost")
        lines.append("")
        lines.append("_Pricing not configured. Pass `--price-input-per-1k` and `--price-output-per-1k` to compute USD cost._")
        lines.append("")

    # ---- Quality ----
    lines.append("## Quality")
    lines.append("")
    lines.append("| Metric | Value |")
    lines.append("|---|---:|")
    lines.append(f"| Baseline pass rate | {_fmt((qual.get('baseline_quality_pass_rate') or 0) * 100, '%')} |")
    lines.append(f"| Proxy pass rate | {_fmt((qual.get('proxy_quality_pass_rate') or 0) * 100, '%')} |")
    lines.append(f"| Proxy − baseline | {_fmt((qual.get('quality_delta') or 0) * 100, ' pts')} |")
    lines.append(f"| Agreement (proxy answer == baseline answer) | {_fmt((qual.get('agreement_rate_proxy_vs_baseline') or 0) * 100, '%')} |")
    lines.append(f"| Proxy pass rate on cache hits | {_fmt(((qual.get('proxy_pass_rate_on_cache_hits') or 0)) * 100, '%')} |")
    lines.append(f"| Proxy pass rate on cache misses | {_fmt(((qual.get('proxy_pass_rate_on_cache_misses') or 0)) * 100, '%')} |")
    lines.append("")

    # ---- Cache ----
    lines.append("## Cache")
    lines.append("")
    lines.append(f"- Hit rate: **{_fmt((cache.get('cache_hit_rate') or 0) * 100, '%')}** ({cache.get('cache_hit_count', 0)}/{cache.get('cache_observed_count', 0)})")
    if cache.get("cache_types"):
        lines.append(f"- Types: {cache['cache_types']}")
    lines.append("")

    # ---- Errors ----
    lines.append("## Errors")
    lines.append("")
    lines.append(
        f"- baseline errors: {err.get('baseline_error_count', 0)}, "
        f"non-2xx: {err.get('baseline_non_2xx_count', 0)}"
    )
    lines.append(
        f"- proxy errors: {err.get('proxy_error_count', 0)}, "
        f"non-2xx: {err.get('proxy_non_2xx_count', 0)}"
    )
    lines.append("")

    lines.append("---")
    lines.append("")
    lines.append("_Methodology: paired baseline (direct) vs Condense proxy on the same prompts. "
                 "Token totals and token-savings headlines are computed from input (`prompt_tokens`) only. "
                 "Latency percentiles are computed "
                 "from per-request HTTP round-trip times. Output-token savings (est.) credits cache hits with avoiding "
                 "the baseline's completion tokens, then subtracts proxy generations on misses and any priming generations._")
    lines.append("")

    path.write_text("\n".join(lines), encoding="utf-8")


def _run_case_once(
    *,
    case: dict[str, Any],
    index: int,
    args: argparse.Namespace,
    retry_kw: dict[str, Any],
) -> dict[str, Any]:
    """Execute one benchmark case and return a full results row."""
    base_request = case["request"]
    baseline_request = _apply_model(base_request, args.baseline_model)
    proxy_request = _apply_model(base_request, args.proxy_model)

    prime_record: dict[str, Any] = {"attempted": False}
    with httpx.Client(timeout=args.timeout) as client:
        if args.prime_proxy_cache:
            prime = _post_json(client, args.proxy_url, proxy_request, args.authorization, **retry_kw)
            prime_record = {
                "attempted": True,
                "skipped": False,
                "fingerprint": _request_fingerprint(proxy_request),
                "status_code": prime.get("status_code"),
                "latency_ms": prime.get("latency_ms"),
                "prompt_tokens": prime.get("prompt_tokens"),
                "completion_tokens": prime.get("completion_tokens"),
                "total_tokens": prime.get("total_tokens"),
                "x_condense_headers": prime.get("x_condense_headers"),
                "error": prime.get("error"),
            }

        baseline = _post_json(
            client, args.baseline_url, baseline_request, args.authorization, **retry_kw
        )
        proxy = _post_json(client, args.proxy_url, proxy_request, args.authorization, **retry_kw)

    return {
        "id": case["id"],
        "index": index,
        "metadata": case["metadata"],
        "reference": case["reference"],
        "baseline_request": baseline_request,
        "proxy_request": proxy_request,
        "baseline": baseline,
        "proxy": proxy,
        "prime": prime_record,
        "quality": {
            "baseline": _quality_result(case["reference"], baseline["assistant_text"]),
            "proxy": _quality_result(case["reference"], proxy["assistant_text"]),
        },
    }


def run(args: argparse.Namespace) -> dict[str, Any]:
    started_at = datetime.now(UTC).isoformat()
    cases = [_normalize_case(row, idx) for idx, row in enumerate(_load_jsonl(args.dataset, args.limit))]
    if not cases:
        raise ValueError(f"No benchmark cases found in {args.dataset}")

    args.out_dir.mkdir(parents=True, exist_ok=True)
    results: list[dict[str, Any]] = []

    retry_kw = {
        "max_retries": int(getattr(args, "max_retries", 1) or 1),
        "retry_backoff_s": float(getattr(args, "retry_backoff_s", 1.0) or 1.0),
    }
    request_delay = float(getattr(args, "request_delay_s", 0.0) or 0.0)
    concurrency = max(1, int(getattr(args, "concurrency", 1) or 1))
    if concurrency > 1 and args.prime_proxy_cache_unique:
        raise SystemExit(
            "--prime-proxy-cache-unique is not supported with --concurrency > 1."
        )

    if concurrency == 1:
        for index, case in enumerate(cases, start=1):
            row = _run_case_once(case=case, index=index, args=args, retry_kw=retry_kw)
            results.append(row)
            proxy = row["proxy"]
            print(
                f"[{index}/{len(cases)}] {case['id']} "
                f"baseline={row['baseline']['status_code']} proxy={proxy['status_code']} "
                f"proxy_cache={proxy['x_condense_headers'].get('x-condense-cache-hit', 'n/a')}",
                flush=True,
            )
            _write_progress(
                args.out_dir / "progress.json",
                completed=index,
                total=len(cases),
                case_id=str(case["id"]),
            )
            if request_delay > 0 and index < len(cases):
                time.sleep(request_delay)
    else:
        rows_by_index: dict[int, dict[str, Any]] = {}
        completed = 0
        with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as executor:
            future_to_case = {
                executor.submit(
                    _run_case_once,
                    case=case,
                    index=index,
                    args=args,
                    retry_kw=retry_kw,
                ): (index, case)
                for index, case in enumerate(cases, start=1)
            }
            for future in concurrent.futures.as_completed(future_to_case):
                index, case = future_to_case[future]
                row = future.result()
                rows_by_index[index] = row
                completed += 1
                proxy = row["proxy"]
                print(
                    f"[{completed}/{len(cases)}] {case['id']} "
                    f"baseline={row['baseline']['status_code']} proxy={proxy['status_code']} "
                    f"proxy_cache={proxy['x_condense_headers'].get('x-condense-cache-hit', 'n/a')}",
                    flush=True,
                )
                _write_progress(
                    args.out_dir / "progress.json",
                    completed=completed,
                    total=len(cases),
                    case_id=str(case["id"]),
                )
        results = [rows_by_index[i] for i in sorted(rows_by_index.keys())]

    completed_at = datetime.now(UTC).isoformat()
    report = _build_report(results, args, started_at, completed_at)
    _write_jsonl(args.out_dir / "results.jsonl", results)
    _write_json(args.out_dir / "report.json", report)
    write_report_md(args.out_dir / "REPORT.md", report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run paired baseline vs Condense proxy benchmarks.",
    )
    parser.add_argument(
        "--dataset",
        type=Path,
        default=DEFAULT_DATASET,
        help="Path to JSONL benchmark cases.",
    )
    parser.add_argument(
        "--baseline-url",
        default=DEFAULT_BASELINE_URL,
        help="Direct upstream chat completions URL (OpenAI-compatible).",
    )
    parser.add_argument(
        "--proxy-url",
        default=DEFAULT_PROXY_URL,
        help="Condense proxy chat completions URL.",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=Path("benchmarks/runs") / f"run-{_utc_timestamp()}",
        help="Directory for results.jsonl and report.json.",
    )
    parser.add_argument(
        "--baseline-model",
        default=GEMINI_BASELINE_MODEL,
        help="Model for baseline requests.",
    )
    parser.add_argument(
        "--proxy-model",
        default=GEMINI_PROXY_MODEL,
        help="Model for proxy requests.",
    )
    parser.add_argument("--limit", type=int, default=None, help="Limit number of cases loaded.")
    parser.add_argument("--timeout", type=float, default=300.0, help="HTTP request timeout in seconds.")
    parser.add_argument(
        "--authorization",
        default=None,
        help="Authorization header value applied to both baseline and proxy requests.",
    )
    parser.add_argument(
        "--prime-proxy-cache",
        action="store_true",
        help="Send an unmeasured proxy request before each measured proxy request.",
    )
    parser.add_argument(
        "--prime-proxy-cache-unique",
        action="store_true",
        help="Prime each distinct prompt once (recommended for repeat-traffic benchmarks).",
    )
    parser.add_argument(
        "--price-input-per-1k",
        type=float,
        default=GEMINI_PRICE_INPUT_PER_1K,
        help="USD per 1K input tokens (0 disables cost block).",
    )
    parser.add_argument(
        "--price-output-per-1k",
        type=float,
        default=GEMINI_PRICE_OUTPUT_PER_1K,
        help="USD per 1K output tokens.",
    )
    parser.add_argument(
        "--preset-label",
        default=None,
        help="Human-friendly label for the preset/run (used in REPORT.md headers).",
    )
    parser.add_argument(
        "--max-retries",
        type=int,
        default=5,
        help="Retry transient HTTP/network failures and 429/5xx responses (default: 5).",
    )
    parser.add_argument(
        "--retry-backoff-s",
        type=float,
        default=1.0,
        help="Initial backoff seconds between retries (doubles each attempt).",
    )
    parser.add_argument(
        "--request-delay-s",
        type=float,
        default=0.0,
        help="Pause between benchmark cases to reduce rate-limit/DNS pressure.",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=1,
        help="Number of benchmark cases to execute concurrently (default: 1).",
    )
    args = parser.parse_args()
    if args.prime_proxy_cache and args.prime_proxy_cache_unique:
        raise SystemExit("Use only one of --prime-proxy-cache or --prime-proxy-cache-unique.")

    report = run(args)
    print(f"Wrote {args.out_dir / 'results.jsonl'}")
    print(f"Wrote {args.out_dir / 'report.json'}")
    print(f"Wrote {args.out_dir / 'REPORT.md'}")
    lat = report["latency"]
    cost = report["cost"]
    tok_totals = report["tokens"]["totals"]
    print(
        "Summary: "
        f"p50_baseline_ms={lat['baseline'].get('p50')} "
        f"p50_proxy_ms={lat['proxy'].get('p50')} "
        f"speedup={lat.get('p50_speedup_factor')}x "
        f"cache_hit_rate={report['cache'].get('cache_hit_rate')} "
        f"token_savings_pct={tok_totals.get('total_savings_pct')}% "
        f"cost_savings_usd={cost.get('cost_savings_usd') if cost.get('configured') else 'n/a'}"
    )


if __name__ == "__main__":
    main()
