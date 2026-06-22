"""Unit tests for routing/trajectory.py — SSE parse + step reconstruction.

The step-reconstruction logic is the only non-trivial part of A0: the
`tool_call` SSE payload carries no iteration index, so `step` is rebuilt
from where `tool_result` boundaries fall. These tests pin that behaviour,
especially the `solo` case (ask_clarification alone in its step).
"""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(
    0, os.path.join(os.path.dirname(__file__), "..", "munin_bench")
)

from routing.trajectory import (  # noqa: E402
    capture,
    parse_sse,
    reconstruct_steps,
)


def _frame(event: str, data: dict) -> str:
    """Build one SSE frame the way sse_starlette serializes it."""
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


def _tool_call(name: str, **args) -> str:
    return _frame("tool_call", {"id": f"c_{name}", "name": name, "arguments": args})


def _tool_result(name: str) -> str:
    return _frame("tool_result", {"name": name, "result": {"ok": True}})


# --- parse_sse ----------------------------------------------------------

def test_parse_sse_basic_event_data_pairs():
    raw = _frame("conversation", {"id": "abc"}) + _tool_call("web_search", query="x")
    events = parse_sse(raw)
    assert [e.event for e in events] == ["conversation", "tool_call"]
    assert events[0].data["id"] == "abc"
    assert events[1].data["name"] == "web_search"


def test_parse_sse_ignores_id_reconnect_lines():
    # P1 #10 adds `id: <stream>-<seq>` lines; they must not break parsing.
    raw = (
        "id: stream7-1\n"
        + _frame("tool_call", {"id": "c1", "name": "calculate", "arguments": {}})
    )
    events = parse_sse(raw)
    assert len(events) == 1
    assert events[0].event == "tool_call"


# --- reconstruct_steps --------------------------------------------------

def test_single_solo_call_is_step_0():
    # The clarification case: one ask_clarification, nothing else.
    events = parse_sse(_tool_call("ask_clarification", question="where?"))
    calls = reconstruct_steps(events)
    assert len(calls) == 1
    assert calls[0]["step"] == 0
    assert calls[0]["name"] == "ask_clarification"


def test_two_calls_same_iteration_share_a_step():
    # Two tool_calls emitted together (no tool_result between them) =>
    # same step. This is what makes `solo` FAIL when a clarification is
    # emitted alongside a sibling call.
    raw = _tool_call("ask_clarification", q="?") + _tool_call("web_search", query="x")
    calls = reconstruct_steps(parse_sse(raw))
    assert [c["step"] for c in calls] == [0, 0]


def test_calls_across_tool_result_boundary_increment_step():
    # iteration 1: web_search -> result -> iteration 2: run_python
    raw = (
        _tool_call("web_search", query="x")
        + _tool_result("web_search")
        + _tool_call("run_python", code="print(1)")
    )
    calls = reconstruct_steps(parse_sse(raw))
    assert [c["step"] for c in calls] == [0, 1]
    assert [c["name"] for c in calls] == ["web_search", "run_python"]


def test_batch_then_next_iteration():
    # iteration 1: two parallel calls -> two results -> iteration 2: one call
    raw = (
        _tool_call("paper_search", query="a")
        + _tool_call("paper_search", query="b")
        + _tool_result("paper_search")
        + _tool_result("paper_search")
        + _tool_call("read_paper", doi="10.1/x")
    )
    calls = reconstruct_steps(parse_sse(raw))
    assert [c["step"] for c in calls] == [0, 0, 1]


def test_solo_step_membership_via_capture():
    # End-to-end: a solo clarification in its own step is detectable by
    # counting calls sharing trajectory[0].step (what score_item does).
    raw = _tool_call("ask_clarification", question="which city?")
    traj = capture(raw)
    first_step = traj.tool_calls[0]["step"]
    n_in_first_step = sum(1 for c in traj.tool_calls if c["step"] == first_step)
    assert n_in_first_step == 1


# --- capture: side channels + text --------------------------------------

def test_capture_assembles_final_text_from_token_content():
    raw = (
        _frame("token", {"content": "Hello "})
        + _frame("token", {"content": "world"})
        + _frame("done", {})
    )
    traj = capture(raw)
    assert traj.final_text == "Hello world"


def test_capture_collects_errors_and_delegation():
    raw = (
        _frame("delegated", {"to_persona": "code", "reason": "needs python"})
        + _frame("persona_changed", {"persona": "code"})
        + _frame("error", {"message": "boom"})
    )
    traj = capture(raw)
    assert traj.delegated[0]["to_persona"] == "code"
    assert traj.persona_changed[0]["persona"] == "code"
    assert traj.errors == ["boom"]


def test_capture_arguments_survive_as_dict():
    raw = _tool_call("calculate", expression="17% of 4450", mode="numeric")
    traj = capture(raw)
    args = traj.tool_calls[0]["arguments"]
    assert args["mode"] == "numeric"
    assert args["expression"] == "17% of 4450"
