#!/usr/bin/env python3
# ==============================================================================
# MUNIN BACKEND - PARSER SWAP SMOKE TEST
# ==============================================================================
# Drives a small set of chat turns through the deployed retrieval service to
# validate that vLLM's tool-call parser correctly extracts tool calls into the
# structured `tool_calls` SSE stream. Built for the qwen3_coder -> qwen3_xml
# parser swap on 2026-05-05, but useful any time we change parser, vLLM
# version, or the model.
#
# The four scenarios cover the tool-call shapes most likely to expose a parser
# regression:
#   1. web_search                    single tool call, simple JSON args
#   2. paper_search                  single tool call, multi-query array arg
#   3. invoke_agent (orchestrator)   nested agent path - the original
#                                    "vanishing agent" bug
#   4. compile_latex                 long code-string argument plus a
#                                    reasoning trace
#
# Pass criteria for each turn:
#   - At least one `tool_call` SSE event fired, with the expected name
#   - No `error` SSE event surfaced
#   - The matching `tool_result` event landed
#   - The final assistant content is non-empty
#
# Usage:
#   ./scripts/smoke-parser-swap.py
#   RETRIEVAL_BASE=http://host:8080 ./scripts/smoke-parser-swap.py
#   SMOKE_EMAIL=alice@example.com ./scripts/smoke-parser-swap.py
#
# Exit 0 = all scenarios passed; non-zero = at least one failed.
# ==============================================================================

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from dataclasses import dataclass, field
from typing import Any, Optional

import httpx

BASE = os.environ.get("RETRIEVAL_BASE", "http://127.0.0.1:8080")
EMAIL = os.environ.get("SMOKE_EMAIL", "smoke-parser@munin.local")
HTTP_TIMEOUT = 400.0
MIN_ANSWER_CHARS = 60  # smoke test, not a behavioural correctness gate


# ---- SSE parsing -------------------------------------------------------------

def parse_sse(text: str) -> dict:
    """
    Collapse a full SSE response body into a structured dict. Trimmed
    version of stress-test.py::parse_sse - we only keep the fields the
    smoke assertions need.
    """
    events: dict[str, int] = {}
    tool_calls: list[dict] = []
    tool_results: list[dict] = []
    errors: list[str] = []
    content_chunks: list[str] = []
    current_event: Optional[str] = None

    for line in text.split("\n"):
        line = line.rstrip()
        if line.startswith("event: "):
            current_event = line[7:]
            events[current_event] = events.get(current_event, 0) + 1
            continue
        if line.startswith("data: "):
            try:
                d = json.loads(line[6:])
            except json.JSONDecodeError:
                continue
            if current_event == "token":
                content_chunks.append(d.get("content", ""))
            elif current_event == "tool_call":
                tool_calls.append(d)
            elif current_event == "tool_result":
                tool_results.append(d)
            elif current_event == "error":
                errors.append(d.get("message", ""))

    return {
        "events": events,
        "content": "".join(content_chunks),
        "tool_calls": tool_calls,
        "tool_results": tool_results,
        "errors": errors,
    }


async def send_chat(
    client: httpx.AsyncClient,
    message: str,
    persona: str = "chat",
    email: Optional[str] = None,
) -> dict:
    """Single-turn ephemeral chat call. Each smoke scenario uses ephemeral
    so we don't accrue rows in chats.db for the smoke email."""
    body = {
        "persona": persona,
        "conversation_id": None,
        "messages": [{"role": "user", "content": message}],
        "ephemeral": True,
    }
    headers = {
        "X-Munin-Email": email or EMAIL,
        "Content-Type": "application/json",
    }
    text_buf: list[str] = []
    async with client.stream(
        "POST",
        f"{BASE}/api/chat/completions",
        json=body,
        headers=headers,
        timeout=HTTP_TIMEOUT,
    ) as response:
        if response.status_code != 200:
            body_text = (await response.aread()).decode(errors="ignore")
            return {
                "http_status": response.status_code,
                "http_body": body_text,
                "events": {},
                "content": "",
                "tool_calls": [],
                "tool_results": [],
                "errors": [f"HTTP {response.status_code}: {body_text[:300]}"],
            }
        async for chunk in response.aiter_text():
            text_buf.append(chunk)
    out = parse_sse("".join(text_buf))
    out["http_status"] = 200
    return out


# ---- Test harness ------------------------------------------------------------

@dataclass
class SmokeOutcome:
    name: str
    passed: bool = False
    reason: str = ""
    duration_s: float = 0.0
    metrics: dict = field(default_factory=dict)


