"""TwoHopCheckRetriever — RRF hybrid with 2-hop graph expansion instead of
1-hop. Expected to DEGRADE vs the 1-hop RRF: two hops pulls in citation
neighbours-of-neighbours, which are mostly topical noise. This is the
degradation-check configuration the paper promises; building it lets us
report the 1-hop-vs-2-hop gap rather than assert it."""

from __future__ import annotations

from .base import Retriever
from .rrf_hybrid import RRFHybridRetriever


class TwoHopCheckRetriever(Retriever):
    name = "two_hop_check"

    def __init__(self, rrf_retriever: RRFHybridRetriever):
        # Reuse the 1-hop retriever's wiring (same dense, graph, k, seed_n)
        # but expand two hops out.
        self._rrf2 = RRFHybridRetriever(
            dense=rrf_retriever.dense,
            neo4j_driver=rrf_retriever.neo4j,
            k=rrf_retriever.k,
            seed_n=rrf_retriever.seed_n,
            hops=2,
        )

    def retrieve(self, query: str, top_k: int = 10) -> list[tuple[str, float]]:
        return self._rrf2.retrieve(query, top_k=top_k)
