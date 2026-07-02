"""Encoder bake-off — does a better retrieval encoder beat SPECTER-v1?

Fully non-invasive: runs IN-MEMORY on a BEIR subset (default SciFact). For each
candidate encoder we embed the corpus + queries, cosine-rank, and score with the
Phase-1 metrics. Nothing touches Qdrant or the production `papers` collection.

Answers the Phase-5 follow-up: answer accuracy is bounded by retrieval recall,
and SPECTER-v1 is a weak, dated document-embedder used out-of-distribution on
short queries. This quantifies how much a retrieval-tuned encoder would lift
recall before committing to a production re-embed.

Candidates carry query/doc prefixes because some encoders require them
(E5: "query:"/"passage:"; BGE: a query instruction; SPECTER/SciNCL: none).
"""

from __future__ import annotations

import json
import os
import subprocess
from datetime import datetime, timezone

import numpy as np

from ..metrics import hits_at_k, mrr, ndcg_at_k, paired_bootstrap, recall_at_k

SUBSETS = {"scifact": "beir/scifact/test", "nfcorpus": "beir/nfcorpus/test"}

# tag -> (model, query_prefix, doc_prefix). SPECTER-v1 first = the baseline.
ENCODERS = [
    ("specter_v1", "/opt/munin/data/models/specter", "", ""),
    ("scincl", "malteos/scincl", "", ""),
    ("e5_large_v2", "intfloat/e5-large-v2", "query: ", "passage: "),
    ("bge_large", "BAAI/bge-large-en-v1.5",
     "Represent this sentence for searching relevant passages: ", ""),
]
METRIC_KEYS = ["ndcg@10", "recall@10", "recall@100", "mrr"]


def _doc_text(doc) -> str:
    title = getattr(doc, "title", "") or ""
    body = getattr(doc, "text", "") or getattr(doc, "abstract", "") or ""
    return f"{title}\n\n{body}".strip()


def _load(subset):
    import ir_datasets
    ds = ir_datasets.load(SUBSETS[subset])
    doc_ids, docs = [], []
    for d in ds.docs_iter():
        doc_ids.append(d.doc_id)
        docs.append(_doc_text(d))
    queries = {q.query_id: q.text for q in ds.queries_iter()}
    qrels = {}
    for qr in ds.qrels_iter():
        qrels.setdefault(qr.query_id, {})[qr.doc_id] = int(qr.relevance)
    qids = [q for q in queries if q in qrels]
    return doc_ids, docs, queries, qrels, qids, ds.docs_count()


def _embed(model_path, texts, prefix, device, batch=64):
    from sentence_transformers import SentenceTransformer
    m = SentenceTransformer(model_path, device=device)
    pref = [prefix + t for t in texts] if prefix else texts
    return np.asarray(m.encode(pref, batch_size=batch, normalize_embeddings=True,
                               show_progress_bar=False), dtype=np.float32)


def _per_query(doc_ids, sims_row, qrels_q, topn=100):
    # top-N doc ids by similarity (descending)
    idx = np.argpartition(-sims_row, range(min(topn, len(sims_row))))[:topn]
    idx = idx[np.argsort(-sims_row[idx])]
    ranked = [doc_ids[i] for i in idx]
    rel = {d for d, g in qrels_q.items() if g >= 1}
    return {"ndcg@10": ndcg_at_k(ranked, qrels_q, 10),
            "recall@1": recall_at_k(ranked, rel, 1),
            "recall@5": recall_at_k(ranked, rel, 5),
            "recall@10": recall_at_k(ranked, rel, 10),
            "recall@100": recall_at_k(ranked, rel, 100),
            "mrr": mrr(ranked, rel), "hits@10": hits_at_k(ranked, rel, 10)}


def _git_sha():
    try:
        return subprocess.check_output(["git", "rev-parse", "--short", "HEAD"],
                                       text=True).strip()
    except Exception:
        return "unknown"


