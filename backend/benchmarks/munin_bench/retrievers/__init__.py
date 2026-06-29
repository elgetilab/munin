"""Retrievers for Track A. See RETRIEVAL-EVAL-SPEC Phase 2.

Production path for the gate/metrics is AgentRetriever (the chat agent's
paper_search). CitationRerankRetriever is the search-page config. RRF + 2-hop
are future-work / degradation-check retrievers.
"""

from .base import Retriever
from .bm25 import BM25Retriever, iter_qdrant_corpus, tokenize
from .specter_dense import SpecterDenseRetriever
from .agent_retriever import AgentRetriever
from .citation_rerank import CitationRerankRetriever, compute_citation_score
from .rrf_hybrid import RRFHybridRetriever, rrf_fuse
from .two_hop_check import TwoHopCheckRetriever

__all__ = [
    "Retriever",
    "BM25Retriever",
    "iter_qdrant_corpus",
    "tokenize",
    "SpecterDenseRetriever",
    "AgentRetriever",
    "CitationRerankRetriever",
    "compute_citation_score",
    "RRFHybridRetriever",
    "rrf_fuse",
    "TwoHopCheckRetriever",
]
