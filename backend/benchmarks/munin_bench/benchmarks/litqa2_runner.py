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
import random
import re
import subprocess
import urllib.request
from concurrent.futures import ThreadPoolExecutor
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
    tbl = pq.read_table(
        path, columns=["id", "question", "ideal", "distractors", "sources"]
    ).to_pylist()
    out = []
    for r in tbl:
        dois = [_norm_doi(s) for s in (r["sources"] or []) if s]
        out.append({"qid": r["id"], "question": r["question"],
                    "ideal": r["ideal"], "distractors": list(r["distractors"] or []),
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
        n_resamples=1000, device="cpu", collection="papers",
        query_prefix="", id_field="doi"):
    questions = load_litqa2()

    # corpus DOI set from the TARGET collection (papers or papers_bge — same
    # DOIs, so in-corpus membership is encoder-independent).
    corpus = set()
    off = None
    while True:
        b, off = qc.scroll(collection, limit=4000, offset=off,
                           with_payload=True, with_vectors=False)
        for p in b:
            d = ((p.payload or {}).get(id_field) or "").strip().lower()
            if d:
                corpus.add(d)
        if off is None:
            break

    judged = [q for q in questions
              if any(d in corpus for d in q["source_dois"])]
    print(f"[litqa2] {len(judged)}/{len(questions)} questions in-corpus "
          f"(collection={collection})")

    print("[litqa2] building/loading frozen query variants...")
    variants = build_frozen_variants(judged, variants_path)

    dense = SpecterDenseRetriever(qc, specter, collection, id_field=id_field,
                                  query_prefix=query_prefix)
    retrievers = {
        "agent": AgentRetriever(qc, specter, collection, frozen_variants=variants,
                                query_prefix=query_prefix),
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
               "per_query": per_query, "qids": [q["qid"] for q in judged],
               "metric_keys": METRIC_KEYS,
               "in_corpus_dois": sorted({d for q in judged for d in q["source_dois"] if d in corpus})}
    with open(os.path.join(out_dir, "retrieval.json"), "w") as fh:
        json.dump(payload, fh, indent=2)
    _write_md(out_dir, payload)
    print(f"[litqa2] results -> {out_dir}/retrieval.{{json,md}}")
    return payload


# ==========================================================================
# Answer track — end-to-end MCQ through the full agentic chat pipeline
# ==========================================================================
ABSTAIN_OPTION = "Insufficient information to answer this question."
# PaperQA2's published LitQA2 result, VERIFIED 2026-07-25 against the primary
# source (Skarlinski et al. 2024, arXiv:2409.13740v2, "Language agents achieve
# superhuman synthesis of scientific knowledge"): "a precision of 85.2% +/- 1.1%
# (mean +/- SD, n=3), and an accuracy of 66.0% +/- 1.2%". Human experts on the
# same set: precision 73.8% +/- 9.6%, accuracy 67.7% +/- 11.9% (n=9) - i.e.
# PaperQA2's accuracy was NOT significantly different from humans (p=0.66); its
# precision was (p=0.0036). Metric definitions match ours: accuracy = correct
# over ALL questions, precision = correct over ANSWERED (non-"insufficient").
PAPERQA2_ACCURACY = 0.660
PAPERQA2_PRECISION = 0.852
LITQA2_HUMAN_ACCURACY = 0.677
LITQA2_HUMAN_PRECISION = 0.738


def build_mcq(q: dict) -> dict:
    """Deterministic MCQ (shuffle seeded by qid) with an abstention option."""
    opts = [q["ideal"]] + list(q["distractors"]) + [ABSTAIN_OPTION]
    random.Random(q["qid"]).shuffle(opts)
    letters = [chr(65 + i) for i in range(len(opts))]
    correct = letters[opts.index(q["ideal"])]
    abstain = letters[opts.index(ABSTAIN_OPTION)]
    prompt = (
        "Answer this multiple-choice question. Use the paper search tools to "
        "find the relevant paper. Give brief reasoning, then end with a line "
        "exactly: 'Answer: <letter>'. If the papers do not contain enough "
        "information, pick the 'Insufficient information' option.\n\n"
        f"Question: {q['question']}\n\n"
        + "\n".join(f"{l}) {o}" for l, o in zip(letters, opts))
    )
    return {"prompt": prompt, "letters": letters, "correct": correct,
            "abstain": abstain, "options": dict(zip(letters, opts))}


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def parse_letter(text: str, letters: list[str], options: dict | None = None) -> str | None:
    """Extract the chosen option letter from the answer (robust to several
    formats; falls back to matching the stated answer TEXT against an option)."""
    valid = "".join(letters)
    # 1. explicit answer phrasings, anywhere (take the LAST occurrence)
    pats = [rf"answer\s*(?:is|:|\-|=)?\s*\(?([{valid}])\)?\b",
            rf"\b(?:option|choice|select|choose|pick)\s*\(?([{valid}])\)?\b",
            rf"\bthe\s+(?:correct\s+)?answer\s+is\s*\(?([{valid}])\)?\b"]
    hits = [m for p in pats for m in re.finditer(p, text, re.I)]
    if hits:
        return max(hits, key=lambda m: m.start()).group(1).upper()
    # 2. a lone/leading letter on any of the last few non-empty lines
    tail_lines = [l.strip() for l in text.splitlines() if l.strip()][-5:]
    for line in reversed(tail_lines):
        m2 = re.match(rf"\(?([{valid}])\)?[.):\-]", line, re.I) or \
             re.fullmatch(rf"\(?([{valid}])\)?", line, re.I)
        if m2:
            return m2.group(1).upper()
    # 3. fuzzy: the model wrote the ANSWER TEXT, not a letter -> map it back
    if options:
        tail = _norm(text[-600:])
        for letter, opt in options.items():
            n = _norm(opt)
            if len(n) >= 12 and n in tail:   # avoid matching very short options
                return letter
    return None


def _ask_chat(base_url: str, email: str, prompt: str, sock_timeout=60,
              deadline=900) -> tuple[str, bool]:
    """Stream one research chat and return the accumulated answer text.

    Two guards, because the SSE stream sends `: keepalive` comments every ~15s
    that reset the socket read timeout: (1) sock_timeout caps a single silent
    read; (2) deadline is an application-level wall-clock cap on the whole
    request so a generation that stalls-but-keeps-alive can't hang a worker
    forever. On deadline we return whatever content arrived (parsed best-effort).

    `deadline` was 300s until 2026-07-24. Every one of the 11 "unparseable"
    verdicts in the 2026-07-24 run was a deadline truncation (truncation was
    perfectly predictive: no truncated response ever parsed), and 6 of them had
    produced only whitespace, i.e. the agent was still inside its tool loop when
    the clock ran out. A single `source` read now costs ~20s (full text, thinking
    on), so a research turn doing several reads plus synthesis legitimately needs
    more than 5 minutes. 900s gives it room; a genuinely hung request is still
    bounded.
    """
    import time
    body = {"persona": "research", "ephemeral": True,
            "messages": [{"role": "user", "content": prompt}]}
    req = urllib.request.Request(
        base_url.rstrip("/") + "/api/chat/completions",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "X-Munin-Email": email,
                 "X-Munin-Ephemeral": "true", "Accept": "text/event-stream",
                 # cost guard: default OFF so routine runs never spend the paid
                 # Brave key / trip S2. For the intentional clean answer-track
                 # measurement (needs live search), run with MUNIN_EVAL_EGRESS=full.
                 "X-Munin-Egress": os.getenv("MUNIN_EVAL_EGRESS", "off")},
        method="POST")
    content, ev = "", None
    truncated = True  # flipped to False only if we see the terminal `done` event
    start = time.time()
    resp = urllib.request.urlopen(req, timeout=sock_timeout)
    try:
        for raw in resp:
            if time.time() - start > deadline:
                break
            line = raw.decode("utf-8", "replace").rstrip("\n")
            if line.startswith("event:"):
                ev = line[6:].strip()
            elif line.startswith("data:"):
                d = line[5:].strip()
                if not d or d == "[DONE]":
                    continue
                try:
                    obj = json.loads(d)
                except Exception:
                    continue
                if ev == "token" and isinstance(obj, dict) and obj.get("content"):
                    content += obj["content"]
            if ev == "done":
                truncated = False
                break
    finally:
        resp.close()
    # truncated=True means we cut off at the deadline before the model finished
    # (its final 'Answer: X' line may be missing) — the key unparseable signal.
    return content, truncated


