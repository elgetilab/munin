"""BM25 lexical retriever (rank_bm25.BM25Okapi) over a corpus of
``(doc_id, text)`` pairs. Tokenisation is lowercase alphanumeric runs
(``[a-z0-9]+``) — i.e. ``lower()`` split on non-alnum, which is the robust
form of the spec's "lower().split() plus alnum filter" (a literal
``str.split()`` would drop every word glued to punctuation).

The tokenised corpus + doc-id list are pickled so re-loads skip re-tokenising
the whole corpus.
"""

from __future__ import annotations

import os
import pickle
import re
from typing import Iterable, Iterator, Optional

from .base import Retriever

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def tokenize(text: str) -> list[str]:
    return _TOKEN_RE.findall((text or "").lower())


class BM25Retriever(Retriever):
    name = "bm25"

    def __init__(
        self,
        corpus_iter: Optional[Iterable[tuple[str, str]]] = None,
        *,
        doc_ids: Optional[list[str]] = None,
        tokenized: Optional[list[list[str]]] = None,
    ):
        from rank_bm25 import BM25Okapi

        if tokenized is not None and doc_ids is not None:
            self.doc_ids = doc_ids
            self._tokenized = tokenized
        elif corpus_iter is not None:
            self.doc_ids = []
            self._tokenized = []
            for doc_id, text in corpus_iter:
                self.doc_ids.append(doc_id)
                self._tokenized.append(tokenize(text))
        else:
            raise ValueError("BM25Retriever needs corpus_iter or (doc_ids, tokenized)")

        if not self._tokenized:
            raise ValueError("BM25Retriever: empty corpus")
        self._bm25 = BM25Okapi(self._tokenized)

    # -- persistence ---------------------------------------------------------
    def save(self, cache_path: str) -> None:
        os.makedirs(os.path.dirname(cache_path) or ".", exist_ok=True)
        with open(cache_path, "wb") as fh:
            pickle.dump({"doc_ids": self.doc_ids, "tokenized": self._tokenized}, fh)

    @classmethod
    def load(cls, cache_path: str) -> "BM25Retriever":
        with open(cache_path, "rb") as fh:
            blob = pickle.load(fh)
        return cls(doc_ids=blob["doc_ids"], tokenized=blob["tokenized"])

    @classmethod
    def from_cache_or_build(
        cls, corpus_iter: Iterable[tuple[str, str]], cache_path: str
    ) -> "BM25Retriever":
        if os.path.exists(cache_path):
            return cls.load(cache_path)
        inst = cls(corpus_iter)
        inst.save(cache_path)
        return inst

    # -- retrieval -----------------------------------------------------------
    def retrieve(self, query: str, top_k: int = 10) -> list[tuple[str, float]]:
        q_tokens = tokenize(query)
        if not q_tokens:
            return []
        scores = self._bm25.get_scores(q_tokens)
        ranked = sorted(
            zip(self.doc_ids, scores), key=lambda x: x[1], reverse=True
        )
        return [(doc_id, float(score)) for doc_id, score in ranked[:top_k]]


def iter_qdrant_corpus(
    qdrant, collection: str, batch: int = 512
) -> Iterator[tuple[str, str]]:
    """Scroll a Qdrant collection yielding ``(doi, title + abstract)`` for BM25.

    Matches SPECTER's title+abstract document construction (Cohan et al. 2020).
    Papers without a DOI are skipped (DOI is the corpus doc_id)."""
    offset = None
    while True:
        points, offset = qdrant.scroll(
            collection_name=collection,
            limit=batch,
            offset=offset,
            with_payload=True,
            with_vectors=False,
        )
        for p in points:
            payload = p.payload or {}
            doi = (payload.get("doi") or "").strip()
            if not doi:
                continue
            title = payload.get("title") or ""
            abstract = payload.get("abstract") or ""
            yield doi, f"{title} {abstract}".strip()
        if offset is None:
            break
