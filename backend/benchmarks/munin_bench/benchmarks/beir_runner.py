"""BEIR subset runner (RETRIEVAL-EVAL-SPEC Phase 3).

Loads a BEIR subset via ir_datasets, builds an ISOLATED ``eval_<subset>``
Qdrant collection (SPECTER over title+abstract; never touches production
``papers``), builds a BM25 index, runs the retriever sweep, scores with the
Phase 1 metrics, and writes results/.

The citation graph does NOT exist for BEIR corpora, so citation-rerank runs
against an empty graph and DEGENERATES to dense-only (citation_score is always
0, combined = norm_vector_weight * vector_score, which is a monotonic rescale
of the dense order -> identical ranking). We run it anyway and report the
equality empirically. The real BEIR story is BM25 vs SPECTER vs RRF[BM25,
SPECTER], where BM25 fills the role Munin's graph plays on the local pool.
"""

from __future__ import annotations

import json
import os
import subprocess
from datetime import datetime, timezone

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

# Retrieval depth: retrieve 100 so Recall@100 is meaningful; nDCG@10 is the
# headline metric.
RETRIEVE_DEPTH = 100
RRF_K = 60

# subset -> ir_datasets id. Five subsets per the paper; csfcube is not in
# ir_datasets' BEIR and may be unavailable (documented, droppable per spec).
SUBSETS = {
    "scifact": "beir/scifact/test",
    "trec-covid": "beir/trec-covid",
    "nfcorpus": "beir/nfcorpus/test",
    "scidocs": "beir/scidocs",
    "csfcube": "csfcube",  # best-effort; errors are surfaced, not fatal
}


# --------------------------------------------------------------------------
# Data loading
# --------------------------------------------------------------------------
def load_beir(subset: str):
    import ir_datasets

    if subset not in SUBSETS:
        raise ValueError(f"unknown subset {subset!r}; known: {list(SUBSETS)}")
    ds = ir_datasets.load(SUBSETS[subset])

    queries = {q.query_id: q.text for q in ds.queries_iter()}
    qrels: dict[str, dict[str, int]] = {}
    for qr in ds.qrels_iter():
        qrels.setdefault(qr.query_id, {})[qr.doc_id] = int(qr.relevance)
    return ds, queries, qrels


def _doc_text(doc) -> str:
    """title + abstract joined EXACTLY as production embeds papers
    (paper_pipeline.py:1392 -> ``f"{title}\\n\\n{abstract}"``), so the BEIR
    eval measures Munin's actual SPECTER document construction, not a variant.
    The separator is irrelevant to BM25 (whitespace-tokenised either way)."""
    title = getattr(doc, "title", "") or ""
    body = getattr(doc, "text", "") or getattr(doc, "abstract", "") or ""
    return f"{title}\n\n{body}".strip()


# --------------------------------------------------------------------------
# Eval collection (isolated; eval_* prefix)
# --------------------------------------------------------------------------
def eval_collection_name(subset: str) -> str:
    return f"eval_{subset.replace('-', '_')}"


def build_eval_collection(qdrant, specter, subset, ds, *, rebuild=False,
                          batch=256) -> str:
    from qdrant_client.models import Distance, PointStruct, VectorParams

    name = eval_collection_name(subset)
    exists = qdrant.collection_exists(name)
    if exists and not rebuild:
        cnt = qdrant.count(name).count
        print(f"  [collection] {name} exists ({cnt} points); reuse")
        return name
    if exists:
        qdrant.delete_collection(name)
    qdrant.create_collection(
        name, vectors_config=VectorParams(size=768, distance=Distance.COSINE)
    )

    buf_ids, buf_texts, pid = [], [], 0
    total = 0

    def flush():
        nonlocal total
        if not buf_texts:
            return
        vecs = specter.encode(buf_texts, batch_size=64,
                              show_progress_bar=False)
        points = [
            PointStruct(id=i, vector=v.tolist(), payload={"doc_id": d})
            for i, v, d in zip(range(total, total + len(buf_ids)), vecs, buf_ids)
        ]
        qdrant.upsert(name, points=points)
        total += len(points)

    for doc in ds.docs_iter():
        buf_ids.append(doc.doc_id)
        buf_texts.append(_doc_text(doc))
        pid += 1
        if len(buf_texts) >= batch:
            flush()
            buf_ids, buf_texts = [], []
            print(f"  [embed] {total} docs...", end="\r")
    flush()
    print(f"  [collection] {name} built: {total} docs            ")
    return name


# --------------------------------------------------------------------------
# Scoring
# --------------------------------------------------------------------------
def _per_query_metrics(ranked_ids, qrels_q):
    relevant = {d for d, g in qrels_q.items() if g >= 1}
    return {
        "ndcg@10": ndcg_at_k(ranked_ids, qrels_q, 10),
        "recall@10": recall_at_k(ranked_ids, relevant, 10),
        "recall@100": recall_at_k(ranked_ids, relevant, 100),
        "mrr": mrr(ranked_ids, relevant),
        "hits@10": hits_at_k(ranked_ids, relevant, 10),
    }


METRIC_KEYS = ["ndcg@10", "recall@10", "recall@100", "mrr", "hits@10"]


def make_header(subset, ds, queries, retrievers, device):
    try:
        sha = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"], text=True
        ).strip()
    except Exception:
        sha = "unknown"
    return {
        "subset": subset,
        "ir_datasets_id": SUBSETS[subset],
        "n_queries": len(queries),
        "n_docs": ds.docs_count(),
        "retrievers": retrievers,
        "retrieve_depth": RETRIEVE_DEPTH,
        "rrf_k": RRF_K,
        "specter_device": device,
        "git_sha": sha,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "note": "citation-rerank degenerates to dense on BEIR (no graph).",
    }


