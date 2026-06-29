"""Phase 2 gate: instantiate every retriever and run the sanity query
"protein folding" against the live papers collection; print top-3 for visual
inspection. Passes if each retriever returns >0 hits.

Run on hugin with the SPECTER/Qdrant/Neo4j stack on the path, e.g.:

    NEO4J_PASSWORD=... \\
    PYTHONPATH=<bm25_dir> /opt/munin/services/pipeline/venv/bin/python \\
        -m munin_bench.pipelines.gate_phase2
"""

from __future__ import annotations

import os
import sys

from ..clients import get_neo4j, get_qdrant, load_specter
from ..config import PAPERS_COLLECTION
from ..retrievers import (
    AgentRetriever,
    BM25Retriever,
    CitationRerankRetriever,
    RRFHybridRetriever,
    SpecterDenseRetriever,
    TwoHopCheckRetriever,
    iter_qdrant_corpus,
)

QUERY = "protein folding"
BM25_CACHE = os.path.join(
    os.path.dirname(__file__), "..", "..", "data", "local", "bm25_papers.pkl"
)


def _show(name, pairs):
    ok = len(pairs) > 0
    flag = "PASS" if ok else "FAIL"
    print(f"\n[{flag}] {name}: {len(pairs)} hits")
    for doi, score in pairs[:3]:
        print(f"    {score:.4f}  {doi}")
    return ok


def main() -> int:
    qdrant = get_qdrant()
    specter = load_specter()

    print("Building/loading BM25 over papers (title+abstract)...")
    bm25 = BM25Retriever.from_cache_or_build(
        iter_qdrant_corpus(qdrant, PAPERS_COLLECTION),
        os.path.abspath(BM25_CACHE),
    )
    print(f"  BM25 corpus size: {len(bm25.doc_ids)}")

    dense = SpecterDenseRetriever(qdrant, specter, PAPERS_COLLECTION)
    # AgentRetriever with a live single-variant set for the sanity query: prove
    # the fan-out path runs (the committed frozen set is a Phase-4 artifact).
    agent = AgentRetriever(
        qdrant, specter, PAPERS_COLLECTION,
        frozen_variants={QUERY: [QUERY, "protein structure prediction",
                                 "molecular dynamics of protein folding"]},
    )

    results = {
        "BM25": bm25.retrieve(QUERY, top_k=10),
        "SpecterDense": dense.retrieve(QUERY, top_k=10),
        "AgentRetriever (production)": agent.retrieve(QUERY, top_k=10),
    }

    # Graph retrievers (need Neo4j).
    try:
        neo4j = get_neo4j()
        cite = CitationRerankRetriever(dense, neo4j, vector_weight=0.7,
                                       citation_weight=0.3)
        rrf = RRFHybridRetriever(dense, neo4j)
        two_hop = TwoHopCheckRetriever(rrf)
        results["CitationRerank (0.7/0.3)"] = cite.retrieve(QUERY, top_k=10)
        results["RRFHybrid (1-hop)"] = rrf.retrieve(QUERY, top_k=10)
        results["TwoHopCheck (2-hop)"] = two_hop.retrieve(QUERY, top_k=10)
    except Exception as e:
        print(f"\n[SKIP] graph retrievers: {e}", file=sys.stderr)

    all_ok = all(_show(name, pairs) for name, pairs in results.items())
    print("\n" + ("GATE PASS" if all_ok and len(results) == 6 else "GATE INCOMPLETE/FAIL"))
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