def run(subset="scifact", *, results_root, device="cpu", encoders=ENCODERS,
        n_resamples=1000):
    doc_ids, docs, queries, qrels, qids, n_docs = _load(subset)
    print(f"[bakeoff:{subset}] {len(qids)} queries, {n_docs} docs, "
          f"{len(encoders)} encoders on {device}")

    per_query = {}   # tag -> {metric -> [per-query]}
    for tag, model, qpref, dpref in encoders:
        print(f"  [{tag}] embedding docs...")
        D = _embed(model, docs, dpref, device)
        Q = _embed(model, [queries[q] for q in qids], qpref, device)
        sims = Q @ D.T   # cosine (both normalized)
        pq = {m: [] for m in METRIC_KEYS}
        for i, qid in enumerate(qids):
            r = _per_query(doc_ids, sims[i], qrels[qid])
            for m in METRIC_KEYS:
                pq[m].append(r[m])
        per_query[tag] = pq
        print(f"  [{tag}] nDCG@10={np.mean(pq['ndcg@10']):.4f} "
              f"Recall@10={np.mean(pq['recall@10']):.4f}")

    baseline = encoders[0][0]
    metrics = {m: {tag: float(np.mean(per_query[tag][m])) for tag in per_query}
               for m in METRIC_KEYS}
    sig = {}
    for tag in per_query:
        if tag == baseline:
            continue
        sig[f"{tag}_vs_{baseline}"] = paired_bootstrap(
            per_query[tag]["ndcg@10"], per_query[baseline]["ndcg@10"],
            n_resamples=n_resamples)

    header = {"subset": subset, "ir_datasets_id": SUBSETS[subset],
              "n_queries": len(qids), "n_docs": n_docs,
              "encoders": [e[0] for e in encoders], "baseline": baseline,
              "device": device, "doc_construction": "title\\n\\nabstract",
              "git_sha": _git_sha(),
              "generated_at": datetime.now(timezone.utc).isoformat()}
    out_dir = os.path.join(results_root, "bakeoff")
    os.makedirs(out_dir, exist_ok=True)
    payload = {"header": header, "metrics": metrics, "significance": sig}
    with open(os.path.join(out_dir, f"{subset}.json"), "w") as fh:
        json.dump(payload, fh, indent=2)
    _write_md(out_dir, payload)
    print(f"[bakeoff] -> {out_dir}/{subset}.{{json,md}}")
    return payload


POOL_METRIC_KEYS = ["recall@1", "recall@5", "recall@10", "mrr"]


def run_litqa2_pool(qc, *, results_root, device="cpu", n_distractors=5000,
                    encoders=ENCODERS, n_resamples=1000, seed=42):
    """Confirm the bake-off on Munin's OWN data: rank each LitQA2 source paper
    among a shared pool of (all 199 sources + a random N-paper corpus sample).
    Same pool for every encoder -> fair relative comparison. Absolute recall is
    optimistic vs the full 68k corpus (fewer distractors), but the ordering
    (does BGE/E5 beat SPECTER on our papers + our query type) is what we confirm.
    """
    import random
    from .litqa2_runner import load_litqa2

    questions = load_litqa2()
    src_dois = {d.lower() for q in questions for d in q["source_dois"]}

    src_text, distractors = {}, []
    off = None
    while True:
        b, off = qc.scroll("papers", limit=4000, offset=off,
                           with_payload=True, with_vectors=False)
        for p in b:
            pl = p.payload or {}
            doi = (pl.get("doi") or "").strip().lower()
            if not doi:
                continue
            text = f"{pl.get('title') or ''}\n\n{pl.get('abstract') or ''}".strip()
            if doi in src_dois:
                src_text[doi] = text
            else:
                distractors.append((doi, text))
        if off is None:
            break
    rng = random.Random(seed)
    sample = rng.sample(distractors, min(n_distractors, len(distractors)))

    doc_ids = list(src_text) + [d for d, _ in sample]
    docs = [src_text[d] for d in src_text] + [t for _, t in sample]
    qids = [q["qid"] for q in questions]
    queries = {q["qid"]: q["question"] for q in questions}
    qrels = {q["qid"]: {d.lower(): 1 for d in q["source_dois"] if d.lower() in src_text}
             for q in questions}
    qids = [q for q in qids if qrels[q]]  # keep questions whose source is in-pool
    print(f"[bakeoff:litqa2-pool] {len(qids)} questions | pool={len(doc_ids)} "
          f"({len(src_text)} sources + {len(sample)} distractors) | {len(encoders)} encoders on {device}")

    per_query = {}
    for tag, model, qpref, dpref in encoders:
        print(f"  [{tag}] embedding pool...")
        D = _embed(model, docs, dpref, device)
        Q = _embed(model, [queries[q] for q in qids], qpref, device)
        sims = Q @ D.T
        pq = {m: [] for m in POOL_METRIC_KEYS}
        for i, qid in enumerate(qids):
            r = _per_query(doc_ids, sims[i], qrels[qid])
            for m in POOL_METRIC_KEYS:
                pq[m].append(r[m])
        per_query[tag] = pq
        print(f"  [{tag}] Recall@10={np.mean(pq['recall@10']):.4f} "
              f"MRR={np.mean(pq['mrr']):.4f}")

    baseline = encoders[0][0]
    metrics = {m: {t: float(np.mean(per_query[t][m])) for t in per_query}
               for m in POOL_METRIC_KEYS}
    sig = {}
    for t in per_query:
        if t == baseline:
            continue
        sig[f"{t}_vs_{baseline}"] = paired_bootstrap(
            per_query[t]["recall@10"], per_query[baseline]["recall@10"],
            n_resamples=n_resamples)

    header = {"task": "litqa2-pool", "n_questions": len(qids),
              "pool_size": len(doc_ids), "n_sources": len(src_text),
              "n_distractors": len(sample), "encoders": [e[0] for e in encoders],
              "baseline": baseline, "device": device, "seed": seed,
              "doc_construction": "title\\n\\nabstract", "git_sha": _git_sha(),
              "generated_at": datetime.now(timezone.utc).isoformat(),
              "caveat": ("Shared random pool; absolute recall optimistic vs full "
                         "68k corpus, relative comparison is valid.")}
    out_dir = os.path.join(results_root, "bakeoff")
    os.makedirs(out_dir, exist_ok=True)
    payload = {"header": header, "metrics": metrics, "significance": sig,
               "metric_keys": POOL_METRIC_KEYS}
    with open(os.path.join(out_dir, "litqa2_pool.json"), "w") as fh:
        json.dump(payload, fh, indent=2)
    _write_pool_md(out_dir, payload)
    print(f"[bakeoff] -> {out_dir}/litqa2_pool.{{json,md}}")
    return payload


