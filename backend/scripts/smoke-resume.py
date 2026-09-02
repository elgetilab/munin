#!/usr/bin/env python3
"""Live smoke for SSE stream resume (KNOWN-BUGS #2).

WHY THIS EXISTS. Resume had never once succeeded in production. Every call in
an observed multi-day window returned 410 or 500, and the unit tests in
tests/test_stream_registry.py passed throughout because they only exercise the
in-process Stream/registry primitives and never touch the HTTP endpoint, which
is exactly where the `NameError: EventSourceResponse` lived. Green unit tests
meant "the building blocks work", not "resume works for users". This script
closes that gap by driving the real endpoint the way a browser does: start a
turn, abort mid-stream (the WiFi drop), reconnect with Last-Event-ID.

Run on the cluster head, against the retrieval container directly:

    /opt/munin/services/pipeline/venv/bin/python backend/scripts/smoke-resume.py

Creates one conversation under a throwaway address and deletes it afterwards.
Sends X-Munin-Egress=off so no paid web tier or rate-limited scholarly API is
touched. Exits 0 on success.
"""
from __future__ import annotations

import asyncio
import json
import sys

import httpx

BASE = "http://127.0.0.1:8080"
EMAIL = "resume-probe@munin.invalid"          # deliberately not a real user
HEADERS = {"X-Munin-Email": EMAIL, "X-Munin-Egress": "off"}
# Long enough to still be streaming when we cut the connection, and phrased to
# keep the model off tools so the event stream is plain content.
PROMPT = ("Explain, in at least 500 words and without using any tools, "
          "what a lipid bilayer phase transition is.")
DROP_AFTER_EVENTS = 12
RECONNECT_DELAY_S = 3.0


async def main() -> int:
    stream_id = last_id = conv_id = None
    before = after = 0
    terminal = False

    async with httpx.AsyncClient(timeout=90.0) as c:
        body = {"persona": "chat", "messages": [{"role": "user", "content": PROMPT}]}
        try:
            async with c.stream("POST", f"{BASE}/api/chat/completions",
                                json=body, headers=HEADERS) as r:
                print(f"  POST /api/chat/completions            -> HTTP {r.status_code}")
                if r.status_code != 200:
                    print("   ", (await r.aread())[:300].decode(errors="replace"))
                    return 1
                event = None
                async for line in r.aiter_lines():
                    if line.startswith("id:"):
                        last_id = line[3:].strip()
                    elif line.startswith("event:"):
                        event = line[6:].strip()
                    elif line.startswith("data:"):
                        if event == "conversation":
                            d = json.loads(line[5:].strip())
                            stream_id, conv_id = d.get("stream_id"), d.get("id")
                            print(f"  stream_id={stream_id}")
                        before += 1
                        if stream_id and last_id and before >= DROP_AFTER_EVENTS:
                            print(f"  dropping connection after {before} events, "
                                  f"Last-Event-ID={last_id}")
                            break
        except Exception as exc:                       # a mid-stream abort is the point
            print(f"  stream aborted: {type(exc).__name__}: {exc}")

        if not (stream_id and last_id):
            print("  FAIL: never captured stream_id / Last-Event-ID")
            return 1

        await asyncio.sleep(RECONNECT_DELAY_S)
        rh = dict(HEADERS, **{"Last-Event-ID": last_id})
        async with c.stream("GET", f"{BASE}/api/chat/completions/resume",
                            params={"stream_id": stream_id}, headers=rh) as r:
            print(f"  GET  /api/chat/completions/resume     -> HTTP {r.status_code}")
            if r.status_code != 200:
                print("   ", (await r.aread())[:300].decode(errors="replace"))
                return 1
            async for line in r.aiter_lines():
                if line.startswith("data:"):
                    after += 1
                elif line.startswith("event:") and line[6:].strip() in ("done", "error"):
                    terminal = True
        print(f"  resumed stream delivered {after} further events, terminal={terminal}")

        if conv_id:
            d = await c.delete(f"{BASE}/api/chats/{conv_id}", headers=HEADERS)
            print(f"  cleanup DELETE /api/chats/{{id}}         -> HTTP {d.status_code}")
            if d.status_code != 200:
                print("  WARNING: cleanup failed, remove the conversation by hand")

    ok = after > 0 and terminal
    print("  RESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
