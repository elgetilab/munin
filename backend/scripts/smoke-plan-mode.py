#!/usr/bin/env python3
# ==============================================================================
# MUNIN BACKEND - PLAN MODE SMOKE TEST
# ==============================================================================
# Drives a curated set of chat turns against the deployed retrieval service to
# validate that Qwen3 actually exercises the plan-mode tools we ship in
# P2 #24 Phase 1 (set_plan + update_plan_item), and skips them on
# single-step requests. Designed for the "the prompt for the model is
# working" gating concern flagged during the 2026-05-28 design discussion.
#
# Phase 1 scenarios (always run):
#   1. multi-step research        expects   set_plan + plan_updated event
#                                           item count in 3..10
#   2. multi-step code            expects   set_plan AND >=1 update_plan_item
#                                           (proves progress-tracking works)
#   3. single-step weather        expects   NO set_plan (don't overplan
#                                           trivial single-tool requests)
#   4. single-step paper lookup   expects   NO set_plan (single web_search /
#                                           paper_lookup is one step)
#
# Phase 2 scenarios (run when SMOKE_PHASE2=1):
#   5. research-persona gate      expects   plan_approval_required SSE event
#                                           when delegate_to_persona fires
#                                           (research.json must declare
#                                           params.plan_approval)
#
# Pass criteria:
#   - For "expects set_plan" scenarios: at least one set_plan tool_call,
#     plan_updated SSE event fired, item count within bounds, final
#     assistant content non-empty.
#   - For "expects NO set_plan" scenarios: zero set_plan tool_calls, no
#     error event, final content non-empty.
#   - Progress-tracking scenario also requires >=1 update_plan_item call.
#   - Phase 2: plan_approval_required SSE event fired AT LEAST once on
#     the gated tool dispatch.
#
# Run criteria:
#   - vLLM serving (curl /api/status returns vllm.status == "running")
#   - retrieval container deployed with audit-24-plan-mode-phase1 branch
#   - personas in /opt/munin/personas updated with the TASK PLANNING block
#     (`deploy.sh personas` after edits to shared/personas/*.json)
#
# Usage:
#   ./scripts/smoke-plan-mode.py
#   RETRIEVAL_BASE=http://host:8080 ./scripts/smoke-plan-mode.py
#   SMOKE_EMAIL=alice@example.com ./scripts/smoke-plan-mode.py
#   SMOKE_PHASE2=1 ./scripts/smoke-plan-mode.py    # include Phase 2 gate
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
EMAIL = os.environ.get("SMOKE_EMAIL", "smoke-plan@munin.local")
RUN_PHASE2 = os.environ.get("SMOKE_PHASE2", "").strip() in ("1", "true", "yes")
HTTP_TIMEOUT = 400.0
MIN_ANSWER_CHARS = 40  # smoke gate, not a behavioural correctness check


# ----------------------------------------------------------------------
# SSE parsing — extended from smoke-parser-swap.py to also surface
# plan_updated and plan_approval_required events.
# ----------------------------------------------------------------------

def parse_sse(text: str) -> dict:
    events: dict[str, int] = {}
    tool_calls: list[dict] = []
    tool_results: list[dict] = []
    plan_events: list[dict] = []
    approval_events: list[dict] = []
    errors: list[str] = []
    content_chunks: list[str] = []
    conversation_id: Optional[str] = None
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
            elif current_event == "plan_updated":
                plan_events.append(d)
            elif current_event == "plan_approval_required":
                approval_events.append(d)
            elif current_event == "conversation":
                conversation_id = d.get("id") or conversation_id
            elif current_event == "error":
                errors.append(d.get("message", ""))

    return {
        "events": events,
        "content": "".join(content_chunks),
        "tool_calls": tool_calls,
        "tool_results": tool_results,
        "plan_events": plan_events,
        "approval_events": approval_events,
        "errors": errors,
        "conversation_id": conversation_id,
    }


async def send_chat(
    client: httpx.AsyncClient,
    message: str,
    *,
    persona: str = "chat",
    ephemeral: bool = False,
    conversation_id: Optional[str] = None,
    email: Optional[str] = None,
) -> dict:
    """Single-turn chat call. Defaults to persistent (ephemeral=False) so
    plan-mode state can persist; the Phase 2 gate REQUIRES a real
    conversation_id."""
    body: dict[str, Any] = {
        "persona": persona,
        "conversation_id": conversation_id,
        "messages": [{"role": "user", "content": message}],
        "ephemeral": ephemeral,
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
                "plan_events": [],
                "approval_events": [],
                "errors": [f"HTTP {response.status_code}: {body_text[:300]}"],
                "conversation_id": None,
            }
        async for chunk in response.aiter_text():
            text_buf.append(chunk)
    out = parse_sse("".join(text_buf))
    out["http_status"] = 200
    return out