def _score_one(q, base_url, email):
    mcq = build_mcq(q)
    try:
        text, truncated = _ask_chat(base_url, email, mcq["prompt"])
    except Exception as e:
        return {"qid": q["qid"], "verdict": "error", "detail": type(e).__name__}
    letter = parse_letter(text, mcq["letters"], mcq["options"])
    if letter is None:
        verdict = "unparseable"
    elif letter == mcq["correct"]:
        verdict = "correct"
    elif letter == mcq["abstain"]:
        verdict = "abstain"
    else:
        verdict = "incorrect"
    return {"qid": q["qid"], "verdict": verdict, "letter": letter,
            "correct": mcq["correct"], "truncated": truncated,
            "text_len": len(text), "text_tail": text[-600:]}


def run_answer(qc, *, base_url, email, results_root, n_resamples=1000,
               concurrency=4, limit=0):
    questions = load_litqa2()
    # answer track scores ALL questions (retrieval-in-corpus is not required to
    # answer; the model may still get it from web/S2), but we tag in-corpus.
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
    if limit:
        questions = questions[:limit]
    print(f"[litqa2-answer] {len(questions)} questions, concurrency={concurrency}")

    from concurrent.futures import as_completed
    results = [None] * len(questions)
    with ThreadPoolExecutor(max_workers=concurrency) as ex:
        futs = {ex.submit(_score_one, q, base_url, email): i
                for i, q in enumerate(questions)}
        done = 0
        for fut in as_completed(futs):
            i = futs[fut]
            results[i] = fut.result()
            done += 1
            if done % 20 == 0:
                print(f"  [answer] {done}/{len(questions)}")

    v = [r["verdict"] for r in results]
    n = len(v)
    correct = v.count("correct")
    incorrect = v.count("incorrect")
    abstain = v.count("abstain")
    unparse = v.count("unparseable")
    error = v.count("error")
    attempted = correct + incorrect  # excludes abstain/unparse/error
    # per-question 0/1 arrays for bootstrap
    acc_arr = [1.0 if r["verdict"] == "correct" else 0.0 for r in results]
    prec_arr = [1.0 if r["verdict"] == "correct" else 0.0
                for r in results if r["verdict"] in ("correct", "incorrect")]
    accuracy = single_bootstrap(acc_arr, n_resamples=n_resamples)
    precision = single_bootstrap(prec_arr, n_resamples=n_resamples) if prec_arr else None

    header = {
        "track": "litqa2-answer",
        "n": n, "correct": correct, "incorrect": incorrect,
        "abstain": abstain, "unparseable": unparse, "error": error,
        "attempted": attempted,
        "deadline_truncated": sum(1 for r in results if r.get("truncated")),
        "git_sha": _git_sha(),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "paperqa2_accuracy_published": PAPERQA2_ACCURACY,
        "caveat": ("NOT like-for-like vs PaperQA2: PaperQA2 was trained on LitQA2 "
                   "(train+eval), Munin's model (Qwen3.6-35B) was not. A loss is "
                   "expected and informative; parity is strong; a win remarkable. "
                   "Precision excludes abstentions + unparseable; accuracy counts "
                   "them as wrong."),
    }
    out_dir = os.path.join(results_root, "litqa2")
    os.makedirs(out_dir, exist_ok=True)
    # per-query accuracy (0/1 by qid) so the scorecard/compare can pair-bootstrap
    q_ids = [r["qid"] for r in results]
    sc_summary = {"accuracy": {"munin": accuracy}}
    if precision:
        sc_summary["precision"] = {"munin": precision}
    payload = {"header": header,
               "accuracy": accuracy,
               "precision": precision,
               "abstention_rate": abstain / n if n else 0.0,
               "per_query": {"munin": {"accuracy": acc_arr}},
               "qids": q_ids,
               "metric_keys": ["accuracy"],
               "sc_summary": sc_summary,
               "results": results}
    with open(os.path.join(out_dir, "answer.json"), "w") as fh:
        json.dump(payload, fh, indent=2)
    _write_answer_md(out_dir, payload)
    # Report deadline truncations SEPARATELY. Lumping them into "unparseable"
    # hides a harness limit as if it were a model failure: in the 2026-07-24 run
    # all 11 unparseables were truncations, which reads very differently.
    n_trunc = sum(1 for r in results if r.get("truncated"))
    print(f"[litqa2-answer] accuracy={accuracy['mean']:.3f} "
          f"precision={precision['mean']:.3f} abstain={abstain}/{n} "
          f"unparseable={unparse} (deadline-truncated={n_trunc}) "
          f"-> {out_dir}/answer.{{json,md}}")
    if n_trunc:
        print(f"[litqa2-answer] WARNING: {n_trunc}/{n} responses hit the "
              f"wall-clock deadline before answering. These score as wrong. "
              f"Raise `deadline` in _ask_chat if the agent needs longer.")
    return payload


