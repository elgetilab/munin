"""AgentRetriever — THE production retriever for Track A (the chat agent's
``paper_search`` path, KICKOFF Q3).

Ranking is copied from ``backend/retrieval/mcp/tools/papers.py::paper_search``
(the dedup-by-DOI / max-score / matched_by vote-fusion, final sort
``(-matched_by, -score)``, ``per_query = max(top_k, 5)``). The ONE deliberate
difference from production: the query variants are a FROZEN, committed set, not
a live LLM expansion. Track A measures the ranker on a fixed input; expansion
drift under a model swap is a live-harness behaviour measured by Track D/E, not
here. State this split in results.

``single_variant=True`` (base query only, no fan-out) is the T2 vanilla-RAG arm
form, so the arm2 -> arm3 comparison isolates the agent loop, not a retrieval
swap.
"""

from __future__ import annotations

from typing import Optional

from .. import config
from .base import Retriever


def _dedupe_key(doi: str) -> str:
    # Mirrors papers.py::_paper_dedupe_key (DOI-based; the eval corpus always
    # has DOIs, so the title/id fallbacks production carries aren't needed).
    return f"doi:{(doi or '').strip().lower()}"


class AgentRetriever(Retriever):
    name = "agent"

    def __init__(
        self,
        qdrant,
        specter,
        collection: str = "papers",
        frozen_variants: Optional[dict[str, list[str]]] = None,
        single_variant: bool = False,
    ):
        self.qdrant = qdrant
        self.specter = specter
        self.collection = collection
        # query (str) -> [base, variant1, ...]; base query is element 0.
        self.frozen_variants = frozen_variants or {}
        self.single_variant = single_variant

    def variants_for(self, query: str) -> list[str]:
        """The variant list this retriever will fan out. Single-variant mode
        (T2 arm) always uses just the base query; otherwise the frozen set,
        falling back to [query] if the query isn't in the frozen file."""
        if self.single_variant:
            return [query]
        variants = self.frozen_variants.get(query)
        if not variants:
            return [query]
        return list(variants)

    def _search_one(self, q: str, per_query: int) -> list[tuple[str, float]]:
        vec = self.specter.encode(q).tolist()
        results = self.qdrant.query_points(
            collection_name=self.collection,
            query=vec,
            limit=per_query,
            query_filter=None,  # Track A queries carry no tag scope
        )
        out: list[tuple[str, float]] = []
        for r in results.points:
            doi = ((r.payload or {}).get("doi") or "").strip()
            if doi:
                out.append((doi, float(r.score)))
        return out

    def retrieve(self, query: str, top_k: int = 10) -> list[tuple[str, float]]:
        query_list = self.variants_for(query)
        per_query = max(top_k, config.AGENT_PER_QUERY_FLOOR)

        # Vote-fusion, verbatim from paper_search: dedupe by DOI, keep max
        # score, count matched_by, sort (-matched_by, -score).
        seen: dict[str, dict] = {}
        for q in query_list:
            for doi, score in self._search_one(q, per_query):
                key = _dedupe_key(doi)
                existing = seen.get(key)
                if existing is None:
                    seen[key] = {"doi": doi, "score": score, "matched_by": 1}
                else:
                    existing["matched_by"] += 1
                    if score > existing["score"]:
                        existing["score"] = score

        merged = sorted(
            seen.values(),
            key=lambda r: (-r["matched_by"], -r["score"]),
        )
        return [(r["doi"], r["score"]) for r in merged[:top_k]]