# ----------------------------------------------------------------------
# Pass / fail dataclass + shared helpers
# ----------------------------------------------------------------------

@dataclass
class SmokeOutcome:
    name: str
    passed: bool = False
    reason: str = ""
    duration_s: float = 0.0
    metrics: dict = field(default_factory=dict)


def _basic_health(res: dict) -> Optional[str]:
    """HTTP + stream-error checks shared across every scenario.
    Returns the failure reason string or None on success."""
    if res.get("http_status") != 200:
        return f"http {res.get('http_status')}: {res.get('http_body', '')[:200]}"
    if res["errors"]:
        return f"stream error: {res['errors'][0]}"
    if len(res["content"].strip()) < MIN_ANSWER_CHARS:
        return (
            f"final content too short ({len(res['content'])} chars); model "
            "may have stalled after the plan without responding to the user"
        )
    return None


def _count_tool_calls(res: dict, name: str) -> int:
    return sum(1 for tc in res["tool_calls"] if tc.get("name") == name)


def _count_successful_tool_results(res: dict, name: str) -> int:
    """Tool RESULTS for ``name`` whose payload is a dict without a
    truthy ``error`` field. Counting calls is not enough: the
    persona-allowlist short-circuit returns a synthetic
    ``{"error": "The 'X' tool is not available..."}`` BEFORE the
    dispatcher runs — the model attempted the call but the
    backend rejected it. The 2026-05-29 bug (set_plan rejected
    because the infra-tool auto-inject was missing in personas.py)
    slipped through the smoke gate precisely because the test
    only checked tool_call presence, not tool_result success."""
    out = 0
    for tr in res["tool_results"]:
        if tr.get("name") != name:
            continue
        r = tr.get("result")
        if not isinstance(r, dict):
            continue
        if r.get("error"):
            continue
        out += 1
    return out


def _failing_tool_result_messages(res: dict, name: str) -> list[str]:
    """Surface the error messages from any failed tool_results for
    ``name`` so the test output explains WHY the call didn't land."""
    msgs: list[str] = []
    for tr in res["tool_results"]:
        if tr.get("name") != name:
            continue
        r = tr.get("result")
        if isinstance(r, dict) and r.get("error"):
            msg = r["error"]
            if isinstance(msg, str):
                msgs.append(msg[:160])
    return msgs


def _collect_metrics(res: dict) -> dict:
    return {
        "tool_calls": [tc.get("name") for tc in res["tool_calls"]],
        "set_plan_count": _count_tool_calls(res, "set_plan"),
        "set_plan_ok": _count_successful_tool_results(res, "set_plan"),
        "update_plan_item_count": _count_tool_calls(res, "update_plan_item"),
        "update_plan_item_ok": _count_successful_tool_results(res, "update_plan_item"),
        "plan_updated_events": len(res["plan_events"]),
        "approval_events": len(res["approval_events"]),
        "content_chars": len(res["content"]),
        "events": res["events"],
    }


# ----------------------------------------------------------------------
# Scenario 1 — multi-step research (expects set_plan)
# ----------------------------------------------------------------------

MULTI_STEP_RESEARCH_PROMPT = (
    "I'd like an overview of recent work on dynamic nuclear polarisation "
    "in solid-state NMR. Find me 3-5 representative papers, summarise the "
    "main findings, and tell me which subfield is moving fastest. Take "
    "your time and lay out the steps before you start so I can follow "
    "along."
)


async def scenario_multistep_research(client: httpx.AsyncClient) -> SmokeOutcome:
    res = await send_chat(client, MULTI_STEP_RESEARCH_PROMPT, persona="chat")
    out = SmokeOutcome(name="multistep research expects set_plan")
    out.metrics = _collect_metrics(res)
    fail = _basic_health(res)
    if fail:
        out.reason = fail
        return out
    set_plan_n = out.metrics["set_plan_count"]
    if set_plan_n == 0:
        out.reason = (
            "model did NOT call set_plan on a clear multi-step request — "
            "persona TASK PLANNING block may need strengthening, or a "
            "forced-tool retry (mirroring _force_clarification_retry) "
            "may be required. tool_calls seen: "
            f"{out.metrics['tool_calls']!r}"
        )
        return out
    if out.metrics["set_plan_ok"] == 0:
        out.reason = (
            "model called set_plan but every tool_result was an error — "
            "the executor rejected the call. Most likely: the persona's "
            "tool_allowlist doesn't include set_plan (auto-inject in "
            "personas.tool_allowlist), or the dispatcher errored "
            "(check tool_result payload). Errors observed: "
            f"{_failing_tool_result_messages(res, 'set_plan')!r}"
        )
        return out
    if out.metrics["plan_updated_events"] == 0:
        out.reason = (
            "set_plan executed successfully but plan_updated SSE event did "
            "not fire — dispatcher may have failed to emit; check "
            "current_sse_emitter is bound in the per-tool scope"
        )
        return out
    first_plan = res["plan_events"][0]
    item_count = len(first_plan.get("items") or [])
    if not (3 <= item_count <= 10):
        out.reason = (
            f"plan had {item_count} items; expected 3-10 for a multi-step "
            "research request. Tighten the persona prompt's wording on "
            "item granularity if the model is over/under-decomposing"
        )
        return out
    out.passed = True
    return out


