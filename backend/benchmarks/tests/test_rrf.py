"""Reciprocal Rank Fusion math tests (known outputs)."""

import pytest

from munin_bench.retrievers.rrf_hybrid import rrf_fuse


def test_rrf_known_scores():
    fused = dict(rrf_fuse([["a", "b", "c"], ["b", "d"]], k=60))
    assert fused["a"] == pytest.approx(1 / 61)
    assert fused["b"] == pytest.approx(1 / 62 + 1 / 61)
    assert fused["c"] == pytest.approx(1 / 63)
    assert fused["d"] == pytest.approx(1 / 62)


def test_rrf_order():
    order = [doc for doc, _ in rrf_fuse([["a", "b", "c"], ["b", "d"]], k=60)]
    # b appears in both -> highest; then a (rank1 in list1) > d (rank2) > c (rank3)
    assert order == ["b", "a", "d", "c"]


def test_rrf_single_ranking_is_passthrough_order():
    order = [doc for doc, _ in rrf_fuse([["x", "y", "z"]], k=60)]
    assert order == ["x", "y", "z"]


def test_rrf_tie_breaks_on_best_rank_then_id():
    # u and v each appear once at rank 1 in separate lists -> equal score.
    # Tie-break: equal best_rank (1) -> doc id ascending.
    fused = rrf_fuse([["v"], ["u"]], k=60)
    assert [doc for doc, _ in fused] == ["u", "v"]


def test_rrf_k_changes_magnitude_not_relative_order():
    a = dict(rrf_fuse([["a", "b"]], k=10))
    b = dict(rrf_fuse([["a", "b"]], k=60))
    assert a["a"] > a["b"]
    assert b["a"] > b["b"]
    assert a["a"] > b["a"]  # smaller k -> larger reciprocal
