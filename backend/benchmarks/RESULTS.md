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

**What the number is and is not.** (1) One arm; faithfulness is most meaningful as
the Track D **paired** comparison (bare/RAG/agentic — the scorer + per-arm capture
files are built for it). (2) Claim extraction (2026-07-09) replaced raw
sentence-split; the number barely moved (0.378 -> 0.356), so the gap is ROBUST,
not a narration artifact. (3) The context union is generous (all retrieval
results), which if anything INFLATES support. `% fully supported` = 0 is length
math, not a finding.

**Crucially, the un-grounded ~65% is NOT hallucination** (settled by two later
results): the T1a null (adding paper_search excerpts did not move grounding, so it
is not an evidence-availability problem) and **Track C1 (0/100 confabulations on
nonexistent papers)**. So the un-supported claims are faithful cross-source
synthesis + MiniCheck literalness (a claim entailed by two passages jointly scores
"unsupported"), not fabrication. Scorecards `2026-07-09_faithfulness-agentic-live`
(baseline), `-t1a`, `-cap`.

---

## Harness iteration — over-tooling & grounding  · 2026-07-09

Three deploy-measured experiments to improve the agentic harness, each gated on
the routing anchor eval (>= 0.950) + the Track B arm. A disciplined arc: two null/
negative results and one win.

| lever | type | result |
|---|---|---|
| research fragment: deep -> **medium** default | prompt | **backfired** — paired over-tooling median 10.5 -> 20.5 calls (thinner baseline induces more compensatory search). Reverted. |
| "<=3-4 follow-ups" wording | prompt | did not bite (over-tooling flat). Kept (harmless). |
| **T1a** paper_search abstract excerpts | code | **null** for grounding (0.356 -> 0.303, CIs overlap) and over-tooling. So the grounding gap is NOT evidence-availability. |
| **over-tooling code cap** (`CHAT_MAX_TOOL_CALLS=30`) | code | **works** — tool_calls/answer max 43 -> 31, p90 40 -> 30, >30 tail 9/40 -> 2/40; grounding held (0.331), 0 failures. |

Lessons (evidence-backed): over-tooling is not fixable by prompt (needs the code
cap — shipped, routes into the existing wrap-up synthesis); grounding is not an
evidence-availability problem (T1a null) — it is model synthesis + judge
literalness (Track C1). Post-deploy routing anchor **0.963** (no regression; one
`known_doi_read` S2-branch side-effect from a T3 description, fixed).
Scorecards: `2026-07-09_faithfulness-agentic-live-{t1a,cap}`,
`2026-07-09_t2-postdeploy`, `2026-07-10_postcap-t3`.

## Track C1 — corpus-grounded abstention (fabricated papers)  · 2026-07-10

"Munin knows when the corpus does not contain the answer" (RQ-M1, corpus-absence
half). The private-corpus abstention regime no public benchmark covers. Set: 100
frozen fabricated items — 80 Crossref-verified-nonexistent DOIs + 20 nonexistent-
paper-by-description, in the group's fields; zero collide with the 67,675-DOI
corpus, so any local citation of them is a confabulation.

| metric | value |
|---|---|
| **abstain / correct-refusal rate** | **0.98 [0.95, 1.00]** (manual review of 2 residuals: also refusals -> ~100%) |
| **confabulated LOCAL citations** (real corpus DOI cited as the fake paper) | **0 / 100** (fully automatic, judge-free) |

Munin calls read_paper on the fake DOI (Crossref 404s), often searches, then
refuses / asks for a corrected identifier; it never invents findings or
substitutes a real local paper. **Strongly supports the anti-hallucination claim**
and settles the Track B question: the un-grounded content is not fabrication.
NOTE: this is the corpus-ABSENT extreme; OVER-abstention is Track C2 below.
Scorecard `2026-07-10_abstention-c1-fabricated`.

## Track C2b — paired shadow-corpus abstention  · 2026-07-10

The complementary paired test: 50 single-source-DOI LitQA2 questions asked twice —
against the live corpus (`papers_bge`, :8080, source PRESENT) and an isolated
second retrieval instance on a shadow collection (`papers_shadow` = papers_bge
minus the 49 sources, :8081, source ABSENT). Shadow verified (removed papers 8/12
in live top-20, **0/12 in shadow**).

| | present (source in) | absent (source removed) |
|---|---|---|
| accuracy | 0.40 | 0.34 |
| abstain rate | **0.48** | 0.50 |

On the 20 answerable questions (present-correct), removing the source gave: **4
correct-abstention, 12 still-correct, 4 wrong.**

**Key finding — a confound that is itself informative.** Removing the local source
rarely triggers abstention because LitQA2 questions are answerable WITHOUT it (the
paper is likely in Qwen's training; the model also web/S2-searches). So the paired
answer-flip is NOT a clean corpus-grounded-abstention measure - `correct_abstention`
0.20 understates calibration (12/16 non-abstentions were genuinely CORRECT).
Reportable, un-confounded: **over-abstention 48%** (abstains even WITH the source -
the real miscalibration; usefulness cost, vs ~0 confabulation in C1) and
over-confidence-on-removal 4/20. Also: answers are NOT purely corpus-grounded
(12/20 correct from external knowledge), which explains part of Track B's
"un-grounded" fraction. A clean C2 needs questions answerable ONLY from the local
corpus (not in the base model / web) - hard to guarantee; **C1 (fabricated) stays
the clean abstention signal.** Scorecard `2026-07-10_abstention-c2-shadow`.

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

# Track C1 abstention (fabricated papers)
$PY -m munin_bench.abstention.fabricate --n 100                       # freeze the set (Crossref-verified)
PYTHONPATH=$HOME/.cache/munin_bench_deps:. $PY -m munin_bench.abstention.run_c1 \
  --base-url http://127.0.0.1:8080 --email litqa2-eval@localhost --date <YYYY-MM-DD>

# Track C2b paired shadow-corpus abstention
PYTHONPATH=$HOME/.cache/munin_bench_deps:. $PY -m munin_bench.abstention.build_shadow --n 50   # build papers_shadow + freeze questions
# varghele brings up the shadow retrieval instance on :8081 (docker/docker-compose.shadow.yml)
PYTHONPATH=$HOME/.cache/munin_bench_deps:. $PY -m munin_bench.abstention.run_c2 --arm present --base-url http://127.0.0.1:8080 --email ... --date <D>
PYTHONPATH=$HOME/.cache/munin_bench_deps:. $PY -m munin_bench.abstention.run_c2 --arm absent  --base-url http://127.0.0.1:8081 --email ... --date <D>  # writes paired scorecard
```
