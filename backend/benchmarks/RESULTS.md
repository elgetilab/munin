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

## Encoder bake-off — LitQA2 pool (Munin corpus)  · git `2955824` · 2026-07-02

Confirmation on Munin's OWN data: rank each LitQA2 source paper among a shared
pool of 190 real source papers + 5000 random corpus papers (same pool for every
encoder). Uses our papers + LitQA2 queries. **The SciFact win holds.**

| metric | specter_v1 | scincl | e5_large_v2 | bge_large |
|---|---|---|---|---|
| Recall@1 | 0.392 | 0.432 | 0.641 | **0.661** |
| Recall@10 | 0.663 | 0.678 | 0.817 | **0.837** |
| MRR | 0.493 | 0.518 | 0.715 | **0.734** |

Significance vs SPECTER-v1 (Recall@10, paired bootstrap): BGE +0.173 (p≈0),
E5 +0.153 (p≈0), **SciNCL +0.015 (p=0.70, n.s.)**. So the lever is a general
SOTA retriever (BGE/E5), NOT the scientific SPECTER successor. Absolute recall
is inflated by the 5190-doc pool (SPECTER's full-corpus LitQA2 Recall@10 was
0.44); the relative comparison is valid.

**Conclusion:** deploying BGE-large (or E5-large) as the paper encoder is
strongly evidence-backed — it substantially lifts retrieval on our corpus and,
via the recall bound, should pull LitQA2 answer accuracy up. Cost: recreate the
Qdrant `papers` collection at 1024-d + re-embed 68k papers + update query
embedding in `paper_search`. That is the recommended production change; full-
corpus magnitude confirmed only after the re-embed.

## Encoder migration Phase A - full-corpus validation  · git `bc35b4d` · 2026-07-03

BGE-large re-embed of ALL 68k papers (`papers_bge`, 1024-d), LitQA2 retrieval
over the full corpus, `compare` vs the committed `baseline-specter-v1`
scorecard (paired bootstrap, same 199 questions). **Gate PASSED, decisively.**

| system | metric | SPECTER-v1 | BGE-large | Δ (p) |
|---|---|---|---|---|
| AgentRetriever (prod) | Recall@1 | 0.191 | 0.487 | +0.30 (~0) |
| | Recall@10 | 0.437 | 0.729 | +0.29 (~0) |
| | MRR | 0.276 | 0.573 | +0.30 (~0) |
| SPECTER-dense | Recall@10 | 0.447 | 0.691 | +0.24 (~0) |

The FULL-corpus gain (+0.29 Recall@10) is LARGER than the 5k-pool gain (+0.17):
with 68k distractors SPECTER-v1 cannot pick the source out of the noise, so a
better encoder helps MORE at scale, not less. AgentRetriever now surfaces the
source in the top-10 73% of the time (was 44%). Since answer accuracy tracks
Recall@10, this projects LitQA2 answer accuracy toward ~0.73 (past PaperQA2's
0.66) - to be measured for real in Phase C. Scorecards:
`scorecards/2026-07-03_{baseline-specter-v1,bge-large}.json`. Migration is
strongly justified; proceed to Phase B (production cutover) per
`todo_v2/done/ENCODER-MIGRATION-PLAN.md`.

## Encoder migration Phase C - end-to-end answer, post-cutover (final)  · 2026-07-06

BGE deployed live; answer track re-run through the full agentic chat pipeline
for BOTH encoders with a FIXED parser (same 199 questions, paired bootstrap).
SPECTER measured via a temporary rollback so it's apples-to-apples.

| metric | SPECTER-v1 | BGE-large | Δ (paired) | p |
|---|---|---|---|---|
| accuracy | 0.422 | 0.497 | +0.075 [0.015, 0.136] | **0.028** |
| precision (attempted) | 0.832 | 0.853 | +0.022 | - |
| verdicts | 84 correct / 97 abstain / 17 wrong | 99 correct / 80 abstain / 17 wrong | +15 correct, -17 abstain | |

**The answer gain is real and significant** (+7.5 pts, p=0.028): better retrieval
lets the model find the source and abstain less (97 -> 80). But it is MODEST
relative to the retrieval jump (Recall@10 +0.29). PaperQA2's 0.66 is still ahead.

