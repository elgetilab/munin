#!/usr/bin/env python3
"""
Flakiness suite for Munin backend components.

Runs synthetic multi-turn conversation scenarios against a deployed
retrieval service multiple times each, so non-deterministic regressions
(model sampling variance, tool-pick bias, streaming races) show up as
``FLAKY`` in the summary instead of being missed by a single-pass
stress-test run.

Scope (shipped features that have historically been flaky or have
multi-turn behavior single-turn tests can't catch):

1. §14 `ask_clarification` full Q/A loop — ambiguous prompt → card →
   synthetic user answer → concrete follow-up work. Covers both the
   organic tool-call path and the prose-detection + forced-retry
   fallback.
2. §18 `compile_latex` iteration — model writes LaTeX, compiles,
   ships .tex + .pdf artifacts.
3. §22 Artifact lifecycle — create → update → version bump.
4. §21 Projects scoping — project instructions injected into the
   system prompt influence the next turn's answer.
5. §9 Memory across conversations — remember in conv A → recall in
   conv B (new conversation, new kernel).
6. §5 Vision — upload image, ask about it.
7. §25 Profile injection — per-user profile reaches the system prompt.
8. Agentic composition — chain paper_lookup / compare_papers into
   create_artifact on a single user turn.

Each scenario has one or more **variants** (different prompts/flows
that exercise the same feature) and each variant runs N reps (default
5). A scenario passes iff every rep of every variant passes the
per-turn assertions. Anything less is reported as FLAKY (some reps
passed) or FAIL (no reps passed).

Usage:
    python scripts/flakiness-suite.py                  # all scenarios, 5 reps
    python scripts/flakiness-suite.py --reps 3
    python scripts/flakiness-suite.py --only clarification
    python scripts/flakiness-suite.py --verbose        # full dump on failure

Shares the BASE/HTTP_TIMEOUT/send_chat helpers with scripts/stress-test.py
via importlib (stress-test.py has a dash in the filename so it can't be
imported normally).
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import importlib.util
import json
import os
import struct
import sys
import time
import traceback
import uuid
import zlib
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional

import httpx

# ----------------------------------------------------------------------------
# Import shared helpers from stress-test.py (which has a dash in the name)
# ----------------------------------------------------------------------------

_STRESS_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "stress-test.py"
)
_stress_spec = importlib.util.spec_from_file_location("stress_test", _STRESS_PATH)
assert _stress_spec is not None and _stress_spec.loader is not None
_stress = importlib.util.module_from_spec(_stress_spec)
# dataclass uses sys.modules[cls.__module__].__dict__ for lazy type
# resolution, so we need to register the module BEFORE executing it.
sys.modules["stress_test"] = _stress
_stress_spec.loader.exec_module(_stress)

BASE: str = _stress.BASE
HTTP_TIMEOUT = _stress.HTTP_TIMEOUT
send_chat = _stress.send_chat
parse_sse = _stress.parse_sse
_mcp_call = _stress._mcp_call
_delete_chat = _stress._delete_chat
make_solid_png = _stress.make_solid_png
png_to_data_url = _stress.png_to_data_url


# ----------------------------------------------------------------------------
# Result / scenario types
# ----------------------------------------------------------------------------


@dataclass
class TurnOutcome:
    """Result of asserting on one turn of a rep."""
    passed: bool
    reason: str = ""
    # Tool call names surfaced on this turn, for post-mortem.
    tool_calls: list[str] = field(default_factory=list)
    clarifications: int = 0
    content_preview: str = ""


@dataclass
class RepOutcome:
    """Result of running one full rep of one variant."""
    variant_label: str
    rep_index: int
    passed: bool
    turn_outcomes: list[TurnOutcome] = field(default_factory=list)
    duration_s: float = 0.0
    failure_turn: Optional[int] = None
    failure_reason: str = ""


@dataclass
class ScenarioOutcome:
    name: str
    reps: list[RepOutcome] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.reps)

    @property
    def passed(self) -> int:
        return sum(1 for r in self.reps if r.passed)

    @property
    def failed(self) -> int:
        return self.total - self.passed

    @property
    def status(self) -> str:
        if self.total == 0:
            return "SKIP"
        if self.passed == self.total:
            return "PASS"
        if self.passed == 0:
            return "FAIL"
        return "FLAKY"


# An assertion callback takes the parsed SSE response dict and a mutable
# state dict (per-rep) and returns a TurnOutcome.
Assertion = Callable[[dict, dict], TurnOutcome]


@dataclass
class Turn:
    """One step in a scenario variant."""
    message: Optional[str]  # None = stateful-only step, no chat call
    assertion: Assertion
    label: str = ""
    # Optional per-turn overrides.
    persona: str = "chat"
    images: Optional[list[str]] = None
    # If set, the chat is sent with this exact conversation_id (instead of
    # the scenario's running conversation_id). Used for §9 memory where a
    # turn intentionally starts a new conversation.
    force_new_conversation: bool = False
    # Use ephemeral mode for this turn. Ephemeral chats skip the
    # conversation store AND skip the document-store funnel for inline
    # images. §5 vision scenarios use this to avoid a qwen-vl quirk
    # where the document-funneled path sometimes produces phantom
    # "document not found" refusals.
    ephemeral: bool = False
    # Soft retries for turns where a known model-level sampling quirk
    # can produce a spurious failure that doesn't indicate a regression.
    # Retries are only attempted on ASSERTION failure (not stream
    # errors, which remain immediately fatal), and the turn passes if
    # any of (1 + soft_retries) attempts passes. Used for §5 vision
    # where qwen-vl autoregressively commits to "I cannot see any
    # image" when that token happens to be sampled first at rate ~3-4%
    # despite the image being perfectly present in vLLM's context.
    soft_retries: int = 0
    # §28 tag-scoped search. Maps directly to the request body's
    # `tags` field. Each entry is {"kind": "topic"|"group"|"contributor",
    # "value": "<slug>"}. None / empty = no scope filter.
    tags: Optional[list[dict]] = None


@dataclass
class Variant:
    """One flow through a scenario — a sequence of turns."""
    label: str
    turns: list[Turn]


@dataclass
class Scenario:
    name: str
    description: str
    variants: list[Variant]
    setup: Optional[Callable[[httpx.AsyncClient, str, dict], Awaitable[None]]] = None
    teardown: Optional[Callable[[httpx.AsyncClient, str, dict], Awaitable[None]]] = None
    # Optional default reps override (otherwise inherits from CLI).
    reps_override: Optional[int] = None


# ----------------------------------------------------------------------------
# Assertion helpers
# ----------------------------------------------------------------------------


def _brief_tool_calls(res: dict) -> list[str]:
    return list(res.get("tool_calls") or [])


def _content_preview(res: dict, limit: int = 200) -> str:
    return (res.get("content") or "")[:limit].replace("\n", " ")


def assert_clarification_fired(res: dict, state: dict) -> TurnOutcome:
    tc = _brief_tool_calls(res)
    clars = len(res.get("clarifications") or [])
    t = TurnOutcome(
        passed=False,
        tool_calls=tc,
        clarifications=clars,
        content_preview=_content_preview(res),
    )
    if res.get("errors"):
        t.reason = f"stream error: {res['errors'][0]}"
        return t
    if clars != 1:
        t.reason = f"expected exactly 1 clarification event, got {clars}"
        return t
    other = [name for name in tc if name != "ask_clarification"]
    if other:
        t.reason = f"other tool calls fired alongside clarification: {other}"
        return t
    t.passed = True
    return t


def assert_no_clarification(res: dict, state: dict) -> TurnOutcome:
    tc = _brief_tool_calls(res)
    clars = len(res.get("clarifications") or [])
    t = TurnOutcome(
        passed=False,
        tool_calls=tc,
        clarifications=clars,
        content_preview=_content_preview(res),
    )
    if res.get("errors"):
        t.reason = f"stream error: {res['errors'][0]}"
        return t
    if clars != 0:
        t.reason = f"unexpected clarification fired: {clars}"
        return t
    content = (res.get("content") or "").strip()
    if len(content) < 30 and not tc:
        t.reason = f"empty answer and no tools: {content!r}"
        return t
    t.passed = True
    return t


def assert_tool_called(tool_name: str, min_count: int = 1) -> Assertion:
    def _inner(res: dict, state: dict) -> TurnOutcome:
        tc = _brief_tool_calls(res)
        count = sum(1 for name in tc if name == tool_name)
        t = TurnOutcome(
            passed=False,
            tool_calls=tc,
            clarifications=len(res.get("clarifications") or []),
            content_preview=_content_preview(res),
        )
        if res.get("errors"):
            t.reason = f"stream error: {res['errors'][0]}"
            return t
        if count < min_count:
            t.reason = (
                f"expected {tool_name} to fire at least {min_count}x, "
                f"got {count}; all tools: {tc}"
            )
            return t
        t.passed = True
        return t

    return _inner


def assert_any_tool_called(tool_names: list[str]) -> Assertion:
    """Any one of ``tool_names`` is acceptable."""
    def _inner(res: dict, state: dict) -> TurnOutcome:
        tc = _brief_tool_calls(res)
        t = TurnOutcome(
            passed=False,
            tool_calls=tc,
            clarifications=len(res.get("clarifications") or []),
            content_preview=_content_preview(res),
        )
        if res.get("errors"):
            t.reason = f"stream error: {res['errors'][0]}"
            return t
        if not any(n in tc for n in tool_names):
            t.reason = f"none of {tool_names} fired; got {tc}"
            return t
        t.passed = True
        return t

    return _inner


def assert_content_contains(needles: list[str], case_insensitive: bool = True) -> Assertion:
    """Response content must contain AT LEAST ONE of the needles."""
    def _inner(res: dict, state: dict) -> TurnOutcome:
        tc = _brief_tool_calls(res)
        t = TurnOutcome(
            passed=False,
            tool_calls=tc,
            clarifications=len(res.get("clarifications") or []),
            content_preview=_content_preview(res),
        )
        if res.get("errors"):
            t.reason = f"stream error: {res['errors'][0]}"
            return t
        content = res.get("content") or ""
        haystack = content.lower() if case_insensitive else content
        hits = [n for n in needles if (n.lower() if case_insensitive else n) in haystack]
        if not hits:
            t.reason = (
                f"none of {needles} found in response; preview: "
                f"{content[:200]!r}"
            )
            return t
        t.passed = True
        return t

    return _inner


def assert_artifact_produced(
    content_type: Optional[str] = None,
    min_artifacts: int = 1,
    source: Optional[str] = None,
) -> Assertion:
    """At least ``min_artifacts`` artifact_created events matched."""
    def _inner(res: dict, state: dict) -> TurnOutcome:
        arts = res.get("artifacts") or []
        matched = arts
        if content_type:
            matched = [a for a in matched if a.get("content_type") == content_type]
        if source:
            matched = [a for a in matched if a.get("source") == source]
        tc = _brief_tool_calls(res)
        t = TurnOutcome(
            passed=False,
            tool_calls=tc,
            clarifications=len(res.get("clarifications") or []),
            content_preview=_content_preview(res),
        )
        if res.get("errors"):
            t.reason = f"stream error: {res['errors'][0]}"
            return t
        if len(matched) < min_artifacts:
            t.reason = (
                f"expected ≥{min_artifacts} artifacts matching "
                f"content_type={content_type} source={source}; got "
                f"{len(matched)} of {len(arts)} total"
            )
            return t
        t.passed = True
        return t

    return _inner


def assert_all_of(*assertions: Assertion) -> Assertion:
    """Compose multiple assertions — all must pass."""
    def _inner(res: dict, state: dict) -> TurnOutcome:
        final: TurnOutcome = TurnOutcome(passed=True)
        for a in assertions:
            outcome = a(res, state)
            if not outcome.passed:
                return outcome
            final = outcome  # keep last for tool_calls/content
        return final

    return _inner


# ----------------------------------------------------------------------------
# Runner
# ----------------------------------------------------------------------------


def _rep_email(scenario_name: str, variant_label: str, rep_index: int) -> str:
    uniq = uuid.uuid4().hex[:8]
    slug = (scenario_name + "-" + variant_label).lower().replace("_", "-")
    return f"flaky-{slug}-r{rep_index}-{uniq}@munin.local"


async def run_rep(
    client: httpx.AsyncClient,
    scenario: Scenario,
    variant: Variant,
    rep_index: int,
    verbose: bool,
) -> RepOutcome:
    email = _rep_email(scenario.name, variant.label, rep_index)
    state: dict[str, Any] = {
        "email": email,
        "conversation_id": None,
        "created_conversations": [],
    }
    outcome = RepOutcome(
        variant_label=variant.label,
        rep_index=rep_index,
        passed=False,
    )
    t0 = time.monotonic()

    try:
        if scenario.setup is not None:
            try:
                await scenario.setup(client, email, state)
            except Exception as e:
                outcome.failure_reason = f"setup raised: {type(e).__name__}: {e}"
                if verbose:
                    traceback.print_exc()
                outcome.duration_s = time.monotonic() - t0
                return outcome

        for turn_idx, turn in enumerate(variant.turns):
            if turn.message is None:
                # Stateful-only step; just run the assertion on empty res.
                turn_res = await turn.assertion({}, state)
                outcome.turn_outcomes.append(turn_res)
                if not turn_res.passed:
                    outcome.failure_turn = turn_idx
                    outcome.failure_reason = turn_res.reason
                    return outcome
                continue

            turn_res: Optional[TurnOutcome] = None
            res: dict = {}
            # Soft-retry loop: up to (1 + turn.soft_retries) attempts.
            # Retries are only attempted on clean assertion failures -
            # a stream error on ANY attempt aborts immediately without
            # further retries, since stream errors indicate real backend
            # problems rather than model sampling variance.
            total_attempts = 1 + max(0, turn.soft_retries)
            for attempt in range(total_attempts):
                conv_id = (
                    None if turn.force_new_conversation
                    else state.get("conversation_id")
                )
                send_kwargs = {
                    "message": turn.message,
                    "conversation_id": conv_id,
                    "persona": turn.persona,
                    "email": email,
                    "ephemeral": turn.ephemeral,
                }
                if turn.images:
                    send_kwargs["images"] = turn.images
                if turn.tags:
                    send_kwargs["tags"] = turn.tags
                try:
                    res = await send_chat(client, **send_kwargs)
                except Exception as e:
                    outcome.failure_turn = turn_idx
                    outcome.failure_reason = (
                        f"send_chat raised: {type(e).__name__}: {e}"
                    )
                    if verbose:
                        traceback.print_exc()
                    return outcome

                # Update state with the conv id on successful send.
                new_conv_id = res.get("conversation_id")
                if new_conv_id and new_conv_id != state.get("conversation_id"):
                    state["created_conversations"].append(new_conv_id)
                state["conversation_id"] = new_conv_id
                state[f"turn_{turn_idx}_response"] = res

                # Hard stop on stream errors - don't retry those.
                if res.get("errors"):
                    turn_res = TurnOutcome(
                        passed=False,
                        reason=f"stream error: {res['errors'][0]}",
                        tool_calls=res.get("tool_calls") or [],
                        clarifications=len(res.get("clarifications") or []),
                        content_preview=_content_preview(res),
                    )
                    break

                try:
                    turn_res = turn.assertion(res, state)
                except Exception as e:
                    turn_res = TurnOutcome(
                        passed=False,
                        reason=f"assertion raised: {type(e).__name__}: {e}",
                        tool_calls=res.get("tool_calls") or [],
                        clarifications=len(res.get("clarifications") or []),
                        content_preview=_content_preview(res),
                    )
                    if verbose:
                        traceback.print_exc()
                    break  # assertion raised — don't retry

                if turn_res.passed:
                    if attempt > 0 and verbose:
                        print(
                            f"        turn {turn_idx} passed on attempt "
                            f"{attempt + 1}/{total_attempts}"
                        )
                    break
                # Soft failure: retry if budget remains.
                if attempt + 1 < total_attempts and verbose:
                    print(
                        f"        turn {turn_idx} soft-retrying "
                        f"({attempt + 1}/{total_attempts}): "
                        f"{turn_res.reason[:120]}"
                    )

            assert turn_res is not None
            outcome.turn_outcomes.append(turn_res)
            if not turn_res.passed:
                outcome.failure_turn = turn_idx
                outcome.failure_reason = turn_res.reason
                return outcome

        outcome.passed = True
    finally:
        outcome.duration_s = time.monotonic() - t0
        # Teardown: always attempted even on failure so state doesn't leak.
        try:
            if scenario.teardown is not None:
                await scenario.teardown(client, email, state)
            # Delete every conversation the rep created.
            for cid in state.get("created_conversations") or []:
                try:
                    await _delete_chat(client, cid, email)
                except Exception:
                    pass
        except Exception as e:
            if verbose:
                print(f"  [teardown warning] {type(e).__name__}: {e}")

    return outcome


async def run_scenario(
    client: httpx.AsyncClient,
    scenario: Scenario,
    reps: int,
    verbose: bool,
) -> ScenarioOutcome:
    result = ScenarioOutcome(name=scenario.name)
    effective_reps = scenario.reps_override or reps

    for variant in scenario.variants:
        for rep_index in range(effective_reps):
            label = f"{scenario.name} / {variant.label} rep {rep_index + 1}/{effective_reps}"
            print(f"  [ .. ] {label} ", end="", flush=True)
            t0 = time.monotonic()
            try:
                rep = await run_rep(client, scenario, variant, rep_index, verbose)
            except Exception as e:
                dt = time.monotonic() - t0
                rep = RepOutcome(
                    variant_label=variant.label,
                    rep_index=rep_index,
                    passed=False,
                    duration_s=dt,
                    failure_reason=f"rep raised: {type(e).__name__}: {e}",
                )
                if verbose:
                    traceback.print_exc()
            result.reps.append(rep)
            tag = "PASS" if rep.passed else "FAIL"
            print(
                f"\r  [{tag}] {label} ({rep.duration_s:5.1f}s)"
                + (f": turn {rep.failure_turn} - {rep.failure_reason}"
                   if not rep.passed else "")
            )
            if verbose and not rep.passed:
                for i, to in enumerate(rep.turn_outcomes):
                    ok = "ok" if to.passed else "FAIL"
                    print(f"        turn {i} [{ok}] tools={to.tool_calls}")
                    if to.content_preview:
                        print(f"              content: {to.content_preview[:200]}")

    return result


# ----------------------------------------------------------------------------
# Scenario definitions
# ----------------------------------------------------------------------------


def _red_square_data_url() -> str:
    """
    Build a 64x64 solid-red PNG and return it as a data URL. Reuses
    ``make_solid_png`` from stress-test.py so we stay consistent with the
    existing vision test fixtures.

    1x1 does NOT work: qwen2-vl's vision tower has a 14x14 minimum patch
    size, so a 1-pixel image gets rejected or embedded as garbage and
    the model responds "I cannot see any image attached". 64x64 is the
    smallest safe size that also matches the existing stress-test
    vision_color fixture.
    """
    png = make_solid_png(64, 64, (220, 30, 30))
    return png_to_data_url(png)


# --- §14 clarification full loop --------------------------------------------


def _clarification_scenario() -> Scenario:
    def follow_up_answer(res: dict, state: dict) -> TurnOutcome:
        # After the user submits their answer, the next turn must end
        # cleanly - no stream errors. §14 spec explicitly allows the
        # model to ask ANOTHER clarification on turn 2 if the first
        # round of answers surfaced new ambiguities, so we don't
        # assert "no clarification". We just require the turn to
        # produce SOMETHING: a substantive prose answer, a concrete
        # tool call, or a valid follow-up clarification card.
        tc = _brief_tool_calls(res)
        out = TurnOutcome(
            passed=False,
            tool_calls=tc,
            clarifications=len(res.get("clarifications") or []),
            content_preview=_content_preview(res),
        )
        if res.get("errors"):
            out.reason = f"stream error: {res['errors'][0]}"
            return out
        content = (res.get("content") or "").strip()
        if not content and not tc and not (res.get("clarifications") or []):
            out.reason = "empty turn: no content, tools, or clarification"
            return out
        out.passed = True
        return out

    def sequential_no_prose_q(res: dict, state: dict) -> TurnOutcome:
        # Regression guard for conv a870e6f5 (2026-04-15): after a
        # partial/incomplete answer on turn 2, if the model decides it
        # still needs one more piece of info, the follow-up clarification
        # MUST go through the ask_clarification tool (structured
        # ``clarification`` SSE event). Writing the sequential question
        # as prose ("Q4: ... - option A - option B") is the failure
        # mode the updated chat_service heuristic should catch and
        # convert to a forced-retry tool call.
        tc = _brief_tool_calls(res)
        clars = res.get("clarifications") or []
        out = TurnOutcome(
            passed=False,
            tool_calls=tc,
            clarifications=len(clars),
            content_preview=_content_preview(res),
        )
        if res.get("errors"):
            out.reason = f"stream error: {res['errors'][0]}"
            return out
        content = (res.get("content") or "").strip().lower()
        # Signals that the model wrote a clarification as prose. Any of
        # these appearing WITHOUT a structured clarification event means
        # the fallback failed to catch it.
        prose_markers = (
            "q4:", "q5:", "**q4", "**q5",
            "one more detail",
            "one more question",
            "before i write",
            "before i can",
            "could you clarify",
        )
        has_prose_marker = any(m in content for m in prose_markers)
        if has_prose_marker and not clars:
            out.reason = (
                f"prose clarification markers present without structured "
                f"clarification event; markers found, content preview: "
                f"{content[:200]!r}"
            )
            return out
        # Otherwise: any end state (structured clarification, substantive
        # prose, or tool calls) is acceptable. Empty turns fail.
        if not content and not tc and not clars:
            out.reason = "empty turn: no content, tools, or clarification"
            return out
        out.passed = True
        return out

    return Scenario(
        name="clarification_full_loop",
        description=(
            "§14 ask_clarification — ambiguous prompt fires a card, "
            "synthetic answer routes to concrete follow-up work."
        ),
        variants=[
            Variant(
                label="epr_fallback",  # exercises prose-detection → forced retry
                turns=[
                    Turn(
                        message=(
                            "I need you help with a project, I need to code "
                            "an analysis script for an EPR record"
                        ),
                        assertion=assert_clarification_fired,
                        label="turn_1_ambiguous",
                    ),
                    Turn(
                        message=(
                            "Q1: Electron paramagnetic resonance (physics/chemistry). "
                            "Q2: Python. Q3: simple line-shape fitting on a CSV."
                        ),
                        assertion=follow_up_answer,
                        label="turn_2_answer",
                    ),
                ],
            ),
            Variant(
                label="help_organic",  # organic tool call, no fallback needed
                turns=[
                    Turn(
                        message="help me with my paper",
                        assertion=assert_clarification_fired,
                    ),
                    Turn(
                        message=(
                            "Q1: I'm drafting the introduction section. "
                            "Q2: Topic is lipid bilayer phase transitions. "
                            "Q3: Biophysics / Nature-style."
                        ),
                        assertion=follow_up_answer,
                    ),
                ],
            ),
            Variant(
                label="fix_it_organic",  # single-word, organic
                turns=[
                    Turn(
                        message="fix it",
                        assertion=assert_clarification_fired,
                        persona="code",
                    ),
                    Turn(
                        message=(
                            "Sorry, I meant: fix this Python function so it "
                            "doesn't crash on empty lists:\n\n"
                            "def average(xs):\n    return sum(xs) / len(xs)"
                        ),
                        assertion=follow_up_answer,
                        persona="code",
                    ),
                ],
            ),
            Variant(
                # Regression for conv a870e6f5: EPR prompt → card →
                # partial answer that intentionally omits ONE parameter
                # (file format) so the model wants to clarify again →
                # the follow-up clarification must go through the tool,
                # not prose. Exercises the full sequential Q/A loop.
                label="epr_sequential_partial_answer",
                turns=[
                    Turn(
                        message=(
                            "I need you help with a project, I need to "
                            "code an analysis script for an EPR record"
                        ),
                        assertion=assert_clarification_fired,
                        label="turn_1_ambiguous",
                    ),
                    Turn(
                        message=(
                            "Q1: Electron Paramagnetic Resonance "
                            "(physics/chemistry spectroscopy). "
                            "Q2: R. "
                            "Q3: Data parsing and visualization."
                        ),
                        assertion=sequential_no_prose_q,
                        label="turn_2_partial_answer",
                    ),
                ],
            ),
        ],
    )


# --- §18 compile_latex -------------------------------------------------------


def _no_hallucinated_artifact_urls(res: dict, state: dict) -> TurnOutcome:
    """
    Regression for conv 979c7fda (2026-04-17): the model linked a
    compile_latex PDF using ``search.muninai.org/paper/<artifact_id>``
    (the paper download URL pattern) instead of the correct
    ``external_url`` from the tool result. The resulting link opened the
    chat page in a new tab instead of downloading the PDF.

    Negative assertion: the assistant content must NOT contain the
    paper-download URL pattern when the turn produced sandbox artifacts.
    """
    content = (res.get("content") or "").lower()
    t = TurnOutcome(
        passed=True,
        tool_calls=_brief_tool_calls(res),
        clarifications=len(res.get("clarifications") or []),
        content_preview=_content_preview(res),
    )
    if "search.muninai.org/paper/" in content:
        t.passed = False
        t.reason = (
            "hallucinated paper-download URL for a sandbox artifact; "
            "model used search.muninai.org/paper/... instead of "
            "the external_url from the tool result"
        )
    return t


def _latex_scenario() -> Scenario:
    latex_ok = assert_all_of(
        assert_tool_called("compile_latex"),
        assert_artifact_produced(content_type="application/pdf"),
        assert_artifact_produced(content_type="application/x-tex"),
        _no_hallucinated_artifact_urls,
    )

    return Scenario(
        name="latex_compile",
        description=(
            "§18 compile_latex — model writes LaTeX, verifies it compiles, "
            "surfaces both .tex and .pdf artifacts."
        ),
        variants=[
            Variant(
                label="quadratic_equation",
                turns=[
                    Turn(
                        message=(
                            "Write me a minimal LaTeX article that displays "
                            "the quadratic formula in display math mode, "
                            "using amsmath. Verify it compiles."
                        ),
                        assertion=latex_ok,
                        persona="code",
                    ),
                ],
            ),
            Variant(
                label="greek_alphabet_table",
                turns=[
                    Turn(
                        message=(
                            "Write a LaTeX document containing a small "
                            "table with the first five Greek letters "
                            "alongside their English names. Compile it "
                            "and return the PDF."
                        ),
                        assertion=latex_ok,
                        persona="chat",
                    ),
                ],
            ),
            # Regression for chat e45e3f2b (2026-04-25): model
            # initially compiled a Beamer deck successfully, then on
            # the second turn ("make the colour theme red") wrote
            # prose claiming success + a fabricated artifact UUID
            # WITHOUT actually calling compile_latex. The user had
            # to send "Can you check again?" before the model
            # noticed it had hallucinated the workflow.
            #
            # Two turns: build, then modify. Both turns MUST fire
            # compile_latex. The second turn is the one that used
            # to fail.
            Variant(
                label="modify_colour_theme_after_initial_build",
                turns=[
                    Turn(
                        message=(
                            "Please make me a Beamer slide deck "
                            "explaining basic quantum mechanics. "
                            "Compile it."
                        ),
                        assertion=latex_ok,
                        persona="chat",
                    ),
                    Turn(
                        message=(
                            "Could you make the presentation colour "
                            "theme red?"
                        ),
                        assertion=latex_ok,
                        persona="chat",
                        # Allow one soft retry — the regression we're
                        # guarding against was sampling-rare-ish, and
                        # the persona rule may not catch every path.
                        soft_retries=1,
                    ),
                ],
            ),
        ],
        # LaTeX compiles are fast but model latency is ~10-30s; 3 reps keeps
        # total runtime reasonable.
        reps_override=3,
    )


# --- §22 Artifact lifecycle --------------------------------------------------


def _artifact_scenario() -> Scenario:
    create_assert = assert_all_of(
        assert_tool_called("create_artifact"),
        assert_artifact_produced(source="model_written"),
    )
    update_assert = assert_tool_called("update_artifact")

    return Scenario(
        name="artifact_lifecycle",
        description=(
            "§22 Artifacts — create a model-written artifact on turn 1, "
            "then update it on turn 2. Verify create_artifact / "
            "update_artifact both fire and artifact_created + "
            "artifact_updated SSE events land."
        ),
        variants=[
            Variant(
                label="python_fibonacci",
                turns=[
                    Turn(
                        message=(
                            "Create an artifact containing a short Python "
                            "function that computes the n-th Fibonacci "
                            "number recursively. No memoization yet."
                        ),
                        assertion=create_assert,
                        persona="code",
                    ),
                    Turn(
                        message=(
                            "Now update that artifact to add memoization "
                            "using functools.lru_cache."
                        ),
                        assertion=update_assert,
                        persona="code",
                    ),
                ],
            ),
            Variant(
                label="markdown_abstract",
                turns=[
                    Turn(
                        message=(
                            "Create an artifact with a short 3-sentence "
                            "abstract on NMR relaxation in membrane proteins."
                        ),
                        assertion=create_assert,
                        persona="research",
                    ),
                    Turn(
                        message=(
                            "Update that abstract to include a mention of "
                            "cross-correlated relaxation experiments."
                        ),
                        assertion=update_assert,
                        persona="research",
                    ),
                ],
            ),
        ],
        reps_override=3,
    )


# --- §21 Projects scoping ----------------------------------------------------


async def _project_setup(client: httpx.AsyncClient, email: str, state: dict) -> None:
    body = {
        "name": f"Flakiness-{uuid.uuid4().hex[:8]}",
        "instructions": (
            "For any coding task in this project, the user strongly "
            "prefers Rust as the implementation language. Always suggest "
            "Rust by default unless the user explicitly asks for a "
            "different language."
        ),
        "default_persona": "code",
    }
    r = await client.post(
        f"{BASE}/api/projects",
        json=body,
        headers={"X-Munin-Email": email, "Content-Type": "application/json"},
        timeout=HTTP_TIMEOUT,
    )
    r.raise_for_status()
    proj = r.json()
    state["project_id"] = proj["id"]


async def _project_teardown(client: httpx.AsyncClient, email: str, state: dict) -> None:
    pid = state.get("project_id")
    if not pid:
        return
    try:
        await client.delete(
            f"{BASE}/api/projects/{pid}",
            headers={"X-Munin-Email": email},
            timeout=HTTP_TIMEOUT,
        )
    except Exception:
        pass


def _project_scoping_scenario() -> Scenario:
    rust_check = assert_content_contains(["rust", "rs", "cargo"])

    return Scenario(
        name="project_instructions_injection",
        description=(
            "§21 Projects — project instructions make it into the system "
            "prompt and visibly steer the model's choices on turn 1."
        ),
        setup=_project_setup,
        teardown=_project_teardown,
        variants=[
            Variant(
                label="language_hint",
                turns=[
                    Turn(
                        message="Write a quick script that reads stdin line-by-line and prints each line's length.",
                        assertion=rust_check,
                        persona="code",
                    ),
                ],
            ),
        ],
        reps_override=3,
    )


# --- §9 Memory across conversations ------------------------------------------


def _memory_scenario() -> Scenario:
    def remember_fired(res: dict, state: dict) -> TurnOutcome:
        return assert_tool_called("remember")(res, state)

    def recall_ultraviolet(res: dict, state: dict) -> TurnOutcome:
        return assert_content_contains(["ultraviolet", "UV"])(res, state)

    return Scenario(
        name="memory_cross_conversation",
        description=(
            "§9 Memory — remember a fact in conversation A, start a fresh "
            "conversation B, and verify the model recalls the fact "
            "(either via the system-prompt memory block or via a recall "
            "tool call)."
        ),
        variants=[
            Variant(
                label="favourite_color",
                turns=[
                    Turn(
                        message=(
                            "Please remember this fact about me for future "
                            "conversations: my favorite color is ultraviolet."
                        ),
                        assertion=remember_fired,
                    ),
                    Turn(
                        message="What's my favorite color?",
                        assertion=recall_ultraviolet,
                        force_new_conversation=True,
                    ),
                ],
            ),
        ],
        reps_override=3,
    )


# --- §5 Vision ---------------------------------------------------------------


def _vision_scenario() -> Scenario:
    def red_seen(res: dict, state: dict) -> TurnOutcome:
        return assert_content_contains(["red", "crimson"])(res, state)

    data_url = _red_square_data_url()

    return Scenario(
        name="vision_single_turn",
        description=(
            "§5 Vision — upload a 1x1 red PNG in the same turn and ask "
            "the model to name the colour. Verifies multimodal content "
            "survives the SSE round-trip."
        ),
        variants=[
            Variant(
                label="red_square",
                turns=[
                    Turn(
                        # qwen-vl is surprisingly sensitive to whether the
                        # prompt ASSERTS the image exists vs asks about
                        # properties of "the attached image". The former is
                        # 6/6 reliable, the latter drops ~30% of the time
                        # with "I cannot see any image" hallucinations. The
                        # wording below is the tested-reliable shape: start
                        # by asserting facts about the image, then ask for
                        # the answer in a fixed format.
                        message=(
                            "The image attached to this message is a "
                            "solid-colour square. State the colour as a "
                            "single English word. Do not preamble. "
                            "Example format: 'blue'."
                        ),
                        assertion=red_seen,
                        images=[data_url],
                        ephemeral=True,
                        # qwen-vl rarely (~3-4%) commits to "I cannot
                        # see any image" as its first output token and
                        # never recovers within the same turn. Two soft
                        # retries push effective failure rate to
                        # <0.005%, well below a level that indicates a
                        # real regression.
                        soft_retries=2,
                    ),
                ],
            ),
        ],
        reps_override=3,
    )


# --- §25 Profile injection ---------------------------------------------------


async def _profile_setup(client: httpx.AsyncClient, email: str, state: dict) -> None:
    body = {
        "display_name": "Flaky Test User",
        "role": "computational chemist",
        "preferences": (
            "Always default to Python for any scripting task. The user "
            "prefers scipy/numpy over any other scientific stack."
        ),
        "default_persona": "chat",
    }
    await client.put(
        f"{BASE}/api/profile",
        json=body,
        headers={"X-Munin-Email": email, "Content-Type": "application/json"},
        timeout=HTTP_TIMEOUT,
    )


async def _profile_teardown(client: httpx.AsyncClient, email: str, state: dict) -> None:
    try:
        await client.delete(
            f"{BASE}/api/profile",
            headers={"X-Munin-Email": email},
            timeout=HTTP_TIMEOUT,
        )
    except Exception:
        pass


def _profile_scenario() -> Scenario:
    python_check = assert_content_contains(["python", "numpy", "scipy"])

    return Scenario(
        name="profile_injection",
        description=(
            "§25 Profile — user profile preferences reach the system "
            "prompt and steer the model's defaults without the user "
            "restating them."
        ),
        setup=_profile_setup,
        teardown=_profile_teardown,
        variants=[
            Variant(
                label="language_preference",
                turns=[
                    Turn(
                        message="Write me a quick script that computes the FFT of a signal.",
                        assertion=python_check,
                    ),
                ],
            ),
        ],
        reps_override=3,
    )


# --- Agentic composition ----------------------------------------------------


def _compose_scenario() -> Scenario:
    compose_assert = assert_all_of(
        assert_any_tool_called([
            "paper_lookup",
            "paper_search",
            "read_paper",          # equally valid for "look up this DOI"
            "semantic_scholar_search",
            "compare_papers",
            "deep_research",
        ]),
    )

    return Scenario(
        name="agentic_composition",
        description=(
            "Model can chain a search/lookup tool with a clear concrete "
            "research target, without hallucinating clarification or "
            "bailing out."
        ),
        variants=[
            Variant(
                label="alphafold_lookup",
                turns=[
                    Turn(
                        message=(
                            "Look up the AlphaFold paper "
                            "10.1038/s41586-021-03819-2 and summarise the "
                            "core contribution in one paragraph."
                        ),
                        assertion=compose_assert,
                        persona="research",
                    ),
                ],
            ),
            Variant(
                label="citation_export",
                turns=[
                    Turn(
                        message=(
                            "Export these two DOIs as BibTeX: "
                            "10.1038/s41586-021-03819-2 and "
                            "10.1126/science.abj8754"
                        ),
                        assertion=assert_tool_called("export_citations"),
                        persona="research",
                    ),
                ],
            ),
        ],
        reps_override=3,
    )


# ----------------------------------------------------------------------------
# §28 Scenario: tag-scoped search (#group / #@user / #topic)
# ----------------------------------------------------------------------------
#
# Three behavioural assertions the unit tests in
# retrieval/tests/test_query_tags.py can't make:
#
# 1. With tags in the request body, the model SEES the active scope
#    and reports it back when asked "what knowledge do I have
#    attached" (regression test for the original bug — model used to
#    answer "no documents found" because the ContextVar was never
#    surfaced into the system prompt).
#
# 2. With tags, paper_search's tool result carries `applied_tags`
#    matching what we sent. This is the structured contract the
#    frontend depends on for the "scoped to X corpus" UI.
#
# 3. With multiple tags, all of them reach paper_search (AND-combine).


def _expected_paper_search_with_tags(expected_tags: list[dict]) -> Assertion:
    """paper_search must have been called AND its tool_result must
    carry applied_tags matching `expected_tags` exactly (order-
    insensitive comparison on (kind, value) pairs)."""

    expected_set = {(t["kind"], t["value"]) for t in expected_tags}

    def _inner(res: dict, state: dict) -> TurnOutcome:
        tc = _brief_tool_calls(res)
        t = TurnOutcome(
            passed=False,
            tool_calls=tc,
            clarifications=len(res.get("clarifications") or []),
            content_preview=_content_preview(res),
        )
        if res.get("errors"):
            t.reason = f"stream error: {res['errors'][0]}"
            return t
        if "paper_search" not in tc:
            t.reason = (
                f"expected paper_search to fire, got {tc}; the model "
                f"may have skipped local search — try a more search-y "
                f"prompt or check persona prompts"
            )
            return t
        # Find the paper_search tool_result
        tool_results = res.get("tool_results") or []
        ps = next(
            (tr for tr in tool_results if tr.get("name") == "paper_search"),
            None,
        )
        if ps is None:
            t.reason = (
                "paper_search tool_call event fired but no matching "
                "tool_result event arrived (stream cut off?); cannot "
                "verify applied_tags"
            )
            return t
        result = (ps or {}).get("result") or {}
        applied = result.get("applied_tags") or []
        applied_set = {
            (a.get("kind"), a.get("value"))
            for a in applied
            if isinstance(a, dict)
        }
        if applied_set != expected_set:
            t.reason = (
                f"applied_tags mismatch: expected {expected_set!r}, "
                f"got {applied_set!r}"
            )
            return t
        t.passed = True
        return t

    return _inner


def _content_mentions_active_scope(needles: list[str]) -> Assertion:
    """Final-prose check: the model's answer must mention every needle
    (case-insensitive). Used to verify the model surfaced the active
    scope in human language ('I searched the Zeitler Lab corpus...')."""

    def _inner(res: dict, state: dict) -> TurnOutcome:
        tc = _brief_tool_calls(res)
        t = TurnOutcome(
            passed=False,
            tool_calls=tc,
            clarifications=len(res.get("clarifications") or []),
            content_preview=_content_preview(res),
        )
        if res.get("errors"):
            t.reason = f"stream error: {res['errors'][0]}"
            return t
        content_lower = (res.get("content") or "").lower()
        missing = [n for n in needles if n.lower() not in content_lower]
        if missing:
            t.reason = (
                f"final answer did not mention {missing!r} (active "
                f"scope tags); model may not have noticed the ACTIVE "
                f"SCOPE TAGS block in the system prompt"
            )
            return t
        t.passed = True
        return t

    return _inner


def _query_tags_scenario() -> Scenario:
    return Scenario(
        name="query_tags",
        description=(
            "§28 tag-scoped search: model knows about active #tag "
            "filters, paper_search results carry applied_tags, "
            "multiple tags AND-combine through to the Qdrant filter."
        ),
        variants=[
            # Variant 1: model awareness — when asked, the model must
            # report the active scope. This is the regression test for
            # the original 789c9f0c bug.
            Variant(
                label="awareness_with_group_tag",
                turns=[
                    Turn(
                        message=(
                            "What knowledge or scope filters do I "
                            "currently have attached to this chat? "
                            "Be specific."
                        ),
                        assertion=_content_mentions_active_scope(["zeitler"]),
                        persona="chat",
                        ephemeral=True,
                        tags=[{"kind": "group", "value": "zeitler"}],
                        # Model can phrase it many ways; sampling
                        # variance can drop the slug occasionally.
                        soft_retries=1,
                    ),
                ],
            ),
            # Variant 2: paper_search is invoked AND scoped. Confirms
            # the ContextVar → Qdrant filter wiring all the way to
            # the structured tool_result the frontend reads.
            Variant(
                label="paper_search_applied_tags",
                turns=[
                    Turn(
                        message=(
                            "Search the local corpus for papers "
                            "about angioedema. Use the paper_search "
                            "tool."
                        ),
                        assertion=_expected_paper_search_with_tags(
                            [{"kind": "group", "value": "zeitler"}]
                        ),
                        persona="research",
                        ephemeral=True,
                        tags=[{"kind": "group", "value": "zeitler"}],
                        soft_retries=1,
                    ),
                ],
            ),
            # Variant 3: multiple tags AND-combine — both must reach
            # paper_search's applied_tags echo.
            Variant(
                label="multi_tag_and_combine",
                turns=[
                    Turn(
                        message=(
                            "Search the local corpus for papers on "
                            "membrane proteins. Use paper_search."
                        ),
                        assertion=_expected_paper_search_with_tags([
                            {"kind": "group", "value": "zeitler"},
                            {
                                "kind": "topic",
                                "value": "nmr-of-membrane-proteins",
                            },
                        ]),
                        persona="research",
                        ephemeral=True,
                        tags=[
                            {"kind": "group", "value": "zeitler"},
                            {
                                "kind": "topic",
                                "value": "nmr-of-membrane-proteins",
                            },
                        ],
                        soft_retries=1,
                    ),
                ],
            ),
        ],
        # 3 reps — model-prose checks have higher variance than tool
        # firings so we want enough samples to distinguish FAIL from
        # FLAKY without burning vLLM time.
        reps_override=3,
    )


SCENARIOS = [
    _clarification_scenario(),
    _latex_scenario(),
    _artifact_scenario(),
    _project_scoping_scenario(),
    _memory_scenario(),
    _vision_scenario(),
    _profile_scenario(),
    _compose_scenario(),
    _query_tags_scenario(),
]


# ----------------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------------


def _render_summary(
    results: list[ScenarioOutcome],
    total_duration_s: float,
    strict: bool,
) -> int:
    print()
    print("=" * 74)
    print(
        f"{'Scenario':<36} {'Reps':>6} {'Pass':>6} {'Fail':>6} {'Status':>10}"
    )
    print("-" * 74)
    total_reps = 0
    total_passed = 0
    flaky: list[ScenarioOutcome] = []
    failed: list[ScenarioOutcome] = []
    for r in results:
        total_reps += r.total
        total_passed += r.passed
        print(
            f"{r.name:<36} {r.total:>6} {r.passed:>6} {r.failed:>6} {r.status:>10}"
        )
        if r.status == "FLAKY":
            flaky.append(r)
        elif r.status == "FAIL":
            failed.append(r)
    print("-" * 74)
    print(
        f"{'TOTAL':<36} {total_reps:>6} {total_passed:>6} "
        f"{total_reps - total_passed:>6}"
    )
    print(f"Duration: {total_duration_s:.1f}s")
    print("=" * 74)

    if flaky:
        print("\nFLAKY scenarios (some reps passed, some failed):")
        for r in flaky:
            print(f"  - {r.name}: {r.passed}/{r.total}")
            for rep in r.reps:
                if not rep.passed:
                    print(
                        f"      {rep.variant_label} rep {rep.rep_index + 1}: "
                        f"turn {rep.failure_turn} - {rep.failure_reason}"
                    )

    if failed:
        print("\nFAILED scenarios (all reps failed):")
        for r in failed:
            print(f"  - {r.name}: 0/{r.total}")
            for rep in r.reps[:2]:
                print(
                    f"      {rep.variant_label} rep {rep.rep_index + 1}: "
                    f"turn {rep.failure_turn} - {rep.failure_reason}"
                )

    # Exit code semantics:
    #   - FAIL (all reps of some scenario failed): exit 1. This is a real
    #     regression that single-pass stress-test would catch too.
    #   - FLAKY (some reps failed for some scenarios) without any FAIL:
    #     exit 0 by default. LLM sampling variance at 5-10% rates is a
    #     feature of the system, not a bug, and treating every 1/15 flake
    #     as a CI blocker would be signal-to-noise hostile. Pass --strict
    #     to promote FLAKY to failure when you explicitly want that.
    #   - PASS everywhere: exit 0.
    if failed:
        return 1
    if flaky:
        if strict:
            print("\n[strict mode] FLAKY scenarios promoted to failure")
            return 1
        print(
            "\nNote: FLAKY scenarios are informational under default mode - "
            "run with --strict to promote them to failure."
        )
        return 0
    return 0


async def main() -> int:
    parser = argparse.ArgumentParser(
        description="Flakiness suite for Munin backend components."
    )
    parser.add_argument(
        "--reps",
        type=int,
        default=5,
        help="Default reps per variant. Scenarios with reps_override ignore this.",
    )
    parser.add_argument(
        "--only",
        help="Run only scenarios whose name contains this substring.",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Dump per-turn outcomes and tracebacks on failure.",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help=(
            "Treat FLAKY scenarios as failures (exit 1). Default mode "
            "only fails on FAIL (all reps failed for some scenario); "
            "FLAKY is informational so low-rate LLM sampling variance "
            "doesn't red-flag CI. Use this flag when you want zero "
            "tolerance."
        ),
    )
    args = parser.parse_args()

    print(f"Flakiness suite -> {BASE}")
    print(f"Default reps: {args.reps}")
    print()

    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
        try:
            r = await client.get(f"{BASE}/health", timeout=5)
            if r.status_code != 200:
                print(f"[FATAL] /health returned {r.status_code}")
                return 2
        except Exception as e:
            print(f"[FATAL] cannot reach {BASE}/health: {e}")
            return 2

        results: list[ScenarioOutcome] = []
        t0 = time.monotonic()
        for scenario in SCENARIOS:
            if args.only and args.only not in scenario.name:
                print(f"  [SKIP] {scenario.name}")
                continue
            print(f"\n>> {scenario.name}  ({scenario.description})")
            result = await run_scenario(client, scenario, args.reps, args.verbose)
            results.append(result)

    return _render_summary(results, time.monotonic() - t0, strict=args.strict)


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main()))
    except KeyboardInterrupt:
        print("\nInterrupted.")
        sys.exit(130)
