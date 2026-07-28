"""LitSearch retrieval benchmark (BENCHMARK-TODO T8).

LitSearch (Ajith et al. 2024, arXiv 2407.18940): 597 real natural-language
literature-search queries over a ~64k-paper S2ORC corpus. This is the closest
PUBLIC benchmark to Munin's actual usage pattern — finding papers from a
question, rather than BEIR/SciFact's claim-verification framing.

WHY THIS IS MORE THAN "A SIXTH BEIR SUBSET". ``beir_runner`` documents that on
BEIR the citation graph does not exist, so ``citation_rerank`` degenerates to
dense-only and the comparison is vacuous. LitSearch ships a populated citation
graph (the corpus ``citations`` field), so this is the FIRST public benchmark
on which Munin's citation-rerank retriever can actually be evaluated rather
than trivially reduced to dense.

Differences from ``beir_runner``, all deliberate:
  * Encoder is **BGE-large-en-v1.5**, the PRODUCTION paper encoder, not the
    SPECTER-v1 the 2026-06/07 BEIR runs used. BGE needs its query instruction
    on QUERIES ONLY (``config.BGE_QUERY_INSTRUCTION``); documents embed raw.
    Never compare these numbers to the SPECTER-era BEIR table.
  * Citation counts come from an in-memory graph built from the corpus, via a
    subclass that overrides ``_citation_counts``. That method is isolated in
    ``CitationRerankRetriever`` precisely so a non-Neo4j source can be
    substituted; the scoring maths is otherwise untouched production code.
  * Relevance is BINARY (a query's ``corpusids`` list), so nDCG@10 has no
    graded gains.

Domain caveat for the paper: LitSearch is ML/NLP, not chemistry. It measures
the retrieval MECHANISM on realistic queries, not Munin's own domain.

    PYTHONPATH=$HOME/.cache/munin_bench_deps:. \
    /opt/munin/services/pipeline/venv/bin/python -m munin_bench.pipelines.run_litsearch
"""

from __future__ import annotations

import json
import os
import subprocess
from datetime import datetime, timezone

from .. import config
from ..metrics import (
    hits_at_k,
    mrr,
    ndcg_at_k,
    paired_bootstrap,
    recall_at_k,
    single_bootstrap,
)
from ..retrievers import (
    BM25Retriever,
    CitationRerankRetriever,
    SpecterDenseRetriever,
    rrf_fuse,
)

RETRIEVE_DEPTH = 100
RRF_K = 60
COLLECTION = "eval_litsearch"

_HF = "hf://datasets/princeton-nlp/LitSearch"
_CORPUS_SHARDS = 6
_QUERY_FILE = f"{_HF}/query/full-00000-of-00001.parquet"


# --------------------------------------------------------------------------
# Data loading
# --------------------------------------------------------------------------
def load_litsearch():
    """Returns (docs, queries, qrels, citation_counts).

    docs           : list[(doc_id, text)] with text = "title\\n\\nabstract"
    queries        : {query_id: query_text}
    qrels          : {query_id: {doc_id: 1}}   (binary relevance)
    citation_counts: {doc_id: in-degree within the corpus}
    """
    import pandas as pd

    frames = []
    for i in range(_CORPUS_SHARDS):
        path = f"{_HF}/corpus_clean/full-0000{i}-of-0000{_CORPUS_SHARDS}.parquet"
        frames.append(pd.read_parquet(
            path, columns=["corpusid", "title", "abstract", "citations"]))
        print(f"  [corpus] shard {i + 1}/{_CORPUS_SHARDS} loaded", end="\r")
    corpus = pd.concat(frames, ignore_index=True)
    print(f"  [corpus] {len(corpus)} papers loaded                    ")

    docs = []
    for cid, title, abstract in zip(corpus["corpusid"], corpus["title"],
                                    corpus["abstract"]):
        title = title or ""
        abstract = abstract or ""
        # Same document construction as production (paper_pipeline.py:1392).
        docs.append((str(cid), f"{title}\n\n{abstract}".strip()))

    # In-corpus citation IN-degree. The `citations` field is OUTgoing
    # references, so in-degree = how many corpus papers cite this one, which
    # is what production's `citation_count` means (Cypher counts (citing)->(p)).
    in_corpus = {str(c) for c in corpus["corpusid"]}
    counts: dict[str, int] = {}
    for refs in corpus["citations"]:
        if refs is None:
            continue
        for r in refs:
            r = str(r)
            if r in in_corpus:
                counts[r] = counts.get(r, 0) + 1
    print(f"  [graph] {len(counts)} papers cited at least once in-corpus; "
          f"{sum(counts.values())} edges")

    qdf = pd.read_parquet(_QUERY_FILE)
    queries, qrels = {}, {}
    for i, (qtext, cids) in enumerate(zip(qdf["query"], qdf["corpusids"])):
        qid = f"q{i}"
        rel = {str(c) for c in (cids if cids is not None else [])
               if str(c) in in_corpus}
        if not rel:
            continue          # unjudged / out-of-corpus target: skip
        queries[qid] = qtext
        qrels[qid] = {d: 1 for d in rel}
    print(f"  [queries] {len(queries)} judged (of {len(qdf)} total)")
    return docs, queries, qrels, counts


