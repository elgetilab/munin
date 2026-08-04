"""Track C2b confidence intervals on the answerable subset.

Pure-function tests over synthetic verdict dicts: no corpus, no shadow
instance, no network. Guards the three things that are easy to get silently
wrong when a proportion is computed over a subset that is itself a random
outcome.
"""

import pytest

from munin_bench.abstention.run_c2 import (
    _correct_abstention_delta,
    _paired,
    _prop_ci,
    _unconditional_ci,
)


def _verdicts(spec: dict) -> dict:
    """{qid: verdict-string} -> the shape the scorer consumes."""
    return {q: {"qid": q, "verdict": v} for q, v in spec.items()}


def _pair(n_correct_present: int, absent_pattern: list[str], n_total: int = 50):
    """Build a present/absent pair. The first `n_correct_present` questions are
    answerable; `absent_pattern` gives the absent-arm verdict for each of them."""
    assert len(absent_pattern) == n_correct_present
    present, absent = {}, {}
    for i in range(n_total):
        q = f"q{i:03d}"
        if i < n_correct_present:
            present[q] = "correct"
            absent[q] = absent_pattern[i]
        else:
            present[q] = "abstain"
            absent[q] = "abstain"
    return _verdicts(present), _verdicts(absent)


# --- _prop_ci ---------------------------------------------------------------

def test_prop_ci_brackets_the_point_estimate():
    out = _prop_ci([1] * 18 + [0] * 9)          # 18/27, the real C2 subset shape
    assert out["n"] == 27
    assert out["rate"] == pytest.approx(18 / 27)
    for kind in ("bootstrap", "wilson"):
        assert out[kind]["ci_low"] <= out["rate"] <= out[kind]["ci_high"]


def test_prop_ci_is_seeded_and_reproducible():
    flags = [1] * 18 + [0] * 9
    assert _prop_ci(flags) == _prop_ci(flags)


def test_wilson_stays_inside_the_unit_interval_at_the_extremes():
    """The bootstrap degenerates to a point at p=0 and p=1 (every resample
    agrees), which is exactly where Wilson has to carry the interval and where
    a naive normal approximation would run off the end of [0, 1]."""
    for flags in ([0] * 20, [1] * 20):
        out = _prop_ci(flags)
        assert out["bootstrap"]["ci_low"] == out["bootstrap"]["ci_high"] == out["rate"]
        assert 0.0 <= out["wilson"]["ci_low"] <= out["wilson"]["ci_high"] <= 1.0
        assert out["wilson"]["ci_low"] < out["wilson"]["ci_high"]   # not degenerate


def test_prop_ci_empty_is_none_not_a_crash():
    assert _prop_ci([]) is None


def test_ci_narrows_as_n_grows():
    def width(n):
        o = _prop_ci([1] * (2 * n // 3) + [0] * (n - 2 * n // 3))
        return o["wilson"]["ci_high"] - o["wilson"]["ci_low"]
    assert width(27) > width(270) > width(2700)


# --- _unconditional_ci ------------------------------------------------------

def test_unconditional_ci_tracks_the_conditional_one():
    """Resampling WHICH items are answerable does not blow the interval up.

    It is a ratio estimator, so numerator and denominator co-vary and the extra
    membership variance largely cancels. The useful property to pin is that the
    two agree closely: that is what makes conditioning on the observed subset
    defensible. An earlier version of this test asserted the unconditional
    interval was strictly wider, which is false on both synthetic and real
    data."""
    present, absent = _pair(27, ["abstain"] * 18 + ["correct"] * 4 + ["incorrect"] * 5)
    qids = list(present)
    cond = _prop_ci([1 if absent[q]["verdict"] == "abstain" else 0
                     for q in qids if present[q]["verdict"] == "correct"])
    uncond = _unconditional_ci(present, absent, qids)
    assert uncond["ci_low"] <= 18 / 27 <= uncond["ci_high"]
    assert uncond["ci_low"] == pytest.approx(cond["bootstrap"]["ci_low"], abs=0.10)
    assert uncond["ci_high"] == pytest.approx(cond["bootstrap"]["ci_high"], abs=0.10)
    assert uncond["empty_resamples"] == 0


def test_unconditional_ci_counts_empty_resamples_instead_of_dividing_by_zero():
    """One answerable question out of 50: most resamples miss it entirely, and
    a rate over an empty subset is undefined rather than 0."""
    present, absent = _pair(1, ["abstain"])
    out = _unconditional_ci(present, absent, list(present))
    assert out is not None
    assert out["empty_resamples"] > 0
    assert 0.0 <= out["ci_low"] <= out["ci_high"] <= 1.0


# --- _correct_abstention_delta ----------------------------------------------

def test_delta_recovers_a_known_shift_and_calls_it_significant():
    old_p, old_a = _pair(20, ["abstain"] * 4 + ["correct"] * 12 + ["incorrect"] * 4)
    new_p, new_a = _pair(27, ["abstain"] * 18 + ["correct"] * 4 + ["incorrect"] * 5)
    out = _correct_abstention_delta((old_p, old_a), (new_p, new_a))
    assert out["old_rate"] == pytest.approx(0.20)
    assert out["new_rate"] == pytest.approx(18 / 27)
    assert out["delta"] == pytest.approx(18 / 27 - 0.20)
    assert out["ci_low"] > 0.0                    # excludes "no change"
    assert out["p_value_two_sided"] < 0.05
    assert out["n_paired_questions"] == 50


def test_delta_on_identical_runs_is_zero_and_not_significant():
    p, a = _pair(27, ["abstain"] * 18 + ["correct"] * 4 + ["incorrect"] * 5)
    out = _correct_abstention_delta((p, a), (p, a))
    assert out["delta"] == pytest.approx(0.0)
    assert out["ci_low"] == out["ci_high"] == pytest.approx(0.0)
    assert out["p_value_two_sided"] == pytest.approx(1.0)


def test_delta_needs_shared_questions():
    p1, a1 = _pair(5, ["abstain"] * 5, n_total=10)
    p2 = _verdicts({f"z{i}": "correct" for i in range(10)})
    a2 = _verdicts({f"z{i}": "abstain" for i in range(10)})
    assert _correct_abstention_delta((p1, a1), (p2, a2)) is None


# --- end-to-end scorecard shape ---------------------------------------------

def test_paired_scorecard_carries_every_ci_and_stays_consistent():
    present, absent = _pair(27, ["abstain"] * 18 + ["correct"] * 4 + ["incorrect"] * 5)
    sc = _paired(present, absent, "2026-01-01", git_sha="deadbee")

    assert sc["answerable_n"] == 27
    assert sc["git_sha"] == "deadbee"            # capture-time sha preserved, not HEAD
    for arm in ("present", "absent"):
        assert sc[arm]["accuracy_ci"]["n"] == 50
        assert sc[arm]["abstain_rate_ci"]["n"] == 50

    sub = sc["on_answerable_when_source_removed"]
    assert sub["correct_abstention"] + sub["answered_still_correct"] + sub["answered_now_wrong"] == 27
    # The three marginal rates are a multinomial over the same 27 items.
    total = sum(sub[k]["rate"] for k in ("correct_abstention_rate_ci",
                                         "answered_still_correct_rate_ci",
                                         "answered_now_wrong_rate_ci"))
    assert total == pytest.approx(1.0)
    assert sub["correct_abstention_rate_ci"]["rate"] == pytest.approx(sub["correct_abstention_rate"])
    assert sub["correct_abstention_rate_ci_unconditional"] is not None
