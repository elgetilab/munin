"""CitationRerankRetriever score-math test. Mocks the dense retriever and the
Neo4j citation lookup (three papers, counts 0 / 10 / 1000) and verifies the
combined score matches the production formula to 4 decimals."""

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


def _make(weights=(0.8, 0.2)):
    r = CitationRerankRetriever(
        _FakeDense(), neo4j_driver=None,
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


def test_compute_citation_score_formula():
    assert compute_citation_score(0, 1000) == 0.0
    assert compute_citation_score(10, 0) == 0.0  # max<=0 guard
    expected = math.log1p(10) / math.log1p(1000)
    assert compute_citation_score(10, 1000) == pytest.approx(expected, abs=1e-12)
    assert compute_citation_score(1000, 1000) == pytest.approx(1.0)


def test_citation_rerank_combined_scores_and_order():
    r = _make(weights=(0.8, 0.2))
    out = dict(r.retrieve("q", top_k=3))

    max_cit = 1000
    norm_v, norm_c = 0.8, 0.2
    exp_a = norm_v * 0.9 + norm_c * compute_citation_score(0, max_cit)
    exp_b = norm_v * 0.8 + norm_c * compute_citation_score(10, max_cit)
    exp_c = norm_v * 0.7 + norm_c * compute_citation_score(1000, max_cit)

    assert out["10.1/a"] == pytest.approx(exp_a, abs=1e-4)
    assert out["10.1/b"] == pytest.approx(exp_b, abs=1e-4)
    assert out["10.1/c"] == pytest.approx(exp_c, abs=1e-4)

    # citation boost lifts c above a above b (0.76 > 0.72 > 0.7094)
    ranked = [doi for doi, _ in r.retrieve("q", top_k=3)]
    assert ranked == ["10.1/c", "10.1/a", "10.1/b"]


def test_citation_rerank_weights_change_ranking():
    # With heavy citation weight, the 1000-citation paper dominates outright.
    r = _make(weights=(0.5, 0.5))
    ranked = [doi for doi, _ in r.retrieve("q", top_k=3)]
    assert ranked[0] == "10.1/c"


def test_citation_rerank_empty_dense_returns_empty():
    r = CitationRerankRetriever(
        type("E", (), {"retrieve": lambda self, q, top_k=10: []})(),
        neo4j_driver=None,
    )
    assert r.retrieve("q", top_k=5) == []
