"""LitQA2 retrieval track (RETRIEVAL-EVAL-SPEC Phase 5, retrieval-only).

For each LitQA2 question, does a retriever surface the question's SOURCE
paper(s) in the top-k of the live `papers` corpus? Reports Recall@1/5/10 and
MRR per retriever with bootstrap CIs, plus the headline paired comparison.

The PRODUCTION retriever is AgentRetriever (the chat agent's paper_search:
multi-query SPECTER vote-fusion). To keep it deterministic + paper-grade, the
per-question query variants are FROZEN (generated once via the production
expansion prompt, committed). SpecterDense (single-query) isolates the fan-out
benefit; CitationRerank is the search-page config.

CAVEAT baked into the output: the LitQA2 source papers were backfilled into the
graph as bare nodes (no CITES edges yet), so CitationRerank's graph signal is
absent for them and it degenerates toward dense on this set. AgentRetriever
(dense-based, no citation) is the faithful production number here.
"""

from __future__ import annotations

import json
import os
import subprocess
from datetime import datetime, timezone

from ..frozen_variants.regen_variants import PROMPT_SHA, expand_query
from ..metrics import mrr as mrr_metric
from ..metrics import paired_bootstrap, recall_at_k, single_bootstrap
from ..retrievers import (
    AgentRetriever,
    CitationRerankRetriever,
    SpecterDenseRetriever,
)

LAB_BENCH_REPO = "futurehouse/lab-bench"
LITQA2_PARQUET = "LitQA2/train-00000-of-00001.parquet"
RETRIEVE_DEPTH = 20
DOI_PREFIX_RE = None


def _norm_doi(u: str) -> str:
    import re
    u = str(u).strip().lower()
    u = re.sub(r"^https?://(dx\.)?doi\.org/", "", u)
    return re.sub(r"^doi:", "", u).strip()


def load_litqa2() -> list[dict]:
    """Load the public LitQA2 split; return [{qid, question, source_dois}]."""
    from huggingface_hub import hf_hub_download
    import pyarrow.parquet as pq

    path = hf_hub_download(LAB_BENCH_REPO, LITQA2_PARQUET, repo_type="dataset")
    tbl = pq.read_table(path, columns=["id", "question", "sources"]).to_pylist()
    out = []
    for r in tbl:
        dois = [_norm_doi(s) for s in (r["sources"] or []) if s]
        out.append({"qid": r["id"], "question": r["question"],
                    "source_dois": [d for d in dois if d]})
    return out


def build_frozen_variants(questions: list[dict], path: str, n: int = 5) -> dict:
    """query-text -> [base, v1, ...]; generate once via the production
    expander and commit. Reused verbatim on re-runs."""
    if os.path.exists(path):
        with open(path) as fh:
            return json.load(fh)["variants"]
    variants = {}
    for i, q in enumerate(questions, 1):
        variants[q["question"]] = expand_query(q["question"], n=n)
        if i % 25 == 0:
            print(f"  [variants] {i}/{len(questions)}")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as fh:
        json.dump({"meta": {"prompt_sha16": PROMPT_SHA, "n": n,
                            "generated_at": datetime.now(timezone.utc).isoformat(),
                            "note": "LitQA2 retrieval frozen variants; provenance only."},
                   "variants": variants}, fh, indent=2, ensure_ascii=False)
    print(f"  [variants] wrote {len(variants)} -> {path}")
    return variants


METRIC_KEYS = ["recall@1", "recall@5", "recall@10", "mrr"]


def _per_q(ranking: list[str], relevant: set[str]) -> dict:
    return {
        "recall@1": recall_at_k(ranking, relevant, 1),
        "recall@5": recall_at_k(ranking, relevant, 5),
        "recall@10": recall_at_k(ranking, relevant, 10),
        "mrr": mrr_metric(ranking, relevant),
    }


def _git_sha():
    try:
        return subprocess.check_output(["git", "rev-parse", "--short", "HEAD"],
                                       text=True).strip()
    except Exception:
        return "unknown"


