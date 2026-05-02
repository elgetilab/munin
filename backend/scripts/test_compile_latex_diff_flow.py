#!/usr/bin/env python3
"""
Targeted end-to-end test for the compile_latex(artifact_id, diff)
iteration flow added 2026-04-28.

Sends a 2-turn chat — build a tiny LaTeX deck, then "make it 16:9
widescreen" — and inspects each turn's tool_call arguments to
verify:

1. Turn 1 calls compile_latex with `source` (full LaTeX text).
2. Turn 2 calls compile_latex with `artifact_id` AND `diff` (NOT
   `source`). This is the architectural win — modify-and-recompile
   should cost ~150 emitted tokens of diff instead of ~3000 tokens
   of full source.

Reports:
- Per-turn duration, tool_calls list, decode token count from usage
  events, and the actual arguments structure for compile_latex calls.
- Pass/fail verdict so the script is suitable as a smoke test.

Runs against the deployed retrieval service at
http://127.0.0.1:8080. Faster than the full flakiness suite — one
build + one modify = ~30-90s under normal cluster load.

Usage:
    python scripts/test_compile_latex_diff_flow.py
    python scripts/test_compile_latex_diff_flow.py --modify "make the colour theme red"

Exit 0 if turn 2 used diff mode, 1 otherwise.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
import uuid
from typing import Any, Optional

import httpx

BASE = "http://127.0.0.1:8080"
HTTP_TIMEOUT = 600.0


def _email_for_run() -> str:
    return f"diff-flow-test-{uuid.uuid4().hex[:8]}@munin.local"


async def _send_chat(
    client: httpx.AsyncClient,
    email: str,
    message: str,
    conversation_id: Optional[str],
) -> dict:
    """One chat turn. Returns parsed SSE events including the full
    arguments payload for each tool_call."""
    body = {
        "persona": "chat",
        "conversation_id": conversation_id,
        "messages": [{"role": "user", "content": message}],
        "ephemeral": False,
    }
    headers = {
        "X-Munin-Email": email,
        "Content-Type": "application/json",
    }
    tool_calls: list[dict] = []
    tool_results: list[dict] = []
    artifacts: list[dict] = []
    errors: list[str] = []
    usage: Optional[dict] = None
    conv_id = conversation_id
    title: Optional[str] = None
    text_buf: list[str] = []
    current_event: Optional[str] = None

    t0 = time.monotonic()
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
                "duration_s": time.monotonic() - t0,
                "errors": [body_text[:300]],
            }
        async for chunk in response.aiter_text():
            text_buf.append(chunk)

    duration = time.monotonic() - t0
    raw = "".join(text_buf)
    for line in raw.split("\n"):
        line = line.rstrip()
        if line.startswith("event: "):
            current_event = line[7:]
            continue
        if not line.startswith("data: "):
            continue
        try:
            d = json.loads(line[6:])
        except json.JSONDecodeError:
            continue
        if current_event == "conversation":
            conv_id = d.get("id") or conv_id
            if d.get("title"):
                title = d["title"]
        elif current_event == "tool_call":
            tool_calls.append(d)
        elif current_event == "tool_result":
            tool_results.append(d)
        elif current_event == "artifact_created":
            artifacts.append(d)
        elif current_event == "error":
            errors.append(d.get("message", ""))
        elif current_event == "done":
            usage = d.get("usage")

    return {
        "http_status": 200,
        "duration_s": duration,
        "conversation_id": conv_id,
        "title": title,
        "tool_calls": tool_calls,
        "tool_results": tool_results,
        "artifacts": artifacts,
        "errors": errors,
        "usage": usage,
    }


async def _delete_conv(
    client: httpx.AsyncClient, email: str, conv_id: str
) -> None:
    try:
        await client.delete(
            f"{BASE}/api/chats/{conv_id}",
            headers={"X-Munin-Email": email},
            timeout=10.0,
        )
    except Exception as e:
        print(f"  (cleanup failed: {e})")


def _summarise_compile_calls(turn_idx: int, res: dict) -> dict:
    """Extract the compile_latex calls from a turn and classify each
    by mode (source / artifact_id / artifact_id+diff)."""
    summary = {
        "turn": turn_idx,
        "duration_s": res.get("duration_s"),
        "all_tool_names": [tc.get("name") for tc in res.get("tool_calls", [])],
        "compile_calls": [],
        "errors": res.get("errors", []),
        "completion_tokens": (res.get("usage") or {}).get("completion_tokens"),
    }
    for tc in res.get("tool_calls", []):
        if tc.get("name") != "compile_latex":
            continue
        args = tc.get("arguments") or {}
        mode = "unknown"
        details: dict[str, Any] = {}
        has_source = isinstance(args.get("source"), str) and args["source"].strip()
        has_artifact = isinstance(args.get("artifact_id"), str) and args["artifact_id"].strip()
        has_diff = isinstance(args.get("diff"), str) and args["diff"].strip()
        if has_source and not has_artifact:
            mode = "source"
            details["source_chars"] = len(args["source"])
        elif has_artifact and has_diff:
            mode = "artifact_id+diff"
            details["artifact_id"] = args["artifact_id"]
            details["diff_chars"] = len(args["diff"])
        elif has_artifact and not has_diff:
            mode = "artifact_id"
            details["artifact_id"] = args["artifact_id"]
        else:
            details["raw_keys"] = sorted(args.keys())
        summary["compile_calls"].append({"mode": mode, **details})
    return summary


def _print_summary(s: dict) -> None:
    print(f"\n--- Turn {s['turn']} ---")
    print(f"  duration: {s['duration_s']:.1f}s")
    print(f"  decode tokens: {s['completion_tokens']}")
    print(f"  tool calls: {s['all_tool_names']}")
    if s["errors"]:
        print(f"  ERRORS: {s['errors']}")
    for i, c in enumerate(s["compile_calls"], start=1):
        print(f"  compile_latex #{i}: mode={c['mode']}")
        for k, v in c.items():
            if k == "mode":
                continue
            print(f"    {k}: {v}")


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--build",
        default=(
            "Write a minimal LaTeX Beamer deck with two slides on quantum "
            "mechanics. Compile it."
        ),
        help="First-turn message that asks for a fresh LaTeX build.",
    )
    parser.add_argument(
        "--modify",
        default=(
            "Can you have the beamer presentation in 16:9 widescreen "
            "format?"
        ),
        help=(
            "Second-turn modify request — should trigger compile_latex "
            "with artifact_id + diff if the model uses the new flow."
        ),
    )
    args = parser.parse_args()

    email = _email_for_run()
    print(f"Email:   {email}")
    print(f"BASE:    {BASE}")
    print(f"Turn 1:  {args.build}")
    print(f"Turn 2:  {args.modify}")

    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
        # Health probe.
        try:
            r = await client.get(f"{BASE}/health", timeout=5.0)
            if r.status_code != 200:
                print(f"[FATAL] /health returned {r.status_code}")
                return 2
        except Exception as e:
            print(f"[FATAL] cannot reach {BASE}/health: {e}")
            return 2

        # Turn 1: build.
        print("\n>> Turn 1: build")
        t1 = await _send_chat(client, email, args.build, conversation_id=None)
        if t1.get("http_status") != 200:
            print(f"[FAIL] turn 1 HTTP {t1.get('http_status')}: {t1.get('http_body','')[:200]}")
            return 1
        s1 = _summarise_compile_calls(1, t1)
        _print_summary(s1)
        conv_id = t1.get("conversation_id")
        if not conv_id:
            print("[FAIL] turn 1 produced no conversation_id")
            return 1
        if not s1["compile_calls"]:
            print("[FAIL] turn 1 didn't call compile_latex at all")
            await _delete_conv(client, email, conv_id)
            return 1

        # Turn 2: modify.
        print("\n>> Turn 2: modify")
        t2 = await _send_chat(client, email, args.modify, conversation_id=conv_id)
        if t2.get("http_status") != 200:
            print(f"[FAIL] turn 2 HTTP {t2.get('http_status')}: {t2.get('http_body','')[:200]}")
            await _delete_conv(client, email, conv_id)
            return 1
        s2 = _summarise_compile_calls(2, t2)
        _print_summary(s2)

        # Cleanup.
        await _delete_conv(client, email, conv_id)

        # Verdict.
        print("\n=== VERDICT ===")
        if not s2["compile_calls"]:
            print(
                "[FAIL] turn 2 didn't call compile_latex. The model "
                "may have written prose only or hit the prose-action "
                "recovery path. Tool calls were: "
                f"{s2['all_tool_names']}"
            )
            return 1
        modes = [c["mode"] for c in s2["compile_calls"]]
        used_diff = any(m == "artifact_id+diff" for m in modes)
        used_artifact_only = any(m == "artifact_id" for m in modes)
        used_source = any(m == "source" for m in modes)
        if used_diff:
            diff_call = next(
                c for c in s2["compile_calls"] if c["mode"] == "artifact_id+diff"
            )
            source_emitted = sum(
                c.get("source_chars", 0)
                for c in s2["compile_calls"]
                if c["mode"] == "source"
            )
            print(
                f"[PASS] turn 2 used artifact_id+diff mode. "
                f"diff size: {diff_call['diff_chars']} chars. "
                f"Decode tokens: {s2['completion_tokens']}. "
                f"(source still emitted in any compile call: "
                f"{source_emitted} chars)"
            )
            return 0
        if used_artifact_only:
            print(
                "[PARTIAL] turn 2 used artifact_id (no diff). The "
                "model recompiled unchanged; the user wanted a "
                "modification. Treating as failure."
            )
            return 1
        if used_source:
            src_chars = sum(c.get("source_chars", 0) for c in s2["compile_calls"])
            print(
                f"[FAIL] turn 2 fell back to source= mode "
                f"({src_chars} chars emitted). Schema description "
                f"may not be teaching the diff flow strongly enough; "
                f"persona prompt nudge may be needed."
            )
            return 1
        print(f"[FAIL] turn 2 compile_latex called with unknown mode shape: {modes}")
        return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
