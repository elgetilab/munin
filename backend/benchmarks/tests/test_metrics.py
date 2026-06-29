"""Gold-value tests for the IR metrics. Hand-computed expected values are
authoritative; where ``ranx`` is importable a couple of cases are
cross-checked against it (skipped otherwise, never a hard dependency).
"""

import math

import pytest

from munin_bench.metrics import hits_at_k, mrr, ndcg_at_k, recall_at_k

try:  # optional cross-check only
    import ranx  # noqa: F401

    HAVE_RANX = True
except Exception:  # pragma: no cover - environment dependent
    HAVE_RANX = False


# --------------------------------------------------------------------------
# ndcg_at_k
# --------------------------------------------------------------------------
def test_ndcg_spec_worked_example():
    # DCG = 2 + 0 + 1/log2(4) = 2.5 ; IDCG = 2 + 1/log2(3).
    # The spec rounds this to "~0.9501"; the exact value is 0.950234, which
    # is what we assert (the spec's two-place round was loose).
    dcg = 2.5
    idcg = 2 + 1 / math.log2(3)
    val = ndcg_at_k(["a", "b", "c"], {"a": 2, "c": 1}, k=3)
    assert val == pytest.approx(dcg / idcg, abs=1e-12)
    assert val == pytest.approx(0.950234, abs=1e-6)


def test_ndcg_perfect_ranking_is_one():
    val = ndcg_at_k(["a", "c", "b"], {"a": 2, "c": 1}, k=3)
    assert val == pytest.approx(1.0, abs=1e-12)


def test_ndcg_no_relevant_docs_is_zero():
    assert ndcg_at_k(["a", "b"], {}, k=3) == 0.0
    assert ndcg_at_k(["a", "b"], {"a": 0, "b": 0}, k=3) == 0.0


def test_ndcg_truncates_at_k():
    # The single relevant doc sits at rank 3, beyond k=2 -> DCG 0 -> nDCG 0.
    assert ndcg_at_k(["x", "y", "a"], {"a": 3}, k=2) == 0.0
    # At k=3 it counts: DCG = 3/log2(4) = 1.5, IDCG = 3/log2(2) = 3 -> 0.5
    assert ndcg_at_k(["x", "y", "a"], {"a": 3}, k=3) == pytest.approx(0.5)


def test_ndcg_graded_three_levels():
    # ranked grades: 3, 1, 2 ; DCG = 3 + 1/log2(3) + 2/log2(4)
    dcg = 3 + 1 / math.log2(3) + 2 / math.log2(4)
    # ideal grades 3,2,1 ; IDCG = 3 + 2/log2(3) + 1/log2(4)
    idcg = 3 + 2 / math.log2(3) + 1 / math.log2(4)
    val = ndcg_at_k(["a", "b", "c"], {"a": 3, "b": 1, "c": 2}, k=3)
    assert val == pytest.approx(dcg / idcg)


def test_ndcg_k_zero_is_zero():
    assert ndcg_at_k(["a"], {"a": 2}, k=0) == 0.0


# --------------------------------------------------------------------------
# recall_at_k
# --------------------------------------------------------------------------
def test_recall_basic():
    assert recall_at_k(["a", "b", "c"], {"a", "d"}, k=3) == pytest.approx(0.5)


def test_recall_all_found():
    assert recall_at_k(["a", "b", "c"], {"a", "b"}, k=3) == pytest.approx(1.0)


def test_recall_respects_cutoff():
    assert recall_at_k(["x", "y", "a"], {"a"}, k=2) == 0.0
    assert recall_at_k(["x", "y", "a"], {"a"}, k=3) == pytest.approx(1.0)


def test_recall_empty_relevant_is_zero():
    assert recall_at_k(["a", "b"], set(), k=3) == 0.0


# --------------------------------------------------------------------------
# mrr
# --------------------------------------------------------------------------
def test_mrr_first_relevant_at_rank_two():
    assert mrr(["x", "a", "b"], {"a", "b"}) == pytest.approx(0.5)


def test_mrr_first_position():
    assert mrr(["a", "b"], {"a"}) == pytest.approx(1.0)


def test_mrr_none_relevant():
    assert mrr(["x", "y"], {"a"}) == 0.0
    assert mrr(["x", "y"], set()) == 0.0


# --------------------------------------------------------------------------
# hits_at_k
# --------------------------------------------------------------------------
def test_hits_present_within_k():
    assert hits_at_k(["x", "a", "y"], {"a"}, k=2) == 1.0


def test_hits_absent_within_k():
    assert hits_at_k(["x", "y", "a"], {"a"}, k=2) == 0.0


def test_hits_empty_relevant():
    assert hits_at_k(["a"], set(), k=3) == 0.0


# --------------------------------------------------------------------------
# optional ranx cross-check
# --------------------------------------------------------------------------
@pytest.mark.skipif(not HAVE_RANX, reason="ranx not installed")
def test_ndcg_matches_ranx():
    from ranx import Qrels, Run, evaluate

    qrels = Qrels({"q1": {"a": 2, "c": 1}})
    run = Run({"q1": {"a": 3.0, "b": 2.0, "c": 1.0}})
    expected = evaluate(qrels, run, "ndcg@3")
    assert ndcg_at_k(["a", "b", "c"], {"a": 2, "c": 1}, k=3) == pytest.approx(
        expected, abs=1e-6
    )
