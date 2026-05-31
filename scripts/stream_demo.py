#!/usr/bin/env python3
"""Watch Condense streaming like a chat UI — words appear one at a time."""

from __future__ import annotations

import argparse
import copy
import json
import re
import sys
import time
from pathlib import Path
from typing import Any

import httpx

PROXY_URL = "http://127.0.0.1:8090/v1/chat/completions"
DEFAULT_DATASET = Path("benchmarks/datasets/converted/profile_support_faq_high_repeat.jsonl")
TOKEN_RE = re.compile(r"\S+|\s+")
CONDENSE_HEADER_PREFIX = "x-condense-"
DEFAULT_WORD_DELAY_S = 0.35


def load_dataset_case(path: Path, index: int = 0) -> tuple[str, dict]:
    lines = [ln.strip() for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    row = json.loads(lines[index])
    return str(row.get("id", f"case_{index}")), copy.deepcopy(row["request"])


def parse_sse_payload(line: str) -> dict[str, Any] | None:
    if not line.startswith("data: "):
        return None
    payload = line[6:].strip()
    if payload == "[DONE]":
        return {"_done": True}
    try:
        return json.loads(payload)
    except json.JSONDecodeError:
        return None


def extract_delta_content(payload: dict[str, Any]) -> str | None:
    for choice in payload.get("choices") or []:
        delta = choice.get("delta") or {}
        content = delta.get("content")
        if isinstance(content, str) and content:
            return content
    return None


def tokenize_for_animation(text: str) -> list[str]:
    """Split into words and whitespace tokens for fixed-rate display."""
    tokens = TOKEN_RE.findall(text)
    if not tokens:
        return []
    return tokens


def describe_sse_event(payload: dict[str, Any]) -> str:
    if payload.get("_done"):
        return "stream finished ([DONE])"

    choices = payload.get("choices") or []
    parts: list[str] = []
    if payload.get("usage"):
        usage = payload["usage"]
        parts.append(
            "usage chunk "
            f"(prompt={usage.get('prompt_tokens')} "
            f"completion={usage.get('completion_tokens')})"
        )
    for choice in choices:
        delta = choice.get("delta") or {}
        if delta.get("role"):
            parts.append(f"role={delta['role']}")
        content = delta.get("content")
        if isinstance(content, str) and content:
            preview = content.replace("\n", "\\n")
            if len(preview) > 40:
                preview = preview[:37] + "..."
            parts.append(f"content (+{len(content)} chars): {preview!r}")
        if choice.get("finish_reason"):
            parts.append(f"finish_reason={choice['finish_reason']}")
    if not parts:
        parts.append("metadata chunk")
    return " | ".join(parts)


class ProofLog:
    """Live proof lines on stderr so stdout stays clean for the animation."""

    def __init__(self, enabled: bool):
        self.enabled = enabled
        self.sse_chunks = 0
        self.content_chunks = 0
        self.chars_from_proxy = 0

    def banner(self, message: str) -> None:
        if self.enabled:
            print(f"[condense] {message}", file=sys.stderr, flush=True)

    def header_block(self, headers: httpx.Headers, *, status_code: int, elapsed_ms: float) -> None:
        if not self.enabled:
            return
        print("[condense] --- proxy streaming proof ---", file=sys.stderr, flush=True)
        print(f"[condense] HTTP {status_code}  transport={headers.get('content-type')}", file=sys.stderr, flush=True)
        for key, value in sorted(headers.items()):
            if key.lower().startswith(CONDENSE_HEADER_PREFIX):
                print(f"[condense] {key}: {value}", file=sys.stderr, flush=True)
        mode = headers.get("x-condense-stream-mode")
        if mode == "live_upstream":
            print("[condense] path: routes.py -> StreamForwarder -> litellm.acompletion(stream=True)", file=sys.stderr, flush=True)
        elif mode == "cache_replay":
            print("[condense] path: routes.py -> replay_completion_as_sse() (cached response)", file=sys.stderr, flush=True)
        elif mode == "bypass_passthrough":
            print("[condense] path: routes.py -> direct upstream passthrough (circuit breaker)", file=sys.stderr, flush=True)
        print(f"[condense] headers received in {elapsed_ms:.0f}ms", file=sys.stderr, flush=True)
        print("[condense] --- live SSE events (one line per proxy chunk) ---", file=sys.stderr, flush=True)

    def sse_event(self, payload: dict[str, Any], *, elapsed_ms: float) -> None:
        if not self.enabled:
            return
        if payload.get("_done"):
            self.banner(
                f"SSE complete: {self.sse_chunks} events, "
                f"{self.content_chunks} content chunks, "
                f"{self.chars_from_proxy} chars from proxy"
            )
            return

        self.sse_chunks += 1
        content = extract_delta_content(payload)
        if content:
            self.content_chunks += 1
            self.chars_from_proxy += len(content)
        self.banner(f"SSE #{self.sse_chunks} @ {elapsed_ms:.0f}ms -> {describe_sse_event(payload)}")


class FixedRateWordPrinter:
    """Reveal text one word at a fixed pace, independent of SSE chunk sizes."""

    def __init__(self, word_delay_s: float):
        self.word_delay_s = max(0.0, word_delay_s)
        self.word_count = 0

    def animate(self, text: str) -> None:
        for token in tokenize_for_animation(text):
            if not token.strip():
                sys.stdout.write(token)
                sys.stdout.flush()
                continue
            sys.stdout.write(token)
            sys.stdout.flush()
            self.word_count += 1
            if self.word_delay_s > 0:
                time.sleep(self.word_delay_s)


def collect_streamed_text(
    resp: httpx.Response,
    proof_log: ProofLog,
    *,
    started: float,
) -> tuple[str, float | None]:
    """Read all SSE chunks from Condense, logging proof as they arrive."""
    parts: list[str] = []
    first_token_ms: float | None = None

    for line in resp.iter_lines():
        if not line:
            continue
        elapsed_ms = (time.perf_counter() - started) * 1000
        payload = parse_sse_payload(line)
        if payload is None:
            continue

        proof_log.sse_event(payload, elapsed_ms=elapsed_ms)
        if payload.get("_done"):
            break

        content = extract_delta_content(payload)
        if content is None:
            continue
        if first_token_ms is None:
            first_token_ms = elapsed_ms
        parts.append(content)

    return "".join(parts), first_token_ms


def stream_chat(
    request: dict,
    *,
    url: str = PROXY_URL,
    show_meta: bool = True,
    word_delay_s: float = DEFAULT_WORD_DELAY_S,
    proof: bool = True,
) -> None:
    body = copy.deepcopy(request)
    body["stream"] = True
    body.setdefault("stream_options", {"include_usage": True})

    started = time.perf_counter()
    proof_log = ProofLog(proof)

    if show_meta:
        user_msg = ""
        for msg in reversed(body.get("messages") or []):
            if msg.get("role") == "user":
                user_msg = str(msg.get("content") or "")
                break
        preview = user_msg.replace("\n", " ")
        if len(preview) > 120:
            preview = preview[:117] + "..."
        print(f"\nYou: {preview}\n")

    with httpx.stream("POST", url, json=body, timeout=120.0) as resp:
        header_ms = (time.perf_counter() - started) * 1000
        proof_log.header_block(resp.headers, status_code=resp.status_code, elapsed_ms=header_ms)

        if resp.status_code != 200:
            print(f"\n[error {resp.status_code}] {resp.read().decode()}", file=sys.stderr)
            return

        collected_text, first_token_ms = collect_streamed_text(resp, proof_log, started=started)

    collect_ms = (time.perf_counter() - started) * 1000
    word_tokens = [t for t in tokenize_for_animation(collected_text) if t.strip()]
    word_count = len(word_tokens)

    if proof:
        print(
            f"[animation] fixed {word_delay_s * 1000:.0f}ms per word | "
            f"{word_count} words | same pace for any answer length",
            file=sys.stderr,
            flush=True,
        )

    if show_meta:
        print("Assistant: ", end="", flush=True)

    animation_started = time.perf_counter()
    printer = FixedRateWordPrinter(word_delay_s)
    printer.animate(collected_text)

    animation_ms = (time.perf_counter() - animation_started) * 1000
    total_ms = (time.perf_counter() - started) * 1000
    print("\n")
    if show_meta:
        print(
            f"  proxy_first_token={first_token_ms:.0f}ms  "
            f"sse_collect={collect_ms:.0f}ms  "
            f"animation={animation_ms:.0f}ms  "
            f"total={total_ms:.0f}ms  "
            f"word_delay={word_delay_s * 1000:.0f}ms  "
            f"words={word_count}"
        )
        if proof:
            print("  (proof: Condense SSE above, animation: fixed-rate word replay below)")


def main() -> None:
    parser = argparse.ArgumentParser(description="Word-by-word Condense stream demo")
    parser.add_argument("--model", default="gemini/gemini-2.5-flash")
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--case-index", type=int, default=1, help="Row index in dataset JSONL")
    parser.add_argument(
        "--prompt",
        default="",
        help="Custom user prompt (overrides dataset case)",
    )
    parser.add_argument("--url", default=PROXY_URL)
    parser.add_argument(
        "--word-delay",
        type=float,
        default=DEFAULT_WORD_DELAY_S,
        help=f"Seconds to pause after each word (default: {DEFAULT_WORD_DELAY_S})",
    )
    parser.add_argument(
        "--slow",
        action="store_true",
        help="Extra slow animation (~0.5s per word)",
    )
    parser.add_argument(
        "--fast",
        action="store_true",
        help="Faster animation (~0.15s per word)",
    )
    parser.add_argument(
        "--instant",
        action="store_true",
        help="No artificial delay; print full answer immediately after SSE completes",
    )
    parser.add_argument(
        "--no-proof",
        action="store_true",
        help="Hide live Condense/SSE proof lines on stderr",
    )
    parser.add_argument(
        "--temperature",
        type=float,
        default=0.0,
        help="Request temperature (default: 0.0 for cache-friendly repeats)",
    )
    args = parser.parse_args()

    if args.instant:
        word_delay_s = 0.0
    elif args.slow:
        word_delay_s = 0.5
    elif args.fast:
        word_delay_s = 0.15
    else:
        word_delay_s = args.word_delay

    if args.prompt:
        request = {
            "model": args.model,
            "messages": [{"role": "user", "content": args.prompt}],
            "temperature": args.temperature,
        }
        case_id = "custom"
    else:
        case_id, request = load_dataset_case(args.dataset, args.case_index)
        request["model"] = args.model
        if args.temperature is not None:
            request["temperature"] = args.temperature

    print(f"Streaming case: {case_id}  model: {args.model}")
    if request.get("temperature", 0) > 0:
        print(
            "[note] temperature > 0 disables exact cache (non_deterministic: skip). "
            "Use --temperature 0 to test cache_hit=true on repeat.",
            file=sys.stderr,
        )
    stream_chat(request, url=args.url, word_delay_s=word_delay_s, proof=not args.no_proof)


if __name__ == "__main__":
    main()
