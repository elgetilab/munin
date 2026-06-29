"""Common retriever interface. Every retriever returns ``(doc_id, score)``
pairs in rank order (best first). ``doc_id`` is a DOI for the Munin corpus and
the BEIR doc id for BEIR subsets; a retriever instance is bound to exactly one
corpus and must never mix the two (RETRIEVAL-EVAL-SPEC Phase 2)."""

from __future__ import annotations

from abc import ABC, abstractmethod


class Retriever(ABC):
    name: str

    @abstractmethod
    def retrieve(self, query: str, top_k: int = 10) -> list[tuple[str, float]]:
        """Return ranked ``(doc_id, score)`` pairs, best first."""

    def retrieve_batch(
        self, queries: list[str], top_k: int = 10
    ) -> list[list[tuple[str, float]]]:
        """Default: loop. Override for efficiency where it matters."""
        return [self.retrieve(q, top_k) for q in queries]