# ----------------------------------------------------------------------
# Scenario 2 — multi-step code (expects set_plan AND update_plan_item)
# ----------------------------------------------------------------------

MULTI_STEP_CODE_PROMPT = (
    "Help me refactor this Python script to add type hints AND write "
    "unit tests for it. Walk through it step by step, and as you "
    "complete each step let me see your progress.\n\n"
    "```python\n"
    "def parse_log(path):\n"
    "    out = []\n"
    "    with open(path) as f:\n"
    "        for line in f:\n"
    "            if 'ERROR' in line:\n"
    "                out.append(line.strip())\n"
    "    return out\n"
    "```"
)


async def scenario_multistep_code_progress(client: httpx.AsyncClient) -> SmokeOutcome:
    res = await send_chat(client, MULTI_STEP_CODE_PROMPT, persona="code")
    out = SmokeOutcome(name="multistep code expects set_plan + update_plan_item")
    out.metrics = _collect_metrics(res)
    fail = _basic_health(res)
    if fail:
        out.reason = fail
        return out
    if out.metrics["set_plan_count"] == 0:
        out.reason = "model did not call set_plan on a clear multi-step task"
        return out
    if out.metrics["set_plan_ok"] == 0:
        out.reason = (
            "model called set_plan but executor rejected every call. "
            f"errors: {_failing_tool_result_messages(res, 'set_plan')!r}"
        )
        return out
    if out.metrics["update_plan_item_count"] == 0:
        out.reason = (
            "model called set_plan but never update_plan_item — progress "
            "tracking is broken or the persona prompt isn't strong enough on "
            "the 'flip status as you work' instruction. tool_calls: "
            f"{out.metrics['tool_calls']!r}"
        )
        return out
    if out.metrics["update_plan_item_ok"] == 0:
        out.reason = (
            "update_plan_item called but every result was an error. "
            f"errors: {_failing_tool_result_messages(res, 'update_plan_item')!r}"
        )
        return out
    if out.metrics["plan_updated_events"] == 0:
        out.reason = (
            "tools ran but plan_updated SSE never fired — dispatcher emit "
            "broken or current_sse_emitter not bound in the per-tool scope"
        )
        return out
    out.passed = True
    return out


# ----------------------------------------------------------------------
# Scenario 3 — single-step weather (expects NO set_plan)
# ----------------------------------------------------------------------

SINGLE_STEP_WEATHER_PROMPT = (
    "What's the weather in Berlin right now?"
)


async def scenario_singlestep_weather(client: httpx.AsyncClient) -> SmokeOutcome:
    res = await send_chat(client, SINGLE_STEP_WEATHER_PROMPT, persona="chat")
    out = SmokeOutcome(name="single-step weather expects no set_plan")
    out.metrics = _collect_metrics(res)
    fail = _basic_health(res)
    if fail:
        out.reason = fail
        return out
    if out.metrics["set_plan_count"] > 0:
        out.reason = (
            f"model OVER-PLANNED a single-step request ({out.metrics['set_plan_count']} "
            "set_plan calls). Single web_search or one-tool questions should "
            "NOT trigger set_plan — tighten the persona's 'DO NOT USE for "
            "one-step requests' guidance"
        )
        return out
    out.passed = True
    return out


# ----------------------------------------------------------------------
# Scenario 4 — single-step paper lookup (expects NO set_plan)
# ----------------------------------------------------------------------

SINGLE_STEP_LOOKUP_PROMPT = (
    "Look up the paper with DOI 10.1126/science.aaa1934 and tell me the title."
)


