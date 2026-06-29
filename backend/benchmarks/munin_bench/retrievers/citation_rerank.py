"""CitationRerankRetriever = the /search/hybrid path (SPECTER dense + Neo4j
citation re-rank). This is the SEARCH-PAGE configuration, not the chat agent's
production path (that's AgentRetriever); the gap between them is a reported
finding (KICKOFF Q3).

``compute_citation_score``, the ``fetch_k = min(top_k*3, 100)`` rule, the
weight normalisation, and the citation-count Cypher are copied verbatim from
``backend/retrieval/main.py`` so any production bugfix is reflected here. If
production diverges from this, production wins: update it here + tell varghele.
"""

from __future__ import annotations

import math

from .. import config
from .base import Retriever


def compute_citation_score(citation_count: int, max_citations: int) -> float:
    """VERBATIM from main.py::compute_citation_score (log-scaled, [0,1])."""
    if max_citations <= 0 or citation_count <= 0:
        return 0.0
    log_count = math.log1p(citation_count)
    log_max = math.log1p(max_citations)
    return min(1.0, log_count / log_max) if log_max > 0 else 0.0


class CitationRerankRetriever(Retriever):
    name = "citation_rerank"

    def __init__(
        self,
        dense,
        neo4j_driver,
        vector_weight: float = 0.8,
        citation_weight: float = 0.2,
    ):
        self.dense = dense
        self.neo4j = neo4j_driver
        self.vector_weight = vector_weight
        self.citation_weight = citation_weight

    def _citation_counts(self, dois: list[str]) -> dict[str, dict]:
        """VERBATIM Cypher from main.py::get_citation_counts. Isolated as a
        method so unit tests can substitute canned counts without a driver."""
        if not self.neo4j or not dois:
            return {}
        with self.neo4j.session() as session:
            result = session.run(
                """
                UNWIND $dois AS doi
                OPTIONAL MATCH (p:Paper {doi: doi})
                OPTIONAL MATCH (citing:Paper)-[:CITES]->(p)
                OPTIONAL MATCH (p)-[:CITES]->(referenced:Paper)
                RETURN doi,
                       count(DISTINCT citing) as citation_count,
                       count(DISTINCT referenced) as reference_count
                """,
                dois=dois,
            )
            return {
                r["doi"]: {
                    "citation_count": r["citation_count"],
                    "reference_count": r["reference_count"],
                }
                for r in result
                if r["doi"]
            }

    def retrieve(self, query: str, top_k: int = 10) -> list[tuple[str, float]]:
        fetch_k = min(top_k * config.FETCH_K_MULT, config.FETCH_K_CAP)
        dense_hits = self.dense.retrieve(query, top_k=fetch_k)
        if not dense_hits:
            return []
        dois = [doi for doi, _ in dense_hits]
        counts = self._citation_counts(dois)

        # main.py: max over the fetched pool, default 1.
        max_citations = max(
            (c.get("citation_count", 0) for c in counts.values()), default=1
        )
        total_weight = self.vector_weight + self.citation_weight
        norm_v = self.vector_weight / total_weight
        norm_c = self.citation_weight / total_weight

        rescored: list[tuple[str, float]] = []
        for doi, vector_score in dense_hits:
            citation_count = counts.get(doi, {}).get("citation_count", 0)
            citation_score = compute_citation_score(citation_count, max_citations)
            combined = norm_v * float(vector_score) + norm_c * citation_score
            rescored.append((doi, combined))

        rescored.sort(key=lambda x: x[1], reverse=True)
        return rescored[:top_k]
