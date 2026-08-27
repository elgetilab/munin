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
import collections
import asyncio
import json
import sys
import time
from typing import Any, Callable, Optional

import httpx

EVAL_USER = "eval-pong@munin.local"
HTTP_TIMEOUT = httpx.Timeout(600.0)

# Registered tool names, for the hallucinated-tool detector (router-era pong
# check b'). Available when run in-container (the documented way); if the
# import fails (run outside /app), the check degrades to "skip" (passes).
try:
    from mcp.schemas import MCP_TOOLS as _REGISTERED_TOOLS
    _REGISTERED_NAMES = set(_REGISTERED_TOOLS.keys())
except Exception:  # pragma: no cover - only outside the container
    _REGISTERED_NAMES = None


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
    def routed_profile(self) -> Optional[str]:
        """The router's up-front pick for the FIRST turn (A3 `routing` SSE).
        Replaces the retired `delegated` event."""
        for (_t, n, d) in self.events:
            if n == "routing":
                return d.get("profile")
        return None

    @property
    def tool_calls_emitted(self) -> list[str]:
        return [d.get("name") for (_t, n, d) in self.events if n == "tool_call"]

    @property
    def has_error_sse(self) -> bool:
        return any(n == "error" for (_t, n, _d) in self.events)

    # -- drop/overtooling instrumentation (the `lov` family) ---------------

    @property
    def total_prompt_tokens(self) -> int:
        """CUMULATIVE prompt tokens across every vLLM call in the turn.

        NOT a single prompt, and not what triggers a 400. `usage_tracker`
        folds each call additively (`slot[k] += ...`) and `aggregate_totals`
        sums across purposes, so the `done` event carries a turn total. An
        earlier version of this property was named `peak_prompt_tokens`, which
        was simply wrong.

        It is still the number that exposes the overtooling blowup: each tool
        call means another vLLM call that re-sends the whole accumulated
        conversation, so this grows roughly quadratically in tool calls. The
        iLOV reproducer spent 637,058 prompt tokens over 26 calls to answer one
        factual question, i.e. ~24.5k per call against a 65,536 window.
        """
        total = 0
        for (_t, n, d) in self.events:
            if n != "done":
                continue
            u = (d or {}).get("usage") or {}
            total = max(total, int(u.get("prompt_tokens") or 0))
        return total

    @property
    def routed_profiles(self) -> list[str]:
        """EVERY routing decision, not just the first. The same question has
        been observed routing to research, code and chat across attempts."""
        return [d.get("profile") for (_t, n, d) in self.events if n == "routing"]

    @property
    def interrupted_reason(self) -> Optional[str]:
        """Reason from a persisted `_(stream interrupted: ...)_` marker."""
        import re as _re
        for c in self.assistant_contents:
            m = _re.search(r"_\(stream interrupted: (.*?)\)_", c or "", _re.S)
            if m:
                return m.group(1).strip()
        return None

    @property
    def terminal_reason(self) -> Optional[str]:
        for (_t, n, d) in self.events:
            if n == "done":
                return (d or {}).get("terminal_reason")
        return None

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


def _all_tools_registered(r: "RunResult") -> bool:
    """b' (router-era): every emitted tool_call names a real registered tool
    (hallucinated-tool detector). Replaces the old allowlist-membership check.
    Degrades to pass if the registry isn't importable (run outside container)."""
    if _REGISTERED_NAMES is None:
        return True
    return all(name in _REGISTERED_NAMES for name in r.tool_calls_emitted)


def _pong_checks() -> list[tuple[str, Callable[[RunResult], bool]]]:
    # Router-era checks (A4). Was: assert delegate_to_persona -> Turing fires
    # and every tool_call is in the persona's allowlist. Both mechanisms were
    # deleted; (a') the router picks the code profile (or the turn routes to
    # code via an html game), (b') every tool_call is a real registered tool.
    return [
        ("artifacts <= 1",
         lambda r: len(r.artifacts) <= 1),
        ("every artifact is text/html",
         lambda r: all(a.get("content_type") == "text/html" for a in r.artifacts)),
        ("no phantom-URL warning",
         lambda r: not any("[backend warning]" in c for c in r.assistant_contents)),
        ("clarify-before-work (no artifact before a clarification)",
         lambda r: not r.artifact_before_clarification()),
        ("routed to code OR produced an html game",   # a'
         lambda r: r.routed_profile == "code"
         or any(a.get("content_type") == "text/html" for a in r.artifacts)),
        ("every tool_call is a real registered tool",  # b' (hallucinated-tool)
         _all_tools_registered),
        ("no error SSE",                               # d'
         lambda r: not r.has_error_sse),
        ("non-empty final assistant content",          # e'
         lambda r: any(c.strip() for c in r.assistant_contents)),
        ("no duplicated answer block  (fuzzy)",
         lambda r: not any(_has_duplicated_block(c) for c in r.assistant_contents)),
    ]