# --------------------------------------------------------------------------
# Citation re-rank over an in-memory graph
# --------------------------------------------------------------------------
class InMemoryCitationRerank(CitationRerankRetriever):
    """CitationRerankRetriever with counts from a dict instead of Neo4j.

    Only ``_citation_counts`` is overridden; the weighting, fetch_k rule and
    log-scaled citation score are inherited verbatim from the production-mirrored
    parent, so this measures the real re-ranker.
    """

    def __init__(self, dense, counts: dict[str, int], **kw):
        super().__init__(dense, neo4j_driver=None, **kw)
        self._counts = counts

    def _citation_counts(self, dois: list[str]) -> dict[str, dict]:
        return {d: {"citation_count": self._counts.get(d, 0),
                    "reference_count": 0} for d in dois}


# --------------------------------------------------------------------------
# Eval collection (isolated; never touches production)
# --------------------------------------------------------------------------
def build_eval_collection(qdrant, encoder, docs, *, rebuild=False, batch=256):
    from qdrant_client.models import Distance, PointStruct, VectorParams

    exists = qdrant.collection_exists(COLLECTION)
    if exists and not rebuild:
        cnt = qdrant.count(COLLECTION).count
        if cnt == len(docs):
            print(f"  [collection] {COLLECTION} exists ({cnt} points); reuse")
            return COLLECTION
        print(f"  [collection] {COLLECTION} has {cnt} points, expected "
              f"{len(docs)} — rebuilding")
    if exists:
        qdrant.delete_collection(COLLECTION)
    dim = encoder.get_sentence_embedding_dimension()
    qdrant.create_collection(
        COLLECTION,
        vectors_config=VectorParams(size=dim, distance=Distance.COSINE))

    total = 0
    for start in range(0, len(docs), batch):
        chunk = docs[start:start + batch]
        # Documents embed RAW — the BGE instruction goes on queries only.
        vecs = encoder.encode([t for _, t in chunk], batch_size=64,
                              show_progress_bar=False, normalize_embeddings=True)
        qdrant.upsert(COLLECTION, points=[
            PointStruct(id=total + j, vector=v.tolist(),
                        payload={"doc_id": d})
            for j, (v, (d, _)) in enumerate(zip(vecs, chunk))])
        total += len(chunk)
        print(f"  [embed] {total}/{len(docs)} docs...", end="\r")
    print(f"  [collection] {COLLECTION} built: {total} docs            ")
    return COLLECTION


# --------------------------------------------------------------------------
# Scoring
# --------------------------------------------------------------------------
METRIC_KEYS = ["ndcg@10", "recall@10", "recall@100", "mrr", "hits@10"]


def _per_query_metrics(ranked_ids, qrels_q):
    relevant = {d for d, g in qrels_q.items() if g >= 1}
    return {
        "ndcg@10": ndcg_at_k(ranked_ids, qrels_q, 10),
        "recall@10": recall_at_k(ranked_ids, relevant, 10),
        "recall@100": recall_at_k(ranked_ids, relevant, 100),
        "mrr": mrr(ranked_ids, relevant),
        "hits@10": hits_at_k(ranked_ids, relevant, 10),
    }


def _git_sha() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "--short", "HEAD"],
                                       text=True).strip()
    except Exception:
        return "unknown"