# --------------------------------------------------------------------------
# Main entry
# --------------------------------------------------------------------------
def run_subset(qdrant, specter, subset, *, results_root, rebuild=False,
               n_resamples=1000, device="cpu"):
    ds, queries, qrels = load_beir(subset)
    qids = [q for q in queries if q in qrels]  # only judged queries
    print(f"[{subset}] {len(qids)} judged queries, {ds.docs_count()} docs")

    coll = build_eval_collection(qdrant, specter, subset, ds, rebuild=rebuild)

    print("  [bm25] building index over title+abstract...")
    bm25 = BM25Retriever((doc.doc_id, _doc_text(doc)) for doc in ds.docs_iter())
    dense = SpecterDenseRetriever(qdrant, specter, coll, id_field="doc_id")
    cite = CitationRerankRetriever(dense, neo4j_driver=None,
                                   vector_weight=0.7, citation_weight=0.3)

    # retriever name -> per-query ranked id lists
    rankings: dict[str, dict[str, list[str]]] = {
        "bm25": {}, "specter_dense": {}, "citation_rerank_0.7_0.3": {},
        "rrf_bm25_specter": {},
    }
    for i, qid in enumerate(qids, 1):
        q = queries[qid]
        bm25_hits = bm25.retrieve(q, top_k=RETRIEVE_DEPTH)
        dense_hits = dense.retrieve(q, top_k=RETRIEVE_DEPTH)
        cite_hits = cite.retrieve(q, top_k=RETRIEVE_DEPTH)
        rrf = rrf_fuse(
            [[d for d, _ in bm25_hits], [d for d, _ in dense_hits]], k=RRF_K
        )
        rankings["bm25"][qid] = [d for d, _ in bm25_hits]
        rankings["specter_dense"][qid] = [d for d, _ in dense_hits]
        rankings["citation_rerank_0.7_0.3"][qid] = [d for d, _ in cite_hits]
        rankings["rrf_bm25_specter"][qid] = [d for d, _ in rrf]
        if i % 25 == 0:
            print(f"  [retrieve] {i}/{len(qids)}", end="\r")
    print(f"  [retrieve] {len(qids)}/{len(qids)} done        ")

    # per-query metrics + aggregates
    per_query = {name: {m: [] for m in METRIC_KEYS} for name in rankings}
    for name, by_qid in rankings.items():
        for qid in qids:
            pm = _per_query_metrics(by_qid[qid], qrels[qid])
            for m in METRIC_KEYS:
                per_query[name][m].append(pm[m])

    summary = {m: {} for m in METRIC_KEYS}
    for m in METRIC_KEYS:
        for name in rankings:
            summary[m][name] = single_bootstrap(
                per_query[name][m], n_resamples=n_resamples
            )

    # headline significance on nDCG@10
    pairs = [("specter_dense", "bm25"), ("rrf_bm25_specter", "specter_dense"),
             ("rrf_bm25_specter", "bm25")]
    sig = {}
    for a, b in pairs:
        sig[f"{a}_vs_{b}"] = paired_bootstrap(
            per_query[a]["ndcg@10"], per_query[b]["ndcg@10"],
            n_resamples=n_resamples,
        )

    header = make_header(subset, ds, queries, list(rankings), device)
    out_dir = os.path.join(results_root, "beir", subset)
    os.makedirs(out_dir, exist_ok=True)
    for name, by_qid in rankings.items():
        with open(os.path.join(out_dir, f"{name}.jsonl"), "w") as fh:
            for qid in qids:
                fh.write(json.dumps({"qid": qid, "ranking": by_qid[qid][:RETRIEVE_DEPTH]}) + "\n")
    payload = {"header": header, "metrics": summary, "significance": sig}
    with open(os.path.join(out_dir, "summary.json"), "w") as fh:
        json.dump(payload, fh, indent=2)
    _write_markdown(out_dir, payload)
    print(f"  [results] {out_dir}/summary.{{json,md}}")
    return payload


def _write_markdown(out_dir, payload):
    header, metrics, sig = payload["header"], payload["metrics"], payload["significance"]
    names = header["retrievers"]
    lines = [
        f"# BEIR — {header['subset']}",
        "",
        f"- ir_datasets: `{header['ir_datasets_id']}`",
        f"- queries (judged): {header['n_queries']} | docs: {header['n_docs']}",
        f"- retrieve depth: {header['retrieve_depth']} | RRF k: {header['rrf_k']} "
        f"| SPECTER: {header['specter_device']}",
        f"- git: `{header['git_sha']}` | {header['generated_at']}",
        f"- note: {header['note']}",
        "",
        "## Metrics (mean [95% CI])",
        "",
        "| metric | " + " | ".join(names) + " |",
        "|" + "---|" * (len(names) + 1),
    ]
    for m in METRIC_KEYS:
        row = [m]
        for name in names:
            s = metrics[m][name]
            row.append(f"{s['mean']:.4f} [{s['ci_low']:.3f}, {s['ci_high']:.3f}]")
        lines.append("| " + " | ".join(row) + " |")
    lines += ["", "## Headline significance (nDCG@10, paired bootstrap)", "",
              "| pair | mean diff | 95% CI | p |", "|---|---|---|---|"]
    for k, s in sig.items():
        lines.append(
            f"| {k} | {s['mean_diff']:+.4f} | "
            f"[{s['ci_low']:.3f}, {s['ci_high']:.3f}] | {s['p_value_two_sided']:.3f} |"
        )
    with open(os.path.join(out_dir, "summary.md"), "w") as fh:
        fh.write("\n".join(lines) + "\n")
