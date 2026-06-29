"""Single-query SPECTER dense retriever over a Qdrant collection.

Mirrors the production ``_qdrant_search_one`` path: SPECTER-encode the query,
``query_points`` against the collection, return ``(doi, score)``. Dedupes by
DOI keeping the best score (SPECTER is one-vector-per-paper, so collisions
shouldn't happen, but production checks and so do we)."""

from __future__ import annotations

from typing import Optional

from .base import Retriever


class SpecterDenseRetriever(Retriever):
    name = "specter_dense"

    def __init__(self, qdrant, specter, collection: str = "papers",
                 id_field: str = "doi"):
        self.qdrant = qdrant
        self.specter = specter
        self.collection = collection
        # Production "papers" keys ids under "doi"; BEIR eval_* collections
        # key them under "doc_id". A retriever instance is bound to one corpus.
        self.id_field = id_field

    def _search(self, query: str, limit: int, query_filter=None) -> list[tuple[str, float]]:
        vec = self.specter.encode(query).tolist()
        results = self.qdrant.query_points(
            collection_name=self.collection,
            query=vec,
            limit=limit,
            query_filter=query_filter,
        )
        best: dict[str, float] = {}
        order: list[str] = []
        for r in results.points:
            payload = r.payload or {}
            doi = (payload.get(self.id_field) or "").strip()
            if not doi:
                continue
            score = float(r.score)
            if doi not in best:
                best[doi] = score
                order.append(doi)
            elif score > best[doi]:
                best[doi] = score
        ranked = sorted(order, key=lambda d: best[d], reverse=True)
        return [(d, best[d]) for d in ranked]

    def retrieve(
        self, query: str, top_k: int = 10, query_filter=None
    ) -> list[tuple[str, float]]:
        return self._search(query, top_k, query_filter)[:top_k]