def run(qc, specter, neo4j, *, results_root, variants_path,
        n_resamples=1000, device="cpu"):
    questions = load_litqa2()

    # corpus DOI set (to restrict to in-corpus questions + flag coverage)
    corpus = set()
    off = None
    while True:
        b, off = qc.scroll("papers", limit=4000, offset=off,
                           with_payload=True, with_vectors=False)
        for p in b:
            d = ((p.payload or {}).get("doi") or "").strip().lower()
            if d:
                corpus.add(d)
        if off is None:
            break

    judged = [q for q in questions
              if any(d in corpus for d in q["source_dois"])]
    print(f"[litqa2] {len(judged)}/{len(questions)} questions in-corpus")

    print("[litqa2] building/loading frozen query variants...")
    variants = build_frozen_variants(judged, variants_path)

    dense = SpecterDenseRetriever(qc, specter, "papers")
    retrievers = {
        "agent": AgentRetriever(qc, specter, "papers", frozen_variants=variants),
        "specter_dense": dense,
        "citation_rerank_0.7_0.3": CitationRerankRetriever(
            dense, neo4j, vector_weight=0.7, citation_weight=0.3),
    }

    per_query = {name: {m: [] for m in METRIC_KEYS} for name in retrievers}
    rankings_out = {name: {} for name in retrievers}
    for i, q in enumerate(judged, 1):
        relevant = {d for d in q["source_dois"] if d in corpus}
        for name, r in retrievers.items():
            ranking = [doi for doi, _ in r.retrieve(q["question"], top_k=RETRIEVE_DEPTH)]
            rankings_out[name][q["qid"]] = ranking[:RETRIEVE_DEPTH]
            pm = _per_q(ranking, relevant)
            for m in METRIC_KEYS:
                per_query[name][m].append(pm[m])
        if i % 20 == 0:
            print(f"  [retrieve] {i}/{len(judged)}")

    summary = {m: {} for m in METRIC_KEYS}
    for m in METRIC_KEYS:
        for name in retrievers:
            summary[m][name] = single_bootstrap(per_query[name][m], n_resamples=n_resamples)

    # headline: agent vs single-query dense on recall@10 and MRR
    sig = {}
    for m in ("recall@10", "mrr"):
        sig[f"agent_vs_specter_dense_{m}"] = paired_bootstrap(
            per_query["agent"][m], per_query["specter_dense"][m],
            n_resamples=n_resamples)

    header = {
        "track": "litqa2-retrieval",
        "n_questions_total": len(questions),
        "n_in_corpus": len(judged),
        "retrieve_depth": RETRIEVE_DEPTH,
        "retrievers": list(retrievers),
        "variant_prompt_sha16": PROMPT_SHA,
        "git_sha": _git_sha(),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "caveat": ("citation_rerank COLLAPSES to ~0 here, and it is a real cold-start "
                   "effect, not a bug: the LitQA2 sources were backfilled with 0 "
                   "citations, so citation re-ranking demotes them below older cited "
                   "papers (verified: a source ranked #1 by dense falls out of top-20 "
                   "under citation-rerank). This is a genuine limitation of citation "
                   "re-ranking on freshly-ingested papers AND partly a backfill artifact "
                   "(no CITES edges), so it is NOT representative of established papers. "
                   "AgentRetriever (no citation signal) is the production number. "
                   "Note: the multi-query fan-out (agent) does not beat single-query "
                   "SPECTER-dense on these precise factual questions. Answer-accuracy vs "
                   "PaperQA2 is NOT like-for-like (PaperQA2 trained on LitQA2); that "
                   "caveat belongs to the answer track."),
    }
    out_dir = os.path.join(results_root, "litqa2")
    os.makedirs(out_dir, exist_ok=True)
    for name, by_qid in rankings_out.items():
        with open(os.path.join(out_dir, f"{name}.jsonl"), "w") as fh:
            for qid, rk in by_qid.items():
                fh.write(json.dumps({"qid": qid, "ranking": rk}) + "\n")
    payload = {"header": header, "metrics": summary, "significance": sig,
               "in_corpus_dois": sorted({d for q in judged for d in q["source_dois"] if d in corpus})}
    with open(os.path.join(out_dir, "retrieval.json"), "w") as fh:
        json.dump(payload, fh, indent=2)
    _write_md(out_dir, payload)
    print(f"[litqa2] results -> {out_dir}/retrieval.{{json,md}}")
    return payload


def _write_md(out_dir, payload):
    h, metrics, sig = payload["header"], payload["metrics"], payload["significance"]
    names = h["retrievers"]
    lines = [
        "# LitQA2 — retrieval track", "",
        f"- questions in-corpus: {h['n_in_corpus']}/{h['n_questions_total']} "
        f"| retrieve depth: {h['retrieve_depth']}",
        f"- git: `{h['git_sha']}` | variant prompt sha: `{h['variant_prompt_sha16']}` "
        f"| {h['generated_at']}",
        "", "## Retrieval metrics (mean [95% CI])", "",
        "| metric | " + " | ".join(names) + " |",
        "|" + "---|" * (len(names) + 1),
    ]
    for m in METRIC_KEYS:
        row = [m]
        for name in names:
            s = metrics[m][name]
            row.append(f"{s['mean']:.4f} [{s['ci_low']:.3f}, {s['ci_high']:.3f}]")
        lines.append("| " + " | ".join(row) + " |")
    lines += ["", "## Headline (AgentRetriever vs single-query dense, paired bootstrap)", "",
              "| metric | mean diff | 95% CI | p |", "|---|---|---|---|"]
    for k, s in sig.items():
        lines.append(f"| {k} | {s['mean_diff']:+.4f} | "
                     f"[{s['ci_low']:.3f}, {s['ci_high']:.3f}] | {s['p_value_two_sided']:.3f} |")
    lines += ["", "## Caveat", "", h["caveat"]]
    with open(os.path.join(out_dir, "summary.md"), "w") as fh:
        fh.write("\n".join(lines) + "\n")