# Overtooling threshold. The 2026-07-27 Track D agentic arm averaged 8.6 tool
# calls per query and the 2026-08-26 re-run 6.9, so a well-behaved research turn
# lands in single digits. The reproducer below was observed emitting 20, 23 and
# 30 calls in a single turn. 15 sits above normal and well below pathological.
LOV_TOOL_BUDGET = 15

# The served window is 65,536 and the backend trims history to
# VLLM_MAX_CONTEXT (60,000). A turn whose prompt passes this is one or two tool
# results away from the 400 that kills it. Observed failures carried prompts of
# 65,542 / 66,391 / 66,416 / 69,703 / 70,787 tokens.
# Cumulative prompt tokens for the whole turn, NOT a single prompt (see
# RunResult.total_prompt_tokens). A healthy 2-4 call research turn spends well
# under 100k; the reproducer spent 637,058. This is a COST/overtooling guard.
# The per-call figure that actually trips vLLM's 400 is not exposed in the
# `done` event at all, which is why the production 400s (65,542 / 69,703 /
# 70,787 single prompts) cannot be reproduced from this harness alone.
LOV_PROMPT_BUDGET = 150_000


def _lov_checks() -> list[tuple[str, Callable[[RunResult], bool]]]:
    """Checks for the drop reproducer.

    Every one of these corresponds to something observed in production on this
    exact question (conversations 78877b40 / 743973b3 / aed5210c, 2026-08-27):
    routing that disagrees with itself across attempts, 20-30 tool calls in one
    turn, prompts past the 65,536 window, and turns that never persist an
    answer. Advisory like the rest of this harness: it reports pass-rates over
    N runs, it is not a gate.
    """
    return [
        ("stream completed (no interrupted marker)",
         lambda r: r.interrupted_reason is None),
        ("no error SSE",
         lambda r: not r.has_error_sse),
        ("non-empty final assistant content",
         lambda r: any(c.strip() for c in r.assistant_contents)),
        (f"tool calls <= {LOV_TOOL_BUDGET}  (overtooling guard)",
         lambda r: len(r.tool_calls_emitted) <= LOV_TOOL_BUDGET),
        (f"turn prompt tokens < {LOV_PROMPT_BUDGET:,}  (overtooling cost)",
         lambda r: 0 < r.total_prompt_tokens < LOV_PROMPT_BUDGET),
        ("routed to research  (a literature lookup, not code)",
         lambda r: r.routed_profile == "research"),
        ("routing stable within the turn",
         lambda r: len(set(p for p in r.routed_profiles if p)) <= 1),
        ("every tool_call is a real registered tool",
         _all_tools_registered),
        ("used the unified `search` tool at least once",
         lambda r: "search" in r.tool_calls_emitted),
        ("did not re-run web_search more than 3x",
         lambda r: r.tool_calls_emitted.count("web_search") <= 3),
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
    # --- drop reproducers -------------------------------------------------
    # The `lov` question is the one a user can reproduce on demand: it forced
    # overtooling, context overflow and a dropped stream on every attempt on
    # 2026-08-27. The others are drawn from turns that carried a
    # `_(stream interrupted: ...)_` marker in production; they are longer-form
    # and slower, so `lov` is the default reproducer.
    "lov": {
        "prompt": "what is the extinction coefficient of iLOV at 280nm?",
        "checks": _lov_checks,
    },
    "lov450": {
        "prompt": (
            "what is the extinction coefficient of iLOV at 280nm and 450nm?"
        ),
        "checks": _lov_checks,
    },
    "gpcr": {
        "prompt": (
            "my problem is that both documents show a different understanding "
            "of GPCRs than I have. I see GPCRs as allosteric machines, where "
            "ligand binding and G protein coupling are thermodynamically "
            "linked. Can you find literature that supports or contradicts this?"
        ),
        "checks": _lov_checks,
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
            _t0 = time.monotonic()
            res = await run_once(client, base, scenario)
            _elapsed = time.monotonic() - _t0
            if res.error:
                print(f"    ERROR: {res.error}")
            # Per-run MEASUREMENTS, not just verdicts. A pass/fail grid tells
            # you a check failed; for a drop investigation you need to see how
            # far past the line it went, and whether it is drifting run to run.
            # Printed for every scenario: cheap, and the numbers are the point.
            _tools = res.tool_calls_emitted
            _top = ", ".join(
                f"{n}x{c}" for n, c in collections.Counter(_tools).most_common(4)
            )
            print(
                "    profile=%-8s tools=%-3d turn_prompt_tokens=%-7d elapsed=%5.0fs%s"
                % (res.routed_profile or "-", len(_tools),
                   res.total_prompt_tokens, _elapsed,
                   f"  interrupted={res.interrupted_reason}"
                   if res.interrupted_reason else ""),
                flush=True,
            )
            if _top:
                print(f"      tools: {_top}", flush=True)
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