def _expect_tool(res: dict, expected: str, label: str) -> SmokeOutcome:
    """
    Shared assertion: the named tool fired, completed, and the model
    produced final prose. Returns a populated SmokeOutcome ready to print.
    """
    out = SmokeOutcome(name=label)
    out.metrics = {
        "tool_calls": [tc.get("name") for tc in res["tool_calls"]],
        "tool_results": [tr.get("name") for tr in res["tool_results"]],
        "content_chars": len(res["content"]),
        "events": res["events"],
    }

    if res.get("http_status") != 200:
        out.reason = f"http {res.get('http_status')}: {res.get('http_body', '')[:200]}"
        return out
    if res["errors"]:
        out.reason = f"stream error: {res['errors'][0]}"
        return out

    matching_calls = [tc for tc in res["tool_calls"] if tc.get("name") == expected]
    if not matching_calls:
        out.reason = (
            f"expected '{expected}' tool_call, got "
            f"{out.metrics['tool_calls']!r}. parser may be leaving "
            "tool calls in plain content (qwen3_coder regression shape)"
        )
        return out
    if not any(tc.get("name") == expected for tc in res["tool_results"]):
        out.reason = (
            f"'{expected}' tool_call fired but no matching tool_result; "
            "executor may have dropped the call"
        )
        return out

    # Args sanity: parser must produce a dict, not a bare string.
    first = matching_calls[0]
    args = first.get("arguments")
    if not isinstance(args, dict):
        out.reason = f"'{expected}' arguments not a dict: {args!r}"
        return out
    if not args:
        out.reason = (
            f"'{expected}' arguments dict is empty - parser may be losing "
            "params (qwen3_coder speculative-decode regression shape)"
        )
        return out

    if len(res["content"].strip()) < MIN_ANSWER_CHARS:
        out.reason = (
            f"final content too short ({len(res['content'])} chars); model "
            "may have stopped after tool_result without summarising"
        )
        return out

    out.passed = True
    return out


# ---- Scenarios ---------------------------------------------------------------

WEB_SEARCH_PROMPT = (
    "Use web_search to find the current population of Reykjavik. "
    "Cite the source URL."
)

PAPER_SEARCH_PROMPT = (
    "Use paper_search to find recent papers on dynamic nuclear polarisation "
    "in solid-state NMR. Pass an array of 3 varied query phrasings."
)

INVOKE_AGENT_PROMPT = (
    "Use the research_orchestrator agent (via invoke_agent) to give me a "
    "short overview of luthiers and explain why you delegated."
)

COMPILE_LATEX_PROMPT = (
    "Compile the following equation as a small standalone PDF using "
    "compile_latex: $E = mc^2$. Wrap it in a minimal article document "
    "and confirm it built."
)


async def scenario_web_search(client: httpx.AsyncClient) -> SmokeOutcome:
    res = await send_chat(client, WEB_SEARCH_PROMPT)
    return _expect_tool(res, "web_search", "web_search single-call")


async def scenario_paper_search(client: httpx.AsyncClient) -> SmokeOutcome:
    res = await send_chat(client, PAPER_SEARCH_PROMPT)
    return _expect_tool(res, "paper_search", "paper_search multi-query array")


async def scenario_invoke_agent(client: httpx.AsyncClient) -> SmokeOutcome:
    res = await send_chat(client, INVOKE_AGENT_PROMPT)
    return _expect_tool(res, "invoke_agent", "invoke_agent (research_orchestrator)")


async def scenario_compile_latex(client: httpx.AsyncClient) -> SmokeOutcome:
    res = await send_chat(client, COMPILE_LATEX_PROMPT)
    return _expect_tool(res, "compile_latex", "compile_latex long-string arg")


SCENARIOS = [
    scenario_web_search,
    scenario_paper_search,
    scenario_invoke_agent,
    scenario_compile_latex,
]


# ---- Runner ------------------------------------------------------------------

async def main() -> int:
    print(f"Smoke test against {BASE}  (email={EMAIL})\n")
    async with httpx.AsyncClient() as client:
        # Probe for the service first so we fail fast with a clear message
        # if the tunnel is down or vLLM is mid-restart.
        try:
            r = await client.get(f"{BASE}/api/status", timeout=10.0)
            r.raise_for_status()
        except Exception as exc:
            print(f"[ERROR] {BASE}/api/status not reachable: {exc!r}")
            return 2

        outcomes: list[SmokeOutcome] = []
        for fn in SCENARIOS:
            label = fn.__name__
            print(f"  [ .. ] {label} ...", end="", flush=True)
            t0 = time.monotonic()
            try:
                outcome = await fn(client)
            except Exception as exc:
                outcome = SmokeOutcome(
                    name=label, passed=False,
                    reason=f"raised {type(exc).__name__}: {exc}",
                )
            outcome.duration_s = time.monotonic() - t0
            outcomes.append(outcome)
            status = "PASS" if outcome.passed else "FAIL"
            print(f"\r  [{status}] {label} ({outcome.duration_s:5.1f}s)")
            if outcome.reason:
                print(f"         reason: {outcome.reason}")
            for k, v in outcome.metrics.items():
                print(f"         {k}: {v}")

    passed = sum(1 for o in outcomes if o.passed)
    total = len(outcomes)
    print(f"\n{passed}/{total} scenarios passed")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
