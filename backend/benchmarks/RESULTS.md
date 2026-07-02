# Munin eval suite — results log

Consolidated, committed record of every eval-suite run so far. The raw
per-query artifacts live under `results/` (gitignored, regenerable); this file
is the durable summary. Numbers are copied from the result JSONs, not memory.

**Provenance shared by all runs below**
- Generation model: `qwen3.6-35b-a3b` (Qwen3.6-35B-A3B-AWQ-4bit), vLLM.
- Retrieval encoder: **SPECTER-v1** (`sentence-transformers/allenai-specter`,
  768d), documents embedded as `title\n\nabstract` (matches production).
- Production corpus: Qdrant `papers`, ~68k papers (incl. the LitQA2 backfill).
- Metric = our `munin_bench.metrics`, verified bit-identical to `pytrec_eval`.
- CIs are 95% percentile bootstrap (1000 resamples, seed 42).

> **One-line story:** SPECTER-v1 is a weak retriever, and answer accuracy is
> bounded by retrieval recall (0.43 ≈ 0.44), so the highest-leverage lever is a
> better encoder, not a better LLM. Details under "Cross-cutting findings".

---

## Phase 3 — BEIR / SciFact (external validity)  · git `eb1cf73` · 2026-06-29

300 queries, 5183 docs. nDCG@10 [95% CI]:

| Retriever | nDCG@10 | note |
|---|---|---|
| BM25 | **0.652** [0.61, 0.70] | reproduces published BEIR BM25 (~0.665) → harness validated |
| SPECTER-dense | 0.479 [0.43, 0.53] | dated citation-embedder, weak on retrieval |
| citation-rerank (0.7/0.3) | 0.479 | degenerates to dense on BEIR (no graph) |
| RRF[BM25, SPECTER] | 0.621 [0.58, 0.67] | best Recall@100 (0.936) |

Significance (paired bootstrap, nDCG@10): SPECTER < BM25 (Δ−0.174, p≈0);
RRF > SPECTER (Δ+0.143, p≈0); RRF vs BM25 n.s. (Δ−0.031, p=0.10).
**Gate:** met via BM25-reproduces-published (the SPECTER>0.5 threshold was
optimistic; even canonical `[SEP]` SPECTER is 0.494). See RETRIEVAL-EVAL-SPEC
Phase 3.

---

## Phase 5 — LitQA2 retrieval  · git `360367d` · 2026-07-01

199/199 questions in-corpus, retrieve depth 20. Does the retriever surface the
source paper?

| Metric | AgentRetriever (production) | SPECTER-dense | citation-rerank |
|---|---|---|---|
| Recall@1 | 0.191 | 0.234 | 0.000 |
| Recall@5 | 0.369 | 0.379 | 0.000 |
| **Recall@10** | **0.442** [0.37, 0.51] | 0.452 [0.38, 0.53] | 0.000 |
| MRR | 0.277 [0.23, 0.33] | 0.308 [0.25, 0.37] | 0.001 |

Findings: (1) multi-query fan-out does **not** beat single-query dense
(Recall@10 p=0.71); (2) citation-rerank **collapses to ~0** — a real cold-start
effect: the LitQA2 sources were backfilled with 0 citations, so re-ranking
demotes them below older cited papers (a source ranked #1 by dense falls out of
top-20). Partly a backfill artifact (no CITES edges); not representative of
established papers.

---

## Phase 5 — LitQA2 answer (end-to-end, research profile)  · git `42b027b` · 2026-07-01

199 questions through the full agentic chat pipeline. 85 correct / 19 incorrect
/ 74 abstain / 21 unparseable.

| Metric | Munin | PaperQA2 (published) |
|---|---|---|
| Accuracy | 0.427 [0.36, 0.50] | 0.660 [verify] |
| Precision (of attempted) | **0.817** [0.74, 0.89] | ~0.88 |
| Abstention rate | 0.372 | — |