async def scenario_singlestep_lookup(client: httpx.AsyncClient) -> SmokeOutcome:
    res = await send_chat(client, SINGLE_STEP_LOOKUP_PROMPT, persona="chat")
    out = SmokeOutcome(name="single-step paper lookup expects no set_plan")
    out.metrics = _collect_metrics(res)
    fail = _basic_health(res)
    if fail:
        out.reason = fail
        return out
    if out.metrics["set_plan_count"] > 0:
        out.reason = (
            "model called set_plan for a single paper_lookup — should be a "
            "one-step request"
        )
        return out
    out.passed = True
    return out


# ----------------------------------------------------------------------
# Scenario 5 — Phase 2 gate (research persona + delegate_to_persona)
# ----------------------------------------------------------------------

PHASE2_GATE_PROMPT = (
    "I need a polished short brief on the kinetics of polymer "
    "crystallisation from melt. WORKFLOW (must follow exactly): "
    "(1) call set_plan with the steps; "
    "(2) optionally call deep_research to gather material; "
    "(3) you MUST then call delegate_to_persona with persona_id='chat' "
    "and reason='write the polished brief' so the writing-capable "
    "persona produces the final prose. Do NOT write the final brief "
    "yourself — the whole point is that you delegate the writing. "
    "This is the rule even if you think you could write it; we are "
    "testing the delegation flow."
)


async def scenario_phase2_gate(client: httpx.AsyncClient) -> SmokeOutcome:
    """Requires Phase 2 deployed AND research.json's params.plan_approval
    populated with ['delegate_to_persona', ...]. Without those, the gate
    never fires and the test correctly reports failure."""
    res = await send_chat(client, PHASE2_GATE_PROMPT, persona="research")
    out = SmokeOutcome(name="phase2 gate fires on research persona")
    out.metrics = _collect_metrics(res)
    fail = _basic_health(res)
    # The Phase 2 short-circuit path may leave content empty (the model
    # sees an "awaiting_user_approval" tool_result and stops); accept a
    # short or empty content as long as the approval event fired.
    if res.get("http_status") != 200:
        out.reason = f"http {res.get('http_status')}: {res.get('http_body', '')[:200]}"
        return out
    if res["errors"]:
        out.reason = f"stream error: {res['errors'][0]}"
        return out
    if out.metrics["set_plan_count"] == 0:
        out.reason = (
            "research persona didn't call set_plan despite expensive work — "
            "if research.json's TASK PLANNING block instructs 'always set_plan "
            "before delegate_to_persona', this is a model-side failure"
        )
        return out
    if out.metrics["set_plan_ok"] == 0:
        out.reason = (
            "research persona called set_plan but every result was an error "
            "— executor rejection (check persona allowlist auto-inject in "
            "personas.tool_allowlist). errors: "
            f"{_failing_tool_result_messages(res, 'set_plan')!r}"
        )
        return out
    if out.metrics["approval_events"] == 0:
        out.reason = (
            "no plan_approval_required SSE event landed. Likely causes: "
            "(a) research.json's params.plan_approval not deployed; "
            "(b) preToolUse hook not registered in hooks/plan_approval.py; "
            "(c) model never actually tried to call delegate_to_persona "
            "(check tool_calls list)"
        )
        return out
    out.passed = True
    return out


# ----------------------------------------------------------------------
# Runner
# ----------------------------------------------------------------------

PHASE1_SCENARIOS = [
    scenario_multistep_research,
    scenario_multistep_code_progress,
    scenario_singlestep_weather,
    scenario_singlestep_lookup,
]

PHASE2_SCENARIOS = [
    scenario_phase2_gate,
]


async def main() -> int:
    print(f"Plan-mode smoke test against {BASE}  (email={EMAIL})")
    print(f"Phase 2 scenarios: {'ENABLED' if RUN_PHASE2 else 'skipped (set SMOKE_PHASE2=1)'}\n")
    async with httpx.AsyncClient() as client:
        try:
            r = await client.get(f"{BASE}/api/status", timeout=10.0)
            r.raise_for_status()
            status = r.json()
            if status.get("vllm", {}).get("status") != "running":
                print(f"[ERROR] vllm not running: {status.get('vllm')}")
                return 2
            if status.get("maintenance", {}).get("active"):
                print(f"[ERROR] maintenance mode is on; chat will be rejected")
                return 2
        except Exception as exc:
            print(f"[ERROR] {BASE}/api/status not reachable: {exc!r}")
            return 2

        scenarios = list(PHASE1_SCENARIOS)
        if RUN_PHASE2:
            scenarios.extend(PHASE2_SCENARIOS)

        outcomes: list[SmokeOutcome] = []
        for fn in scenarios:
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
            status_str = "PASS" if outcome.passed else "FAIL"
            print(f"\r  [{status_str}] {label} ({outcome.duration_s:5.1f}s)")
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
