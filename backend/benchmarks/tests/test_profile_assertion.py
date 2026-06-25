"""Tests for the A3 profile assertion: capturing the `routing` SSE event and
asserting the emitted profile against expected.profile in score_item."""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "munin_bench"))

from routing.trajectory import capture  # noqa: E402
from routing.routing_eval import (  # noqa: E402
    Expected,
    RoutingEvalItem,
    ToolCall,
    score_item,
)


def _frame(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


def _item(profile: str) -> RoutingEvalItem:
    return RoutingEvalItem(
        id="t", category="multi_turn", query="plot this",
        expected=Expected(profile=profile), rationale="x",
    )


def test_routing_event_captured():
    raw = _frame("routing", {"profile": "code", "pin": "research",
                             "method": "knn", "confidence": 0.4})
    t = capture(raw)
    assert t.routed_profile == "code"
    assert t.routing_method == "knn"
    assert t.pin == "research"


def test_profile_match_recorded_as_diagnostic():
    # Profile is REPORTED, not gated: it lands in diagnostics, never in checks,
    # and does not affect `passed`.
    res = score_item(_item("code"), [], emitted_profile="code")
    assert res.diagnostics["profile_match"] is True
    assert "profile" not in res.checks
    assert res.passed is True


def test_profile_mismatch_does_not_fail_the_item():
    # A profile miss is reported (diagnostic False) but must NOT fail the item
    # or add a gating failure (decision 2026-06-25: tool outcomes gate).
    res = score_item(_item("code"), [], emitted_profile="research")
    assert res.diagnostics["profile_match"] is False
    assert "profile" not in res.checks
    assert res.passed is True


def test_no_emitted_profile_skips_diagnostic_backward_compat():
    # Pre-router runs (A0/A2) pass emitted_profile=None -> no profile metric,
    # so those baselines stay comparable.
    res = score_item(_item("code"), [], emitted_profile=None)
    assert "profile_match" not in res.diagnostics


def test_no_expected_profile_skips_diagnostic():
    item = RoutingEvalItem(id="t", category="no_tool", query="x",
                           expected=Expected(no_tool=True), rationale="x")
    res = score_item(item, [], emitted_profile="chat")
    assert "profile_match" not in res.diagnostics