**NOT like-for-like:** PaperQA2 was trained on LitQA2; Munin's off-the-shelf
Qwen was not. `unparseable` (no clean `Answer: X`) counts as wrong for accuracy,
excluded from precision. Only the verdict was stored, so re-scoring the 21
unparseable needs a re-run.

---

## Cross-cutting findings

1. **Retrieval is the bottleneck, not reasoning.** Answer accuracy (0.427) ≈
   retrieval Recall@10 (0.442). When the paper is retrieved the model answers it
   well (precision 0.82) and abstains rather than guessing. → the lever is
   better retrieval, which lifts retrieval AND answers together.
2. **SPECTER-v1 is weak + used out-of-distribution** (embeds short questions,
   not title+abstract). A retrieval-tuned encoder (SPECTER2 / SciNCL / E5 / BGE)
   is the highest-confidence recall win. Untested — an encoder bake-off on
   `eval_*` collections would quantify it without a production re-embed.
3. **`title\n\nabstract` vs `[SEP]` costs ~1.5 nDCG@10** on SciFact (0.479 vs
   0.494). Production embedding could switch to the tokenizer `[SEP]` token
   (needs a corpus re-embed). See memory `project_specter_sep_finding`.
4. **The agentic fan-out did not rescue recall** here (answer acc ≈ single-pass
   recall) — but that's a hint, not a measurement. The harness's marginal value
   is Track D (bare vs vanilla-RAG vs agentic), not yet built.

---

## Encoder bake-off — BEIR SciFact  · git `5f9ef15` · 2026-07-02

In-memory, apples-to-apples (same `title\n\nabstract` docs, cosine, our
metrics). Answers "would a better encoder help recall?" **Yes, dramatically.**

| Encoder | nDCG@10 | Recall@10 | Recall@100 | MRR | Δ nDCG@10 vs SPECTER (p) |
|---|---|---|---|---|---|
| SPECTER-v1 (current) | 0.479 | 0.637 | 0.840 | 0.441 | — |
| SciNCL | 0.564 | 0.723 | 0.908 | 0.530 | +0.085 (~0) |
| E5-large-v2 | 0.722 | 0.844 | 0.963 | 0.692 | +0.243 (~0) |
| **BGE-large-en-v1.5** | **0.746** | **0.873** | 0.948 | 0.716 | **+0.268 (~0)** |

BGE-large lifts nDCG@10 +56% over SPECTER-v1 and **beats BM25 (0.652)**, which
SPECTER lost to. Retrieval-tuning (E5/BGE) matters more than scientific
pretraining (SciNCL). Caveats: SciFact != Munin's corpus (direction very likely
holds, magnitude TBD); BGE/E5 are 1024-d (SPECTER 768-d) so deploying means a
Qdrant collection recreate + full 68k re-embed. Highest-ROI change found:
lifts retrieval AND (via the recall bound) answer accuracy together.

Not yet done: confirm the winner on Munin's own corpus / LitQA2 (needs a one-off
re-embed of the candidate pool with BGE/E5).

---

## Reproduce

```bash
cd backend/benchmarks
export PYTHONPATH=$HOME/.cache/munin_bench_deps:.   # SPECTER stack + rank_bm25/ir_datasets/pyarrow
export NEO4J_PASSWORD=...                           # from the cluster .env
PY=/opt/munin/services/pipeline/venv/bin/python

$PY -m pytest tests/                                              # Phase 1-2 unit gates
MUNIN_BENCH_SPECTER_DEVICE=cpu $PY -m munin_bench.pipelines.run_beir --subset scifact
$PY -m munin_bench.pipelines.run_litqa2 --track retrieval
$PY -m munin_bench.pipelines.run_litqa2 --track answer --concurrency 1   # concurrency<=vLLM max-num-seqs
MUNIN_BENCH_SPECTER_DEVICE=cpu $PY -m munin_bench.pipelines.run_bakeoff --subset scifact
```

Not yet run: BEIR nfcorpus/scidocs/trec-covid; Phase 4 local pool (deferred,
insufficient usage); master-plan Tracks B–F. Status table: `README.md`.
