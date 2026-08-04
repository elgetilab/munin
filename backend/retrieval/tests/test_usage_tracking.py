"""
Standalone tests for the per-request token-usage aggregator
(usage_tracker.py, P0 #3 from docs/architecture/HARNESS-AUDIT-2026-05.md).

Run inside the retrieval container:
    docker exec munin-retrieval python /app/tests/test_usage_tracking.py
Or locally:
    python backend/retrieval/tests/test_usage_tracking.py
"""

from __future__ import annotations

import sys
import traceback
from pathlib import Path

sys.path.insert(0, "/app")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from usage_tracker import (  # noqa: E402
    aggregate_totals,
    current_usage_aggregator,
    record_usage,
)


def _check(name: str, ok: bool, detail: str = "") -> bool:
    label = "PASS" if ok else "FAIL"
    print(f"[{label}] {name}{(' - ' + detail) if detail and not ok else ''}")
    return ok


# ---------------------------------------------------------------------------
# Behaviour without a bound aggregator
# ---------------------------------------------------------------------------

def test_record_noop_when_unbound() -> bool:
    """No aggregator on the ContextVar → record_usage is a silent no-op."""
    try:
        record_usage("main_turn", {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15})
        return _check("record_usage no-ops when no aggregator bound", True)
    except Exception as e:
        return _check("record_usage no-ops when no aggregator bound", False, str(e))


def test_record_handles_none_and_empty() -> bool:
    """None / empty / partial dicts must not crash the call site."""
    agg: dict = {}
    token = current_usage_aggregator.set(agg)
    try:
        record_usage("x", None)
        record_usage("x", {})
        record_usage("x", {"prompt_tokens": None, "completion_tokens": None})
    finally:
        current_usage_aggregator.reset(token)
    # None/empty must produce no slot; partial-with-None should produce a
    # zero slot (the partial dict is truthy so it counts as a real call).
    return _check(
        "record_usage tolerates None and empty payloads",
        agg == {"x": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}},
        f"agg={agg}",
    )


# ---------------------------------------------------------------------------
# Accumulation
# ---------------------------------------------------------------------------

def test_record_accumulates_same_purpose() -> bool:
    """Multiple folds under the same purpose must sum, not overwrite —
    the whole point of fixing the last-wins bug from the audit."""
    agg: dict = {}
    token = current_usage_aggregator.set(agg)
    try:
        record_usage("main_turn", {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120})
        record_usage("main_turn", {"prompt_tokens": 80, "completion_tokens": 10, "total_tokens": 90})
        record_usage("main_turn", {"prompt_tokens": 50, "completion_tokens": 5, "total_tokens": 55})
    finally:
        current_usage_aggregator.reset(token)
    return _check(
        "record_usage sums repeated folds under the same purpose",
        agg["main_turn"] == {"prompt_tokens": 230, "completion_tokens": 35, "total_tokens": 265},
        f"agg={agg}",
    )


def test_record_keeps_purposes_separate() -> bool:
    agg: dict = {}
    token = current_usage_aggregator.set(agg)
    try:
        record_usage("main_turn", {"prompt_tokens": 100, "completion_tokens": 10, "total_tokens": 110})
        record_usage("wrap_up", {"prompt_tokens": 50, "completion_tokens": 5, "total_tokens": 55})
        record_usage("title", {"prompt_tokens": 8, "completion_tokens": 2, "total_tokens": 10})
    finally:
        current_usage_aggregator.reset(token)
    return _check(
        "record_usage keeps separate purposes in distinct slots",
        set(agg.keys()) == {"main_turn", "wrap_up", "title"}
        and agg["main_turn"]["total_tokens"] == 110
        and agg["wrap_up"]["total_tokens"] == 55
        and agg["title"]["total_tokens"] == 10,
    )


# ---------------------------------------------------------------------------
# total_tokens reconstruction
# ---------------------------------------------------------------------------

def test_record_reconstructs_total_tokens() -> bool:
    """Some vLLM builds omit total_tokens. The aggregator must reconstruct
    it from prompt + completion so downstream consumers always see a
    populated total."""
    agg: dict = {}
    token = current_usage_aggregator.set(agg)
    try:
        record_usage("main_turn", {"prompt_tokens": 100, "completion_tokens": 20})  # no total
    finally:
        current_usage_aggregator.reset(token)
    return _check(
        "record_usage reconstructs total_tokens when missing",
        agg["main_turn"]["total_tokens"] == 120,
        f"slot={agg.get('main_turn')}",
    )


