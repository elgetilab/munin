"""CitationRerankRetriever score-math test. Mocks the dense retriever and the
Neo4j citation lookup (three papers, counts 0 / 10 / 1000) and verifies the
combined score matches the production formula to 4 decimals.

UPDATED 2026-07-28 for the score-normalisation fix. These tests previously
asserted the UNNORMALISED formula, i.e. they encoded the bug: raw cosine scores
(clustered in a narrow band) were mixed with citation scores spanning the full
[0,1], so the citation term dominated the ranking regardless of the nominal
weights. Measured on LitSearch that cost -0.368 nDCG@10 (0.117 vs 0.485 for
dense alone). ``main.py::hybrid_search`` and this retriever now min-max
normalise the vector score within the fetched pool first.
"""

import math

import pytest

from munin_bench.retrievers.citation_rerank import (
    CitationRerankRetriever,
    compute_citation_score,
)


class _FakeDense:
    def retrieve(self, query, top_k=10):
        # vector scores, already ranked
        return [("10.1/a", 0.9), ("10.1/b", 0.8), ("10.1/c", 0.7)][:top_k]


def _make(weights=(0.8, 0.2), dense=None):
    r = CitationRerankRetriever(
        dense or _FakeDense(), neo4j_driver=None,
        vector_weight=weights[0], citation_weight=weights[1],
    )
    # Substitute canned citation counts (bypass the driver).
    counts = {
        "10.1/a": {"citation_count": 0, "reference_count": 0},
        "10.1/b": {"citation_count": 10, "reference_count": 3},
        "10.1/c": {"citation_count": 1000, "reference_count": 50},
    }
    r._citation_counts = lambda dois: {d: counts[d] for d in dois if d in counts}
    return r


# Pool min-max over the fixture's vector scores: (0.9, 0.8, 0.7) -> (1.0, 0.5, 0.0)
_VN = {"10.1/a": 1.0, "10.1/b": 0.5, "10.1/c": 0.0}
_CS = {
    "10.1/a": 0.0,
    "10.1/b": math.log1p(10) / math.log1p(1000),   # ~0.34708
    "10.1/c": 1.0,
}


def test_compute_citation_score_formula():
    assert compute_citation_score(0, 1000) == 0.0
    assert compute_citation_score(10, 0) == 0.0  # max<=0 guard
    expected = math.log1p(10) / math.log1p(1000)
    assert compute_citation_score(10, 1000) == pytest.approx(expected, abs=1e-12)
    assert compute_citation_score(1000, 1000) == pytest.approx(1.0)


def test_vector_score_is_pool_normalised_before_mixing():
    r = _make(weights=(0.8, 0.2))
    out = dict(r.retrieve("q", top_k=3))
    for doi in _VN:
        expected = 0.8 * _VN[doi] + 0.2 * _CS[doi]
        assert out[doi] == pytest.approx(expected, abs=1e-4)
    # a=0.80, b=0.4694, c=0.20 -> relevance leads, as the weights intend.
    assert [d for d, _ in r.retrieve("q", top_k=3)] == ["10.1/a", "10.1/b", "10.1/c"]


def test_production_weights_are_relevance_first_regression():
    """The bug this fix addresses, at the documented production 0.7/0.3.

    UNNORMALISED the ranking inverted completely — a=0.63, b=0.664, c=0.79,
    so the zero-relevance/1000-citation paper won and the best-matching paper
    came last. Normalised: a=0.70, b=0.454, c=0.30. If this test ever flips
    back to c-first, the normalisation has been lost again.
    """
    r = _make(weights=(0.7, 0.3))
    ranked = [doi for doi, _ in r.retrieve("q", top_k=3)]
    assert ranked == ["10.1/a", "10.1/b", "10.1/c"]
    out = dict(r.retrieve("q", top_k=3))
    assert out["10.1/a"] == pytest.approx(0.70, abs=1e-4)
    assert out["10.1/c"] == pytest.approx(0.30, abs=1e-4)


def test_citation_can_still_win_when_weighted_to():
    """Citations are not neutered — they just no longer win by default.

    At 0.5/0.5 the extremes tie exactly (0.5 each), because normalised
    relevance runs 1->0 while citation runs 0->1 across the same pool. Past
    that crossover the citation term leads.
    """
    tie = dict(_make(weights=(0.5, 0.5)).retrieve("q", top_k=3))
    assert tie["10.1/a"] == pytest.approx(tie["10.1/c"], abs=1e-4)

    r = _make(weights=(0.3, 0.7))
    assert [d for d, _ in r.retrieve("q", top_k=3)][0] == "10.1/c"


def test_degenerate_pool_does_not_divide_by_zero():
    """All-identical vector scores => zero range. Every paper gets
    vector_norm 1.0, so ranking falls back to the citation term."""
    class _Flat:
        def retrieve(self, query, top_k=10):
            return [("10.1/a", 0.5), ("10.1/b", 0.5), ("10.1/c", 0.5)][:top_k]

    r = _make(weights=(0.7, 0.3), dense=_Flat())
    out = dict(r.retrieve("q", top_k=3))
    for doi in _CS:
        assert out[doi] == pytest.approx(0.7 * 1.0 + 0.3 * _CS[doi], abs=1e-4)
    assert [d for d, _ in r.retrieve("q", top_k=3)][0] == "10.1/c"


def test_citation_rerank_empty_dense_returns_empty():
    r = CitationRerankRetriever(
        type("E", (), {"retrieve": lambda self, q, top_k=10: []})(),
        neo4j_driver=None,
    )
    assert r.retrieve("q", top_k=5) == []
