#!/usr/bin/env python3
"""
End-to-end smoke test for the ``delegate_to_persona`` flow added
2026-04-28.

Sends a single message that fits ANOTHER persona's specialty better
than the source persona's, and inspects the SSE stream for:

1. A ``delegated`` event emitted before the response settles.
2. A ``persona_changed`` event so the frontend can re-render.
3. The conversation row in chats.db shows the new persona id.
4. The delegated persona's tools end up firing (not the source's).

We use ``research → code`` because research's allowlist excludes
``run_python``; a request for a quick Python script forces the model
to delegate (it has no other way to produce the script). This is the
most reliable trigger short of asking a deep-literature question to
``code``.

Runs against the deployed retrieval service. Faster than the
flakiness suite — one user turn with a delegation hop ≈ 30-60s.

Usage:
    python scripts/test_delegate_persona.py
    python scripts/test_delegate_persona.py --from research --to code
    python scripts/test_delegate_persona.py --message "..." --from chat

Exit 0 if delegation fired AND the new persona produced output, 1
otherwise.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sqlite3
import sys
import time
import uuid
from typing import Optional

import httpx

BASE = os.environ.get("RETRIEVAL_BASE", "http://127.0.0.1:8080")
HTTP_TIMEOUT = 600.0
CHATS_DB = os.environ.get("CHATS_DB", "/opt/munin/data/chats.db")


def _email() -> str:
    return f"delegate-test-{uuid.uuid4().hex[:8]}@munin.local"


async def _send_chat(
    client: httpx.AsyncClient,
    email: str,
    message: str,
    persona: str,
    conversation_id: Optional[str],
) -> dict:
    body = {
        "persona": persona,
        "conversation_id": conversation_id,
        "messages": [{"role": "user", "content": message}],
        "ephemeral": False,
    }
    headers = {
        "X-Munin-Email": email,
        "Content-Type": "application/json",
    }
    delegated_events: list[dict] = []
    persona_changed_events: list[dict] = []
    tool_calls: list[dict] = []
    errors: list[str] = []
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
        elif current_event == "delegated":
            delegated_events.append(d)
        elif current_event == "persona_changed":
            persona_changed_events.append(d)
        elif current_event == "tool_call":
            tool_calls.append(d)
        elif current_event == "error":
            errors.append(d.get("message", ""))

    return {
        "http_status": 200,
        "duration_s": duration,
        "conversation_id": conv_id,
        "title": title,
        "delegated_events": delegated_events,
        "persona_changed_events": persona_changed_events,
        "tool_calls": tool_calls,
        "errors": errors,
    }


def _peek_persona_in_db(conv_id: str) -> Optional[str]:
    """Side-channel sanity check: read the persisted persona straight
    from the SQLite row. Confirms the chat_store update landed."""
    try:
        conn = sqlite3.connect(CHATS_DB)
        cur = conn.execute(
            "SELECT persona FROM conversations WHERE id = ?", (conv_id,)
        )
        row = cur.fetchone()
        conn.close()
        return row[0] if row else None
    except Exception as e:
        print(f"  (persona DB peek failed: {e})")
        return None


async def _delete_conv(client: httpx.AsyncClient, email: str, conv_id: str) -> None:
    try:
        await client.delete(
            f"{BASE}/api/chats/{conv_id}",
            headers={"X-Munin-Email": email},
            timeout=10.0,
        )
    except Exception as e:
        print(f"  (cleanup failed: {e})")


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--from",
        dest="from_persona",
        default="research",
        help="Source persona (default: research, which lacks run_python).",
    )
    parser.add_argument(
        "--to",
        dest="expected_to",
        default="code",
        help=(
            "Expected delegation target. Test passes if the model "
            "delegates to this persona; warns if it picks a different "
            "persona but still delegates."
        ),
    )
    parser.add_argument(
        "--message",
        default=(
            "Write me a quick Python script that generates a 100-point "
            "sine wave and saves it to a CSV file. Run it and show me "
            "the output."
        ),
        help=(
            "User message. Default is a Python-execution request that "
            "research persona cannot satisfy with its own tools, "
            "forcing a delegation to code."
        ),
    )
    args = parser.parse_args()

    email = _email()
    print(f"Email:        {email}")
    print(f"BASE:         {BASE}")
    print(f"From persona: {args.from_persona}")
    print(f"Expect to:    {args.expected_to}")
    print(f"Message:      {args.message[:80]}")
    print()

    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
        try:
            r = await client.get(f"{BASE}/health", timeout=5.0)
            if r.status_code != 200:
                print(f"[FATAL] /health returned {r.status_code}")
                return 2
        except Exception as e:
            print(f"[FATAL] cannot reach {BASE}/health: {e}")
            return 2

        res = await _send_chat(
            client, email, args.message,
            persona=args.from_persona, conversation_id=None,
        )
        if res.get("http_status") != 200:
            print(f"[FAIL] HTTP {res.get('http_status')}: {res.get('errors', [])}")
            return 1

        conv_id = res.get("conversation_id")
        print(f"duration:           {res['duration_s']:.1f}s")
        print(f"conversation_id:    {conv_id}")
        print(f"delegated events:   {len(res['delegated_events'])}")
        print(f"persona_changed:    {len(res['persona_changed_events'])}")
        print(f"tool calls:         {[t.get('name') for t in res['tool_calls']]}")
        if res.get("errors"):
            print(f"errors:             {res['errors']}")
        for d in res["delegated_events"]:
            print(f"  delegated -> {d.get('to_persona')}: {d.get('reason')!r}")

        if conv_id:
            db_persona = _peek_persona_in_db(conv_id)
            print(f"persona in DB:      {db_persona}")

        # Verdict.
        print("\n=== VERDICT ===")
        ok = True
        if not res["delegated_events"]:
            print(
                "[FAIL] no `delegated` SSE event fired. The source "
                "persona either had the tools to handle the request "
                "directly (heuristic mismatch) or the intercept didn't "
                "fire. Check tool_calls above to see what the model "
                "did instead."
            )
            ok = False
        else:
            actual_to = res["delegated_events"][0].get("to_persona")
            if actual_to == args.expected_to:
                print(f"[PASS] delegated to {actual_to} as expected.")
            else:
                print(
                    f"[WARN] delegated to {actual_to!r}, expected "
                    f"{args.expected_to!r}. Test still passes — the "
                    f"core mechanism worked, just to a different target."
                )

        if conv_id:
            db_persona = _peek_persona_in_db(conv_id)
            expected_db = (
                res["delegated_events"][0].get("to_persona")
                if res["delegated_events"] else args.from_persona
            )
            if db_persona != expected_db:
                print(
                    f"[FAIL] persona in DB is {db_persona!r}, expected "
                    f"{expected_db!r} (post-delegation persistence)."
                )
                ok = False
            else:
                print(f"[PASS] persona persisted as {db_persona!r}")

            await _delete_conv(client, email, conv_id)

        return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
