#!/usr/bin/env python3
"""
Behaviour eval harness (on-demand, metrics report).

Unit tests can't assert non-deterministic model behaviour. This drives
the REAL agentic loop against the live vLLM and reports per-property
pass-rates over N runs. It is NOT a unit test: it needs the running
service + GPU, is slow, and is advisory (never a hard gate).

Run INSIDE the retrieval container (it POSTs to the local, fully
initialised service so we don't have to re-init embedders/singletons):

    docker exec munin-retrieval python /app/evals/run_eval.py --scenario pong --runs 8

Flags: --scenario (default pong), --runs N (default 8), --base URL.

Each run: POST the scenario prompt as a dedicated eval user, parse the
SSE stream, answer one clarification if the model asks, fetch the
persisted conversation, evaluate the scenario's checks, then DELETE the
conversation so prod chats.db stays clean. Scenarios are data — add more
beside `pong` for future behaviour guards.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from typing import Any, Callable, Optional

import httpx

EVAL_USER = "eval-pong@munin.local"
HTTP_TIMEOUT = httpx.Timeout(600.0)


# ---------------------------------------------------------------------------
# Run result + SSE driver
# ---------------------------------------------------------------------------

class RunResult:
    def __init__(self) -> None:
        # (turn_index, event_name, data) in arrival order.
        self.events: list[tuple[int, str, dict]] = []
        self.assistant_contents: list[str] = []   # from persisted conversation
        self.error: Optional[str] = None
        self.conversation_id: Optional[str] = None

    # -- derived helpers --
    @property
    def artifacts(self) -> list[dict]:
        return [d for (_t, n, d) in self.events if n == "artifact_created"]

    @property
    def delegated_to_code(self) -> bool:
        return any(
            n == "delegated" and d.get("to_persona") == "code"
            for (_t, n, d) in self.events
        )

    def artifact_before_clarification(self) -> bool:
        """True if any artifact_created precedes a clarification in the SAME turn."""
        per_turn: dict[int, list[str]] = {}
        for (t, n, _d) in self.events:
            if n in ("artifact_created", "clarification"):
                per_turn.setdefault(t, []).append(n)
        for seq in per_turn.values():
            if "clarification" in seq:
                ci = seq.index("clarification")
                if "artifact_created" in seq[:ci]:
                    return True
        return False


async def _post_turn(
    client: httpx.AsyncClient, base: str, prompt: str,
    conversation_id: Optional[str], turn: int, result: RunResult,
) -> Optional[str]:
    """POST one user turn, stream SSE into result.events. Returns the
    conversation id (captured from the `conversation` event on turn 1)."""
    body: dict[str, Any] = {
        "messages": [{"role": "user", "content": prompt}],
        "persona": "chat",
    }
    if conversation_id:
        body["conversation_id"] = conversation_id
    headers = {"X-Munin-Email": EVAL_USER, "Content-Type": "application/json"}
    cid = conversation_id
    cur_event = ""
    async with client.stream(
        "POST", f"{base}/api/chat/completions", json=body, headers=headers,
    ) as resp:
        if resp.status_code != 200:
            await resp.aread()
            raise RuntimeError(f"chat POST returned {resp.status_code}")
        async for line in resp.aiter_lines():
            if line.startswith(":"):           # keepalive comment
                continue
            if line.startswith("event:"):
                cur_event = line[6:].strip()
            elif line.startswith("data:") and cur_event:
                raw = line[5:].strip()
                try:
                    data = json.loads(raw)
                except json.JSONDecodeError:
                    data = {"_raw": raw}
                result.events.append((turn, cur_event, data))
                if cur_event == "conversation" and data.get("id"):
                    cid = data["id"]
            elif line == "":
                cur_event = ""
    return cid


# ---------------------------------------------------------------------------
# Scenarios
# ---------------------------------------------------------------------------

def _has_duplicated_block(content: str) -> bool:
    """Fuzzy: a substantial paragraph (>80 chars) repeated within one message."""
    seen: set[str] = set()
    for para in content.split("\n\n"):
        p = para.strip()
        if len(p) <= 80:
            continue
        key = p[:120]
        if key in seen:
            return True
        seen.add(key)
    return False


def _pong_checks() -> list[tuple[str, Callable[[RunResult], bool]]]:
    return [
        ("artifacts <= 1",
         lambda r: len(r.artifacts) <= 1),
        ("every artifact is text/html",
         lambda r: all(a.get("content_type") == "text/html" for a in r.artifacts)),
        ("no phantom-URL warning",
         lambda r: not any("[backend warning]" in c for c in r.assistant_contents)),
        ("clarify-before-work (no artifact before a clarification)",
         lambda r: not r.artifact_before_clarification()),
        ("delegated->code OR produced an html game",
         lambda r: r.delegated_to_code
         or any(a.get("content_type") == "text/html" for a in r.artifacts)),
        ("no duplicated answer block  (fuzzy)",
         lambda r: not any(_has_duplicated_block(c) for c in r.assistant_contents)),
    ]


SCENARIOS: dict[str, dict] = {
    "pong": {
        "prompt": "Please code me a pong game",
        # Sent only if the model asks a clarification on turn 1.
        "clarify_answer": (
            "Single player versus the computer, in HTML/CSS/JavaScript so I "
            "can play it in the browser."
        ),
        "checks": _pong_checks,
    },
}


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

async def run_once(client: httpx.AsyncClient, base: str, scenario: dict) -> RunResult:
    r = RunResult()
    try:
        cid = await _post_turn(client, base, scenario["prompt"], None, 1, r)
        r.conversation_id = cid
        asked_clarification = any(n == "clarification" for (_t, n, _d) in r.events)
        if asked_clarification and scenario.get("clarify_answer") and cid:
            await _post_turn(client, base, scenario["clarify_answer"], cid, 2, r)
        # Fetch persisted conversation for final content (phantom warning +
        # duplicate-answer checks read the saved assistant messages).
        if cid:
            headers = {"X-Munin-Email": EVAL_USER}
            resp = await client.get(f"{base}/api/chats/{cid}", headers=headers)
            if resp.status_code == 200:
                conv = resp.json()
                r.assistant_contents = [
                    m.get("content") or ""
                    for m in (conv.get("messages") or [])
                    if m.get("role") == "assistant"
                ]
    except Exception as e:  # noqa: BLE001 - report, don't crash the suite
        r.error = f"{type(e).__name__}: {e}"
    finally:
        if r.conversation_id:
            try:
                await client.delete(
                    f"{base}/api/chats/{r.conversation_id}",
                    headers={"X-Munin-Email": EVAL_USER},
                )
            except Exception:
                pass  # cleanup is best-effort
    return r


async def main_async(scenario_name: str, runs: int, base: str) -> int:
    scenario = SCENARIOS.get(scenario_name)
    if scenario is None:
        print(f"unknown scenario {scenario_name!r}; have: {', '.join(SCENARIOS)}")
        return 2
    checks = scenario["checks"]()

    results: list[RunResult] = []
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
        for i in range(runs):
            print(f"  run {i + 1}/{runs} ...", flush=True)
            res = await run_once(client, base, scenario)
            if res.error:
                print(f"    ERROR: {res.error}")
            results.append(res)

    ok_runs = [r for r in results if r.error is None]
    errored = len(results) - len(ok_runs)

    print(f"\n{scenario_name}  ({len(ok_runs)}/{runs} runs completed"
          f"{f', {errored} errored' if errored else ''})")
    if not ok_runs:
        print("  no successful runs — check the service / vLLM.")
        return 1
    width = max(len(name) for name, _ in checks)
    for name, fn in checks:
        passed = sum(1 for r in ok_runs if fn(r))
        bar = "." * (width - len(name))
        print(f"  {name} {bar} {passed}/{len(ok_runs)}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Behaviour eval (metrics report).")
    ap.add_argument("--scenario", default="pong")
    ap.add_argument("--runs", type=int, default=8)
    ap.add_argument("--base", default="http://localhost:8080")
    args = ap.parse_args()
    return asyncio.run(main_async(args.scenario, args.runs, args.base))


if __name__ == "__main__":
    sys.exit(main())
