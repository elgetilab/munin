#!/usr/bin/env python3
"""
Direct-to-vLLM streaming reproducer for the recurring hang seen
during multi-turn flakiness suite runs (2026-04-27/28).

Cuts the retrieval service out of the loop so we can isolate
whether the hang originates in vLLM (engine, scheduler, qwen3_coder
tool parser, qwen3 reasoning parser, CUDA kernel) versus in
retrieval (httpx streaming, tool execution, SSE plumbing).

Procedure:

1. Fetch the actual MCP tools schema from the deployed retrieval
   service so we reproduce the same tool surface the model decides
   against during real chat turns.
2. For each prompt size in --sizes, build a synthetic chat
   history that approximates a multi-turn LaTeX iteration: a
   chat persona system prompt, a long stretch of filler context
   roughly emulating accumulated tool_call args + tool_results,
   and a final user message asking for an action that should
   trigger compile_latex.
3. Stream vLLM directly (same body as chat_service._stream_vllm_once
   minus the retrieval-side framing). Measure:
     - prompt_tokens_estimate (4 chars per token rule-of-thumb)
     - time-to-first-token (TTFT)
     - inter-token gap max (the smoking gun for the hang — if a
       single decoded token takes >30s to arrive while previous
       tokens flowed fine, the engine is stalling mid-decode)
     - decode rate (tok/s)
     - total tokens emitted
     - whether the request completed or was killed by --max-wall.
4. Tabulate results so a hang-vs-pass pattern across sizes is
   immediately visible.

Usage:
    python scripts/repro_vllm_hang.py
    python scripts/repro_vllm_hang.py --sizes 5,15,30,45
    python scripts/repro_vllm_hang.py --max-wall 600 --max-tokens 16384

Each size gets one shot. To repeat for sampling variance, pass
--reps 3.

The script is read-only against vLLM (no state changes). It uses
default chat persona sampling so behaviour matches the failing
runs we observed.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from typing import Any, Optional

import httpx

VLLM_URL = os.getenv("VLLM_URL", "http://<cluster-lan-ip>:8000")
VLLM_MODEL = os.getenv("VLLM_MODEL_NAME", "qwen3.5-35b-a3b")
RETRIEVAL_BASE = os.getenv("RETRIEVAL_BASE", "http://127.0.0.1:8080")

# Match the chat persona's sampling (personas/chat.json params).
SAMPLING = {
    "temperature": 1.0,
    "top_p": 0.95,
    "top_k": 20,
    "min_p": 0.0,
    "presence_penalty": 1.5,
}


async def fetch_tools_schema(client: httpx.AsyncClient) -> list[dict]:
    """Pull the live MCP tools list from the deployed retrieval
    service so the synthetic prompt has the exact tools the model
    sees during real chats."""
    body = {
        "jsonrpc": "2.0",
        "id": "1",
        "method": "tools/list",
        "params": {},
    }
    r = await client.post(
        f"{RETRIEVAL_BASE}/mcp/messages",
        json=body,
        timeout=20.0,
    )
    r.raise_for_status()
    payload = r.json()
    raw_tools = (payload.get("result") or {}).get("tools") or []
    out: list[dict] = []
    for t in raw_tools:
        out.append({
            "type": "function",
            "function": {
                "name": t.get("name", ""),
                "description": t.get("description", ""),
                "parameters": t.get("inputSchema", {"type": "object"}),
            },
        })
    return out


def build_synthetic_messages(target_prompt_tokens: int) -> list[dict]:
    """Build a synthetic chat history whose total prompt size is
    roughly ``target_prompt_tokens`` tokens (4 chars per token).

    Shape mirrors what the model sees on a multi-turn LaTeX iteration:

        system prompt (chat persona, ~5 KB)
        user: "build me a Beamer deck"
        assistant: "[full ~9KB LaTeX source]"   <-- the bulk
        tool_result: "[compile output]"
        user: "make it red"
        assistant: "[modified ~9KB LaTeX]"      <-- more bulk
        tool_result: "[compile output]"
        user: "make it 16:9"                    <-- the actual ask

    The filler isn't real LaTeX (we don't need vLLM to do anything
    meaningful with it); it just needs to be plausible text the
    model has to attend to. Padding with realistic Beamer source
    so the qwen3 tokenizer doesn't compress it more than expected.
    """
    chars_target = target_prompt_tokens * 4

    system = (
        "You are Meitner, a versatile assistant on a research cluster. "
        "When the user asks you to write or modify LaTeX, you compile "
        "it via the compile_latex tool to verify it builds. For "
        "iteration use the artifact_id+diff mode where possible."
    )

    sample_latex = (
        "\\documentclass[aspectratio=43]{beamer}\n"
        "\\usetheme{Madrid}\n"
        "\\usepackage{amsmath, amssymb, physics}\n"
        "\\title{Quantum Mechanics: An Introduction}\n"
        "\\author{Generated}\n"
        "\\date{\\today}\n"
        "\\begin{document}\n"
        "\\frame{\\titlepage}\n"
        "\\begin{frame}{Schrodinger Equation}\n"
        "  The time-dependent equation:\n"
        "  \\[ i\\hbar \\frac{\\partial}{\\partial t} \\Psi(\\mathbf{r},t) "
        "= \\hat{H} \\Psi(\\mathbf{r},t) \\]\n"
        "  where $\\hat{H}$ is the Hamiltonian operator.\n"
        "\\end{frame}\n"
        "\\begin{frame}{Wave-particle duality}\n"
        "  De Broglie wavelength: $\\lambda = h/p$.\n"
        "  This unifies classical wave and particle pictures into a "
        "single quantum framework that depends on the momentum $p$.\n"
        "\\end{frame}\n"
        "\\end{document}\n"
    )

    # Inflate the LaTeX block to roughly half the target so the two
    # assistant turns combined dominate the prompt (mirrors the real
    # multi-turn shape).
    half_target = chars_target // 2
    blocks_needed = max(1, half_target // (len(sample_latex) * 2))
    inflated = (sample_latex + "\n") * max(1, blocks_needed)

    msgs: list[dict] = [
        {"role": "system", "content": system},
        {
            "role": "user",
            "content": "Write a minimal Beamer deck on quantum mechanics. Compile it.",
        },
        {
            "role": "assistant",
            "content": (
                "I've compiled the deck. Source (truncated for context):\n\n"
                + inflated
            ),
        },
        {
            "role": "user",
            "content": "Could you make the colour theme red?",
        },
        {
            "role": "assistant",
            "content": (
                "I've updated the colour theme. Modified source:\n\n"
                + inflated
            ),
        },
        {
            "role": "user",
            "content": (
                "Can you have the beamer presentation in 16:9 widescreen "
                "format?"
            ),
        },
    ]
    return msgs


def _approx_prompt_chars(messages: list[dict]) -> int:
    total = 0
    for m in messages:
        c = m.get("content")
        if isinstance(c, str):
            total += len(c)
        elif isinstance(c, list):
            for blk in c:
                if isinstance(blk, dict):
                    t = blk.get("text") or blk.get("content")
                    if isinstance(t, str):
                        total += len(t)
    return total


async def stream_one(
    client: httpx.AsyncClient,
    messages: list[dict],
    tools: list[dict],
    max_tokens: int,
    max_wall_s: float,
) -> dict:
    """One streaming vLLM call with tools enabled. Captures TTFT,
    inter-token gap stats, and whether we hit the wall-clock budget
    before completion (treated as a hang)."""
    body: dict[str, Any] = {
        "model": VLLM_MODEL,
        "messages": messages,
        "stream": True,
        "stream_options": {"include_usage": True},
        "max_tokens": max_tokens,
    }
    if tools:
        body["tools"] = tools
        body["tool_choice"] = "auto"
    body.update(SAMPLING)

    t0 = time.monotonic()
    ttft: Optional[float] = None
    last_token_t: Optional[float] = None
    max_gap = 0.0
    decoded_chars = 0
    decoded_chunks = 0
    finish_reason: Optional[str] = None
    usage: Optional[dict] = None
    error: Optional[str] = None
    hung = False

    try:
        async with client.stream(
            "POST",
            f"{VLLM_URL}/v1/chat/completions",
            json=body,
            timeout=httpx.Timeout(connect=10.0, read=max_wall_s, write=30.0, pool=30.0),
        ) as r:
            if r.status_code != 200:
                body_text = (await r.aread()).decode(errors="ignore")
                return {
                    "error": f"HTTP {r.status_code}: {body_text[:300]}",
                }
            buf = ""
            async for chunk in r.aiter_text():
                if not chunk:
                    continue
                buf += chunk
                while "\n\n" in buf:
                    raw, buf = buf.split("\n\n", 1)
                    for line in raw.split("\n"):
                        if not line.startswith("data: "):
                            continue
                        data_part = line[6:].strip()
                        if data_part == "[DONE]":
                            continue
                        try:
                            d = json.loads(data_part)
                        except json.JSONDecodeError:
                            continue
                        choices = d.get("choices") or []
                        if choices:
                            delta = choices[0].get("delta") or {}
                            content = delta.get("content") or ""
                            reasoning = delta.get("reasoning") or ""
                            tcs = delta.get("tool_calls") or []
                            piece_chars = (
                                len(content) + len(reasoning)
                                + sum(
                                    len((tc.get("function") or {}).get("arguments") or "")
                                    + len((tc.get("function") or {}).get("name") or "")
                                    for tc in tcs
                                )
                            )
                            if piece_chars > 0:
                                now = time.monotonic()
                                if ttft is None:
                                    ttft = now - t0
                                if last_token_t is not None:
                                    gap = now - last_token_t
                                    if gap > max_gap:
                                        max_gap = gap
                                last_token_t = now
                                decoded_chars += piece_chars
                                decoded_chunks += 1
                            fr = choices[0].get("finish_reason")
                            if fr is not None:
                                finish_reason = fr
                        u = d.get("usage")
                        if u is not None:
                            usage = u

                # Wall-clock kill switch: even though httpx has a
                # read timeout, that's per-chunk. We also enforce a
                # total wall budget so an SSE that keeps emitting
                # zero-payload heartbeats forever still exits.
                if time.monotonic() - t0 > max_wall_s:
                    hung = True
                    break
    except httpx.ReadTimeout:
        hung = True
        error = f"httpx ReadTimeout after {max_wall_s}s"
    except httpx.RequestError as e:
        error = f"{type(e).__name__}: {e}"
    except Exception as e:
        error = f"{type(e).__name__}: {e}"

    duration = time.monotonic() - t0
    decode_rate = (
        decoded_chars / max(0.001, duration - (ttft or 0)) if ttft else 0
    )
    return {
        "duration_s": round(duration, 1),
        "ttft_s": round(ttft, 2) if ttft is not None else None,
        "max_inter_token_gap_s": round(max_gap, 2),
        "decoded_chars": decoded_chars,
        "decoded_chunks": decoded_chunks,
        "decode_chars_per_sec": round(decode_rate, 1),
        "finish_reason": finish_reason,
        "usage": usage,
        "hung": hung,
        "error": error,
    }


def _format_row(label: str, prompt_chars: int, res: dict) -> str:
    status = "HUNG" if res.get("hung") else (
        "ERR" if res.get("error") else (res.get("finish_reason") or "?")
    )
    cells = [
        f"{label:>12s}",
        f"prompt={prompt_chars:>7d}c (~{prompt_chars // 4:>5d}t)",
        f"ttft={str(res.get('ttft_s', '-')):>6s}",
        f"max_gap={res.get('max_inter_token_gap_s', 0):>5.1f}s",
        f"decode={res.get('decode_chars_per_sec', 0):>6.1f}c/s",
        f"out_chars={res.get('decoded_chars', 0):>5d}",
        f"dur={res.get('duration_s', 0):>5.1f}s",
        f"status={status}",
    ]
    if res.get("error"):
        cells.append(f"err={res['error'][:60]}")
    return "  ".join(cells)


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--sizes",
        default="5,10,20,30,45",
        help=(
            "Comma-separated target prompt sizes in K-tokens. The "
            "5K case is the baseline (single-turn equivalent); 30K+ "
            "matches what we see on multi-turn LaTeX iteration."
        ),
    )
    parser.add_argument(
        "--reps",
        type=int,
        default=1,
        help="How many shots per size for sampling variance.",
    )
    parser.add_argument(
        "--max-wall",
        type=float,
        default=420.0,
        help=(
            "Wall-clock budget per shot in seconds. Beyond this the "
            "request is killed and reported as HUNG. Default mirrors "
            "the suite's HTTP_TIMEOUT (400s) plus a small margin."
        ),
    )
    parser.add_argument(
        "--max-tokens",
        type=int,
        default=16384,
        help="max_tokens cap (matches the deployed chat_service default).",
    )
    parser.add_argument(
        "--no-tools",
        action="store_true",
        help=(
            "Send the request without the MCP tools schema. Localises "
            "whether the prefill cliff is tied to the 38-tool definition "
            "blob (would shift) or purely to user-prompt length (cliff "
            "stays put)."
        ),
    )
    args = parser.parse_args()

    sizes = [int(s.strip()) for s in args.sizes.split(",") if s.strip()]
    print(f"vLLM:       {VLLM_URL}")
    print(f"Model:      {VLLM_MODEL}")
    print(f"Retrieval:  {RETRIEVAL_BASE}")
    print(f"Sizes:      {sizes} K target prompt tokens")
    print(f"Reps:       {args.reps}")
    print(f"max_tokens: {args.max_tokens}")
    print(f"max_wall:   {args.max_wall}s")
    print()

    if args.no_tools:
        tools: list[dict] = []
        print("Tools:      [] (--no-tools)")
    else:
        async with httpx.AsyncClient(timeout=20.0) as bootstrap:
            try:
                tools = await fetch_tools_schema(bootstrap)
            except Exception as e:
                print(f"[FATAL] could not fetch tools schema: {e}")
                return 2
        print(f"Tools:      {len(tools)} fetched from MCP")
    print()

    rows: list[str] = []
    async with httpx.AsyncClient(timeout=None) as client:
        for size_k in sizes:
            target_tokens = size_k * 1000
            messages = build_synthetic_messages(target_tokens)
            prompt_chars = _approx_prompt_chars(messages)
            for rep in range(args.reps):
                label = f"{size_k}K r{rep+1}"
                print(f">> {label} (prompt ~{prompt_chars} chars / "
                      f"~{prompt_chars // 4} tokens) ...", flush=True)
                res = await stream_one(
                    client, messages, tools,
                    max_tokens=args.max_tokens,
                    max_wall_s=args.max_wall,
                )
                line = _format_row(label, prompt_chars, res)
                print("  " + line)
                rows.append(line)

    print()
    print("=== SUMMARY ===")
    for line in rows:
        print(line)

    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