# --------------------------------------------------------------------------
# Main entry
# --------------------------------------------------------------------------
def run(qdrant, encoder, *, results_root, rebuild=False, n_resamples=1000,
        device="cpu", limit=0, date=None):
    docs, queries, qrels, counts = load_litsearch()
    qids = sorted(queries, key=lambda q: int(q[1:]))
    if limit:
        qids = qids[:limit]
    print(f"[litsearch] {len(qids)} queries, {len(docs)} docs")

    coll = build_eval_collection(qdrant, encoder, docs, rebuild=rebuild)

    print("  [bm25] building index over title+abstract...")
    bm25 = BM25Retriever(iter(docs))
    dense = SpecterDenseRetriever(qdrant, encoder, coll, id_field="doc_id",
                                  query_prefix=config.BGE_QUERY_INSTRUCTION)
    cite = InMemoryCitationRerank(dense, counts,
                                  vector_weight=0.7, citation_weight=0.3)

    names = ["bm25", "bge_dense", "citation_rerank_0.7_0.3", "rrf_bm25_bge"]
    rankings: dict[str, dict[str, list[str]]] = {n: {} for n in names}
    for i, qid in enumerate(qids, 1):
        q = queries[qid]
        bm25_hits = bm25.retrieve(q, top_k=RETRIEVE_DEPTH)
        dense_hits = dense.retrieve(q, top_k=RETRIEVE_DEPTH)
        cite_hits = cite.retrieve(q, top_k=RETRIEVE_DEPTH)
        rrf = rrf_fuse([[d for d, _ in bm25_hits], [d for d, _ in dense_hits]],
                       k=RRF_K)
        rankings["bm25"][qid] = [d for d, _ in bm25_hits]
        rankings["bge_dense"][qid] = [d for d, _ in dense_hits]
        rankings["citation_rerank_0.7_0.3"][qid] = [d for d, _ in cite_hits]
        rankings["rrf_bm25_bge"][qid] = [d for d, _ in rrf]
        if i % 25 == 0:
            print(f"  [retrieve] {i}/{len(qids)}", end="\r")
    print(f"  [retrieve] {len(qids)}/{len(qids)} done        ")

    per_query = {n: {m: [] for m in METRIC_KEYS} for n in names}
    for n in names:
        for qid in qids:
            pm = _per_query_metrics(rankings[n][qid], qrels[qid])
            for m in METRIC_KEYS:
                per_query[n][m].append(pm[m])

    aggregates = {
        n: {m: single_bootstrap(per_query[n][m], n_resamples=n_resamples)
            for m in METRIC_KEYS} for n in names
    }

    # Paired significance vs bge_dense (the production retriever).
    sig = {}
    for n in names:
        if n == "bge_dense":
            continue
        sig[f"{n}_vs_bge_dense"] = {
            m: paired_bootstrap(per_query[n][m], per_query["bge_dense"][m],
                                n_resamples=n_resamples)
            for m in ("ndcg@10", "recall@10")
        }

    # Did citation-rerank actually change the ranking? On BEIR it cannot.
    changed = sum(1 for qid in qids
                  if rankings["citation_rerank_0.7_0.3"][qid][:10]
                  != rankings["bge_dense"][qid][:10])
    cited_in_pool = sum(
        1 for qid in qids
        for d in rankings["bge_dense"][qid][:RETRIEVE_DEPTH] if counts.get(d))

    sc = {
        "track": "litsearch",
        "date": date,
        "git_sha": _git_sha(),
        "encoder": "BGE-large-en-v1.5",
        "encoder_note": ("PRODUCTION encoder. The 2026-06/07 BEIR tables used "
                         "SPECTER-v1 — do not compare across them."),
        "n_queries": len(qids),
        "n_docs": len(docs),
        "retrieve_depth": RETRIEVE_DEPTH,
        "relevance": "binary (query corpusids)",
        "citation_graph": {
            "papers_cited_in_corpus": len(counts),
            "edges": sum(counts.values()),
            "top10_changed_vs_dense": changed,
            "top10_changed_frac": round(changed / len(qids), 4) if qids else None,
            "note": ("Non-zero means citation-rerank is genuinely exercised "
                     "here, unlike BEIR where it degenerates to dense."),
        },
        "dense_pool_with_citations": cited_in_pool,
        "aggregates": aggregates,
        "significance_vs_bge_dense": sig,
        "device": device,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "domain_caveat": ("LitSearch is ML/NLP, not chemistry. It measures the "
                          "retrieval mechanism on realistic paper-finding "
                          "queries, not Munin's own domain."),
    }

    os.makedirs(results_root, exist_ok=True)
    raw = os.path.join(results_root, "litsearch_rankings.json")
    json.dump({n: rankings[n] for n in names}, open(raw, "w"))

    print("\n=== LitSearch (BGE-large, binary relevance) ===")
    for n in names:
        a = aggregates[n]
        print(f"  {n:<26} nDCG@10={a['ndcg@10']['mean']:.4f} "
              f"[{a['ndcg@10']['ci_low']:.3f},{a['ndcg@10']['ci_high']:.3f}]  "
              f"R@10={a['recall@10']['mean']:.4f}  "
              f"R@100={a['recall@100']['mean']:.4f}  "
              f"MRR={a['mrr']['mean']:.4f}")
    print(f"  citation-rerank changed top-10 on {changed}/{len(qids)} queries")

    if date:
        out = os.path.join(os.path.dirname(__file__), "..", "..", "scorecards",
                           f"{date}_litsearch.json")
        os.makedirs(os.path.dirname(out), exist_ok=True)
        json.dump(sc, open(out, "w"), indent=2)
        print(f"[litsearch] scorecard -> {os.path.abspath(out)}")
    return sc
