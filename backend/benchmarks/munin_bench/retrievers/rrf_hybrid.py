"""RRFHybridRetriever — the FUTURE-WORK retriever: SPECTER dense fused with a
1-hop citation-graph expansion via Reciprocal Rank Fusion.

RRF (not a weighted sum) because it's the published methodology for combining
rankings of incomparable score scales (Cormack et al. 2009, SIGIR): a document
scores ``sum_r 1/(k + rank_r(d))`` over the rankings it appears in, ``k=60``.
The dense ranking and the graph-expansion ranking live on totally different
scales (cosine vs. co-citation frequency), which is exactly the case RRF is
for.
"""

from __future__ import annotations

from .. import config
from .base import Retriever


def rrf_fuse(rankings: list[list[str]], k: int = 60) -> list[tuple[str, float]]:
    """Reciprocal Rank Fusion over several ranked id lists (best-first).

    ``score(d) = sum over rankings of 1/(k + rank)`` with ``rank`` 1-indexed.
    Ties broken by best (lowest) rank seen, then doc id, for determinism.
    """
    scores: dict[str, float] = {}
    best_rank: dict[str, int] = {}
    for ranking in rankings:
        for rank, doc_id in enumerate(ranking, start=1):
            scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (k + rank)
            if doc_id not in best_rank or rank < best_rank[doc_id]:
                best_rank[doc_id] = rank
    return sorted(
        scores.items(), key=lambda x: (-x[1], best_rank[x[0]], x[0])
    )


class RRFHybridRetriever(Retriever):
    name = "rrf_hybrid"

    def __init__(self, dense, neo4j_driver, k: int = config.RRF_K,
                 seed_n: int = config.RRF_SEED_N, hops: int = 1):
        self.dense = dense
        self.neo4j = neo4j_driver
        self.k = k
        self.seed_n = seed_n
        self.hops = hops

    def _expand_neighbors(self, seeds: list[str]) -> list[str]:
        """Citation neighbours of the seed DOIs, ordered by how many seeds
        connect to each (co-citation frequency). ``hops`` is a controlled
        int (1 or 2), baked into the pattern length literal (Cypher can't
        parameterise variable-length bounds)."""
        if not self.neo4j or not seeds:
            return []
        pattern = f"-[:CITES*1..{int(self.hops)}]-"
        query = (
            "UNWIND $dois AS doi "
            "MATCH (p:Paper {doi: doi})" + pattern + "(nb:Paper) "
            "WHERE nb.doi IS NOT NULL AND NOT nb.doi IN $dois "
            "RETURN nb.doi AS doi, count(*) AS freq "
            "ORDER BY freq DESC"
        )
        with self.neo4j.session() as session:
            result = session.run(query, dois=seeds)
            return [r["doi"] for r in result]

    def retrieve(self, query: str, top_k: int = 10) -> list[tuple[str, float]]:
        fetch_k = min(top_k * config.FETCH_K_MULT, config.FETCH_K_CAP)
        dense_hits = self.dense.retrieve(query, top_k=fetch_k)
        dense_ranking = [doi for doi, _ in dense_hits]
        if not dense_ranking:
            return []
        seeds = dense_ranking[: self.seed_n]
        expansion_ranking = self._expand_neighbors(seeds)
        fused = rrf_fuse([dense_ranking, expansion_ranking], k=self.k)
        return fused[:top_k]