def _write_pool_md(out_dir, payload):
    h, metrics, sig = payload["header"], payload["metrics"], payload["significance"]
    tags = h["encoders"]
    lines = [f"# Encoder bake-off — LitQA2 pool (Munin corpus)", "",
             f"- {h['n_questions']} questions | pool {h['pool_size']} "
             f"({h['n_sources']} sources + {h['n_distractors']} corpus distractors, seed {h['seed']})",
             f"- baseline = `{h['baseline']}` | device {h['device']} | git `{h['git_sha']}` | {h['generated_at']}",
             "", "| metric | " + " | ".join(tags) + " |",
             "|" + "---|" * (len(tags) + 1)]
    for m in POOL_METRIC_KEYS:
        lines.append("| " + " | ".join([m] + [f"{metrics[m][t]:.4f}" for t in tags]) + " |")
    lines += ["", f"## Significance vs `{h['baseline']}` (Recall@10, paired bootstrap)",
              "", "| candidate | Δ Recall@10 | 95% CI | p |", "|---|---|---|---|"]
    for k, s in sig.items():
        lines.append(f"| {k} | {s['mean_diff']:+.4f} | "
                     f"[{s['ci_low']:.3f}, {s['ci_high']:.3f}] | {s['p_value_two_sided']:.3f} |")
    lines += ["", f"## Caveat", "", h["caveat"]]
    with open(os.path.join(out_dir, "litqa2_pool.md"), "w") as fh:
        fh.write("\n".join(lines) + "\n")


def _write_md(out_dir, payload):
    h, metrics, sig = payload["header"], payload["metrics"], payload["significance"]
    tags = h["encoders"]
    lines = [f"# Encoder bake-off — BEIR {h['subset']}", "",
             f"- {h['n_queries']} queries, {h['n_docs']} docs | device {h['device']} "
             f"| docs = {h['doc_construction']}",
             f"- baseline = `{h['baseline']}` | git `{h['git_sha']}` | {h['generated_at']}",
             "", "| metric | " + " | ".join(tags) + " |",
             "|" + "---|" * (len(tags) + 1)]
    for m in METRIC_KEYS:
        row = [m] + [f"{metrics[m][t]:.4f}" for t in tags]
        lines.append("| " + " | ".join(row) + " |")
    lines += ["", f"## Significance vs `{h['baseline']}` (nDCG@10, paired bootstrap)",
              "", "| candidate | Δ nDCG@10 | 95% CI | p |", "|---|---|---|---|"]
    for k, s in sig.items():
        lines.append(f"| {k} | {s['mean_diff']:+.4f} | "
                     f"[{s['ci_low']:.3f}, {s['ci_high']:.3f}] | {s['p_value_two_sided']:.3f} |")
    with open(os.path.join(out_dir, f"{payload['header']['subset']}.md"), "w") as fh:
        fh.write("\n".join(lines) + "\n")