def _write_answer_md(out_dir, payload):
    h = payload["header"]
    acc, prec = payload["accuracy"], payload["precision"]
    lines = [
        "# LitQA2 — answer track (end-to-end, research profile)", "",
        f"- N={h['n']} | correct={h['correct']} incorrect={h['incorrect']} "
        f"abstain={h['abstain']} unparseable={h['unparseable']} error={h['error']}",
        f"- git: `{h['git_sha']}` | {h['generated_at']}", "",
        "## Metrics (mean [95% CI])", "",
        "| metric | Munin | PaperQA2 (published) | Human experts (published) |",
        "|---|---|---|---|",
        f"| accuracy | {acc['mean']:.3f} [{acc['ci_low']:.3f}, {acc['ci_high']:.3f}] "
        f"| {PAPERQA2_ACCURACY:.3f} | {LITQA2_HUMAN_ACCURACY:.3f} |",
    ]
    if prec:
        lines.append(f"| precision (of attempted) | {prec['mean']:.3f} "
                     f"[{prec['ci_low']:.3f}, {prec['ci_high']:.3f}] "
                     f"| {PAPERQA2_PRECISION:.3f} | {LITQA2_HUMAN_PRECISION:.3f} |")
    lines += [f"| abstention rate | {payload['abstention_rate']:.3f} | n/a | n/a |",
              "", "## Caveat", "", h["caveat"]]
    with open(os.path.join(out_dir, "answer_summary.md"), "w") as fh:
        fh.write("\n".join(lines) + "\n")


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