METHOD NOTE (important): the first BGE run showed +0.05 n.s. - a HARNESS BUG. The
answer parser dropped ~17% of BGE answers (and ~10% of SPECTER's) as
"unparseable"; those were the model reasoning long then abstaining, truncated at
the 180s deadline before the final line. Fix = a hardened parser (more formats +
fuzzy answer-text match) + a 300s deadline + saving the full response text. The
recovered cases became ABSTENTIONS, not correct answers, so accuracy barely
moved but the comparison became honest. Runs are ~4-5h/arm at the 300s deadline
(long answers now complete instead of truncating).

**Revised thesis:** retrieval was the dominant bottleneck (Phase 5), but not the
ONLY one - the model abstains on ~40% of questions even with good retrieval, so
a large recall gain yields only a modest accuracy gain. The naive "accuracy ~=
recall -> ~0.73" projection was wrong. Scorecards:
`2026-07-06_answer-{specter-v1-v2,bge-large-v2}.json`.

---

## Track B — answer faithfulness (local MiniCheck)  · git `73ab062` · 2026-07-08

Local, privacy-preserving faithfulness judge: **MiniCheck-Flan-T5-Large** (<1B)
scores each answer sentence's support against the retrieved contexts (replicates
the authors' exact inference; no data leaves the premises).

**Judge validated (B2)** on RAGTruth (ungated, GitHub), 120 responses:

| task | AUROC | BA@0.5 |
|---|---|---|
| **QA** (Munin's task) | **0.950** | 0.725 |
| Summary / Data2txt | 0.708 / 0.723 | 0.650 / 0.525 |

Gate (QA-AUROC ≥ 0.70) passed → Flan-T5-Large sufficient, no 7B escalation.
Scorecard `2026-07-08_faithfulness-judge-ragtruth`.

**Interim single-arm faithfulness (B3/B4)** — live agentic arm, 40 LitQA2
questions:

| metric | value (95% CI) |
|---|---|
| **% claims supported** (macro, length-robust) | **0.356 [0.272, 0.425]** |
| mean faithfulness (per-claim support) | 0.404 [0.347, 0.450] |
| per-answer grounding | 14.6 real claims, 82.5 contexts / answer |

(claim-extraction refined 2026-07-09: dropping process-narration/questions/
headers moved the number 0.378 -> 0.356, CIs overlap heavily — the grounding gap
is ROBUST to claim extraction, not a narration artifact. `2026-07-09` scorecard
supersedes `2026-07-08` as the dashboard baseline.)

**CAVEAT — this absolute value is interim, NOT a paper figure.** (1) One arm;
faithfulness is meaningful as the Track D **paired** comparison (bare/RAG/agentic
— the scorer + per-arm capture files are built for it). (2) We sentence-split the
whole answer, so reasoning/hedge/transition sentences are scored and deflate the
number; a real claim-extraction step is the refinement before any headline
figure. (3) The context union is generous (all retrieval results), which if
anything INFLATES support — yet it is still 0.38, so a substantial share of
agentic-answer content is un-retrieved synthesis (directly relevant to Track C
abstention and Track D harness-value). `% fully supported` = 0 is length math at
~22 claims/answer, not a finding. Scorecard `2026-07-08_faithfulness-agentic-live`.

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
insufficient usage); master-plan Tracks C–F (Track B judge built + validated,
one interim arm above). Status table: `README.md`.

```bash
# Track B faithfulness (judge validation + one live arm)
$PY -m munin_bench.faithfulness.smoke_minicheck                       # B1 sanity
$PY -m munin_bench.faithfulness.validate_ragtruth --n-per-cell 20 --date <YYYY-MM-DD>   # B2
MUNIN_BENCH_ENTAILMENT_DEVICE=cuda:0 $PY -m munin_bench.faithfulness.faithfulness_runner \
  --base-url http://127.0.0.1:8080 --email litqa2-eval@localhost \
  --arm agentic-live --limit 40 --concurrency 1 --date <YYYY-MM-DD>   # B3+B4 (capture on CPU-ok, score GPU)
```