def test_aggregate_reconstructs_total_tokens() -> bool:
    """Same fallback at the aggregate level — defends against an upstream
    that consistently reports zero totals on every call."""
    agg = {
        "main_turn": {"prompt_tokens": 50, "completion_tokens": 10, "total_tokens": 0},
        "wrap_up": {"prompt_tokens": 30, "completion_tokens": 5, "total_tokens": 0},
    }
    totals = aggregate_totals(agg)
    return _check(
        "aggregate_totals reconstructs total when all slots are zero",
        totals == {"prompt_tokens": 80, "completion_tokens": 15, "total_tokens": 95},
        f"totals={totals}",
    )


# ---------------------------------------------------------------------------
# aggregate_totals
# ---------------------------------------------------------------------------

def test_aggregate_sums_across_purposes() -> bool:
    agg = {
        "main_turn": {"prompt_tokens": 1000, "completion_tokens": 200, "total_tokens": 1200},
        "forced_required": {"prompt_tokens": 800, "completion_tokens": 40, "total_tokens": 840},
        "wrap_up": {"prompt_tokens": 500, "completion_tokens": 150, "total_tokens": 650},
        "title": {"prompt_tokens": 30, "completion_tokens": 5, "total_tokens": 35},
        "agent_turn": {"prompt_tokens": 600, "completion_tokens": 80, "total_tokens": 680},
        "agent_wrap_up": {"prompt_tokens": 200, "completion_tokens": 100, "total_tokens": 300},
        "summary": {"prompt_tokens": 400, "completion_tokens": 100, "total_tokens": 500},
    }
    totals = aggregate_totals(agg)
    expected = {
        "prompt_tokens": 1000 + 800 + 500 + 30 + 600 + 200 + 400,
        "completion_tokens": 200 + 40 + 150 + 5 + 80 + 100 + 100,
        "total_tokens": 1200 + 840 + 650 + 35 + 680 + 300 + 500,
    }
    return _check(
        "aggregate_totals sums each key across every purpose",
        totals == expected,
        f"got={totals}, expected={expected}",
    )


def test_aggregate_empty() -> bool:
    return _check(
        "aggregate_totals on empty agg returns zero dict",
        aggregate_totals({}) == {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    )


# ---------------------------------------------------------------------------
# End-to-end realistic scenario
# ---------------------------------------------------------------------------

def test_realistic_multi_call_scenario() -> bool:
    """Simulate the audit's worst-case turn: 3 main_turn streams, 2
    forced_required retries, 1 wrap_up, 1 title. Verify the totals are
    the sum of all calls — what the gateway should see, not just the
    last call."""
    agg: dict = {}
    token = current_usage_aggregator.set(agg)
    try:
        record_usage("main_turn", {"prompt_tokens": 5000, "completion_tokens": 300, "total_tokens": 5300})
        record_usage("main_turn", {"prompt_tokens": 5400, "completion_tokens": 280, "total_tokens": 5680})
        record_usage("main_turn", {"prompt_tokens": 5800, "completion_tokens": 50, "total_tokens": 5850})
        record_usage("forced_required", {"prompt_tokens": 5900, "completion_tokens": 30, "total_tokens": 5930})
        record_usage("forced_required", {"prompt_tokens": 5950, "completion_tokens": 25, "total_tokens": 5975})
        record_usage("wrap_up", {"prompt_tokens": 6000, "completion_tokens": 400, "total_tokens": 6400})
        record_usage("title", {"prompt_tokens": 80, "completion_tokens": 9, "total_tokens": 89})
        totals = aggregate_totals(agg)
    finally:
        current_usage_aggregator.reset(token)

    expected_total = 5300 + 5680 + 5850 + 5930 + 5975 + 6400 + 89
    return _check(
        "realistic 7-call turn produces correct cumulative total",
        totals["total_tokens"] == expected_total
        and agg["main_turn"]["total_tokens"] == 5300 + 5680 + 5850,
        f"totals={totals}, expected total_tokens={expected_total}",
    )


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

TESTS = [
    test_record_noop_when_unbound,
    test_record_handles_none_and_empty,
    test_record_accumulates_same_purpose,
    test_record_keeps_purposes_separate,
    test_record_reconstructs_total_tokens,
    test_aggregate_reconstructs_total_tokens,
    test_aggregate_sums_across_purposes,
    test_aggregate_empty,
    test_realistic_multi_call_scenario,
]


def main() -> int:
    passed = 0
    failed = 0
    for test in TESTS:
        try:
            ok = test()
        except Exception:
            ok = False
            print(f"[FAIL] {test.__name__} - exception:")
            traceback.print_exc()
        if ok:
            passed += 1
        else:
            failed += 1
    print(f"\n{passed} passed, {failed} failed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
