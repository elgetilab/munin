"""Tests for the completed_in_turn non-gating diagnostic (A2 Q4).

Verifies the diagnostic records whether a deferred (via_tool_search_ok) tool
actually fired in-turn, WITHOUT changing the permissive gate or `passed`.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "munin_bench"))

from routing.routing_eval import (  # noqa: E402
    Expected,
    Predicate,
    PredOp,
    RoutingEvalItem,
    ToolCall,
    ToolExpectation,
    score_item,
)


def _item() -> RoutingEvalItem:
    # Mirror export_bibtex: a deferred tool reachable via tool_search.
    return RoutingEvalItem(
        id="export_bibtex_like",
        category="citation_export",
        query="bibtex for these dois",
        expected=Expected(
            required_tools=[ToolExpectation(
                name="export_citations",
                arg_predicates=[Predicate(path="format", op=PredOp.EQ, value="bibtex")],
                via_tool_search_ok=True,
            )],
            reward_basis=["required"],
        ),
        rationale="test",
    )


def test_completed_in_turn_true_when_tool_fires_correctly():
    # The actual tool fired with the right args -> gate passes AND completed.
    traj = [ToolCall(name="export_citations",
                     arguments={"format": "bibtex", "dois": ["10.1/a"]}, step=0)]
    res = score_item(_item(), traj)
    assert res.passed is True
    assert res.diagnostics["completed_in_turn:export_citations"] is True


def test_reached_but_not_completed_passes_gate_but_diagnostic_false():
    # Model reached for tool_search but never fired export_citations.
    # Permissive gate -> passed True; diagnostic -> False (the real gap).
    traj = [ToolCall(name="tool_search", arguments={"query": "bibtex export"}, step=0)]
    res = score_item(_item(), traj)
    assert res.passed is True                                  # permissive gate
    assert res.diagnostics["completed_in_turn:export_citations"] is False


def test_neither_fails_gate_and_diagnostic_false():
    # Neither tool_search nor export_citations -> gate fails, diagnostic False.
    traj = [ToolCall(name="web_search", arguments={"query": "bibtex"}, step=0)]
    res = score_item(_item(), traj)
    assert res.passed is False
    assert res.diagnostics["completed_in_turn:export_citations"] is False


def test_wrong_args_not_completed_even_if_tool_fired():
    # export_citations fired but format != bibtex -> pred fails -> not completed,
    # but tool_search absent so gate also fails (no permissive rescue).
    traj = [ToolCall(name="export_citations",
                     arguments={"format": "ris", "dois": ["10.1/a"]}, step=0)]
    res = score_item(_item(), traj)
    assert res.diagnostics["completed_in_turn:export_citations"] is False
    assert res.passed is False


def test_diagnostic_never_affects_passed():
    # A diagnostic=False must not flip an otherwise-passing item.
    traj = [ToolCall(name="tool_search", arguments={"query": "x"}, step=0)]
    res = score_item(_item(), traj)
    # passed is computed only from `checks`; diagnostics is separate.
    assert res.passed == (all(res.checks.values()) if res.checks else True)
