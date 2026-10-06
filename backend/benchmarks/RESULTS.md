# Munin eval suite - results log

Consolidated, committed record of every eval-suite run so far. The raw
per-query artifacts live under `results/` (gitignored, regenerable); this file
is the durable summary.

> **`ablation_runs/`, `c1_runs/`, `c2_runs/` and `faithfulness_runs/` are NOT
> regenerable**, despite being gitignored alongside `results/`. They hold the
> per-query verdicts behind the headline numbers, the committed scorecards carry
> only `per_arm` and `deltas` (no per-query arrays), and the model that produced
> them is no longer deployed. Until 2026-09-15 `run_arm.run()` overwrote
> `ablation_runs/<arm>.json` in place, so a single re-run destroyed the only copy
> and with it any paired old-vs-new comparison. **Since 2026-09-15 every arm
> writes to `ablation_runs/<tag>/` (`--tag` / `MUNIN_ABLATION_TAG`), resumes
> from `<arm>.capture.jsonl`, and the flat 08-26 Qwen3.8 files live under
> `ablation_runs/qwen38-27b/`, which is what an untagged reader resolves to.**
> Paths written as `ablation_runs/<arm>.json` in sections dated before that
> mean `ablation_runs/qwen38-27b/<arm>.json` today. The Qwen3.8 set is archived
> off-machine as `munin-bench-artifacts-2026-09-15-qwen38-complete.tar.gz`
> (sha256 `3aa5d909…bfbc`, 36 files, verified on arrival). The 2026-07-27 clean run is archived **off-machine**
> in the operator's backup (5.3 MB tar.gz + sha256 + manifest, verified on
> arrival 2026-08-25), with a working copy on the cluster. Take a fresh
> off-machine copy before any re-run that writes these directories. Numbers are copied from the result JSONs, not memory.

**Provenance shared by all runs below**
- Generation model: this changed on 2026-08-26. Every section up to and
  including T11 (2026-07-27) ran on `qwen3.6-35b-a3b`
  (Qwen3.6-35B-A3B-AWQ-4bit, MoE, retired from production 2026-08-25). From
  "Model swap ... Track D re-run" on, the headline sections are on
  **`qwen3.8-27b`** (`cyankiwi/Qwen3.8-27B-AWQ-INT4`, dense), which is what
  production serves: Track D, per-arm faithfulness and T11 (08-26), the
  standalone answer track (09-14), C1 (09-14), C2b and risk-coverage
  (09-15), the `egress=off` arm (09-15). Two further backbones ran the
  whole suite as eval-only instances beside production: `gpt-oss-20b`
  (09-16, a different lab) and the retired `qwen3.6-35b-a3b` itself
  (09-17, so the Qwen3.6 numbers to quote are same-protocol ones, and the
  July sections are the July harness). Retrieval sections are
  model-independent by construction. `PAPER.md` says which model each
  headline claim is on.
- Retrieval encoder: this changed partway. Phase 3-5 + the bake-offs used
  **SPECTER-v1** (`allenai-specter`, 768d, corpus `papers`); the encoder
  migration cut over to **BGE-large-en-v1.5** (1024d, corpus `papers_bge`) -
  **now live in production**. Each section states the encoder it used; the old
  `papers`/SPECTER collection is retained only as a rollback.
- Metric = our `munin_bench.metrics`, verified bit-identical to `pytrec_eval`.
- CIs are 95% percentile bootstrap (1000 resamples, seed 42).

> **One-line story (updated 2026-08-26):** retrieval was the FIRST bottleneck
> (SPECTER answer acc 0.43 ≈ recall 0.44), fixed by the BGE-large migration
> (Recall@10 0.44 -> 0.73, answer acc -> 0.497). The bigger lever came later: the
> flat tool loop was replaced by named agents, and `source(mode=qa)` reading FULL
> text instead of `read_paper`'s summarise-and-discard collapsed over-abstention
> from ~42% to 6% and took **LitQA2 answer accuracy 0.497 -> 0.864** (precision
> 0.92, past PaperQA2's 0.66). So the reading path, not retrieval, was the real
> ceiling on single-source answering; retrieval/corpus now bounds only BREADTH
> (distinct sources per Deep Research report). The harness ablation then closed
> the loop on the central claim: on the finished architecture, **agentic 0.839 vs
> bare 0.302 vs naive-RAG 0.171**, harness value **+0.538 [0.457, 0.618]
> p<0.001** (2026-07-27, n=199 paired, Qwen3.6), with tool-failure **recovery
> 1.000**. **Superseded as the headline on 2026-08-26** by the same three arms
> on the production model Qwen3.8-27B: **agentic 0.874 vs bare 0.387 vs RAG
> 0.211**, harness value **+0.487 [0.407, 0.568]**, recovery still 1.000.
> **The reading of the +0.538 → +0.487 move was revised on 2026-09-17**, when
> the retired Qwen3.6 checkpoint came back as an eval instance and ran the
> same protocol: agentic **0.869** vs bare 0.337 vs RAG 0.126, harness value
> **+0.533 [0.452, 0.613]**. The bare-arm budget defect was worth ~0.035 on
> the bare arm and the harness value held; the two Qwen backbones reach the
> same ceiling inside the harness (question-paired −0.005, p = 0.93) and
> differ only outside it, so the smaller delta on Qwen3.8 is its higher bare
> floor, not a correction of an inflated figure (see "Retired backbone
> re-run"). Full arc below.

---

## Phase 3 - BEIR / SciFact (external validity)  · git `eb1cf73` · 2026-06-29

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

## Phase 5 - LitQA2 retrieval  · git `360367d` · 2026-07-01

199/199 questions in-corpus, retrieve depth 20. Does the retriever surface the
source paper?

| Metric | AgentRetriever (production) | SPECTER-dense | citation-rerank |
|---|---|---|---|
| Recall@1 | 0.191 | 0.234 | 0.000 |
| Recall@5 | 0.369 | 0.379 | 0.000 |
| **Recall@10** | **0.442** [0.37, 0.51] | 0.452 [0.38, 0.53] | 0.000 |
| MRR | 0.277 [0.23, 0.33] | 0.308 [0.25, 0.37] | 0.001 |

Findings: (1) multi-query fan-out does **not** beat single-query dense
(Recall@10 p=0.71); (2) citation-rerank **collapses to ~0** - a cold-start
effect: the LitQA2 sources were backfilled with 0 citations, so re-ranking
demotes them below older cited papers (a source ranked #1 by dense falls out of
top-20). Partly a backfill artifact (no CITES edges); not representative of
established papers.

> **The cold-start explanation above is INCOMPLETE (revised 2026-07-28).** On
> LitSearch, citation-rerank collapses just as badly (−0.368 nDCG@10, p≈0) on a
> corpus with a *dense* citation graph (344k edges), where cold-start cannot
> apply. The underlying cause is a score-scale mismatch in the re-rank formula
> that is present regardless of graph density. Cold-start compounded it here
> but did not cause it. See the T8 LitSearch section below.

---

## Phase 5 - LitQA2 answer (end-to-end, research profile)  · git `42b027b` · 2026-07-01

199 questions through the full agentic chat pipeline. 85 correct / 19 incorrect
/ 74 abstain / 21 unparseable.

| Metric | Munin | PaperQA2 (published) |
|---|---|---|
| Accuracy | 0.427 [0.36, 0.50] | 0.660 |
| Precision (of attempted) | **0.817** [0.74, 0.89] | 0.852 |
| Abstention rate | 0.372 | - |

**PaperQA2 baseline** (accuracy 0.660, precision 0.852, n=248): verified against
the primary source; full provenance and the human-expert comparison are in the
2026-07-24 section below. This table previously carried `0.660 [verify]` and
precision "~0.88"; the published precision is **0.852**, corrected 2026-07-27.

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
   is the highest-confidence recall win. Untested - an encoder bake-off on
   `eval_*` collections would quantify it without a production re-embed.
3. **`title\n\nabstract` vs `[SEP]` costs ~1.5 nDCG@10** on SciFact (0.479 vs
   0.494). Production embedding could switch to the tokenizer `[SEP]` token
   (needs a corpus re-embed). See memory `project_specter_sep_finding`.
4. **The agentic fan-out did not rescue recall** here (answer acc ≈ single-pass
   recall) - but that's a hint, not a measurement. The harness's marginal value
   is Track D (bare vs vanilla-RAG vs agentic) - since built, and it confirmed
   the hint emphatically: +0.538 harness value on the finished architecture.

---

## Encoder bake-off - BEIR SciFact  · git `5f9ef15` · 2026-07-02

In-memory, apples-to-apples (same `title\n\nabstract` docs, cosine, our
metrics). Answers "would a better encoder help recall?" **Yes, dramatically.**

| Encoder | nDCG@10 | Recall@10 | Recall@100 | MRR | Δ nDCG@10 vs SPECTER (p) |
|---|---|---|---|---|---|
| SPECTER-v1 (current) | 0.479 | 0.637 | 0.840 | 0.441 | - |
| SciNCL | 0.564 | 0.723 | 0.908 | 0.530 | +0.085 (~0) |
| E5-large-v2 | 0.722 | 0.844 | 0.963 | 0.692 | +0.243 (~0) |
| **BGE-large-en-v1.5** | **0.746** | **0.873** | 0.948 | 0.716 | **+0.268 (~0)** |

BGE-large lifts nDCG@10 +56% over SPECTER-v1 and **beats BM25 (0.652)**, which
SPECTER lost to. Retrieval-tuning (E5/BGE) matters more than scientific
pretraining (SciNCL). Caveats: SciFact != Munin's corpus (direction very likely
holds, magnitude TBD); BGE/E5 are 1024-d (SPECTER 768-d) so deploying means a
Qdrant collection recreate + full 68k re-embed. Highest-ROI change found:
lifts retrieval AND (via the recall bound) answer accuracy together.

## Encoder bake-off - LitQA2 pool (Munin corpus)  · git `2955824` · 2026-07-02

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
strongly evidence-backed - it substantially lifts retrieval on our corpus and,
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
`../../docs/paper-track/done/ENCODER-MIGRATION-PLAN.md`.

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

## LitQA2 answer - post agent-architecture  · git `9f0c02a` · 2026-07-24

The single biggest answer-accuracy move in the suite, and it is NOT an encoder
or retrieval change. Between 2026-07-06 and 2026-07-24 the flat MCP tool loop was
replaced by the four named agents (source / search / compute / deep_research).
The load-bearing one for this benchmark is `source(mode=qa)`, which reads the
**full paper text** in one LLM call instead of `read_paper`'s
summarise-and-discard. Track D had shown the discard step was the bottleneck: a
full-text oracle flipped 9/11 over-abstentions to correct (0.82 ceiling). The
`source` agent was built to deliver that oracle in production.

Same 199 questions, same model (`qwen3.6-35b-a3b`), same encoder (BGE-large),
same runner + scoring code as the 2026-07-06 baseline (verified by git log on
`litqa2_runner.py`).

| metric | 2026-07-06 baseline | 2026-07-24 (900s) | Δ |
|---|---|---|---|
| accuracy | 0.497 [0.432, 0.563] | **0.864 [0.819, 0.910]** | +0.367 |
| precision (attempted) | 0.853 (n=116) | 0.920 (n=187) | +0.067 |
| withheld (abstain+unparse) | 83/199 (42%) | 12/199 (6%) | -71 |
| verdicts | 99 correct | 172 correct / 15 wrong / 12 abstain / 0 unparse | |

The CIs are disjoint (baseline tops out at 0.563). **This clears the 0.82
architectural ceiling (0.864).**

Published LitQA2 baselines, VERIFIED 2026-07-25 against the primary source
(Skarlinski et al. 2024, arXiv:2409.13740v2 - accuracy = correct / all asked,
precision = correct / answered, identical to our definitions):

| system | accuracy | precision |
|---|---|---|
| **Munin (2026-07-24)** | **0.864 [0.819, 0.910]** | 0.920 |
| PaperQA2 | 0.660 ± 0.012 (n=3) | 0.852 ± 0.011 |
| Human experts | 0.677 ± 0.119 (n=9) | 0.738 ± 0.096 |

Munin's accuracy exceeds both PaperQA2 (0.660) and the human-expert mean (0.677),
and its precision (0.920) exceeds both. Read this with the like-for-like caveat
firmly attached: **PaperQA2 was trained on LitQA2 and Munin's Qwen3.6 was not, so
the accuracy comparison flatters Munin**; the human number carries a huge SD
(±0.119) on n=9; and our 0.864 has the temperature churn documented below. It is
a strong, real result, not a clean "beats humans" headline.

DEADLINE, and why it moved again. The Phase C run used a 300s wall-clock deadline.
`source(mode=qa)` reads full text with thinking ON (~20s/read after the
2026-07-23 findings-mode fix), so a research turn doing several reads now
legitimately needs more than 5 minutes. A first full run at 300s scored **0.814**
but truncated 11 answers (all counted wrong). Raising the deadline to 900s
(`f4558c4`) and re-running clean gave 0.864 with **0 truncations, 0
unparseable**. The 300s and 900s runs are BOTH valid; use 0.814 only if comparing
to the 300s-era baseline directly, 0.864 as the current headline.

STOCHASTICITY CAVEAT (measured, not assumed). Comparing the 300s and 900s runs
question-by-question, 34/199 verdicts changed - and only 11 of those were the
recovered truncations. 6 correct->incorrect and 6 incorrect->correct flipped
purely from temperature-0.7 resampling. The net gain is real (CI floor rose
0.759 -> 0.819) but any single-question or sub-3-point delta on this benchmark is
inside the noise floor. Trust aggregates, not individual runs.

Scorecard: `2026-07-24_answer-full-900s.json`. The 300s comparison run is
preserved off-tree.

**Revised thesis (supersedes Phase C's).** Phase C concluded retrieval was the
dominant bottleneck and the model's ~40% abstention was a hard floor. That floor
was NOT the model - it was `read_paper` discarding the text that held the answer.
Giving the model the full text (source-qa) collapsed over-abstention from ~42% to
6% and roughly doubled accuracy. Retrieval quality still bounds BREADTH (how many
distinct sources a Deep Research report can cite - see `docs/agent-track/TODO.md`), but on
single-source MCQ answering the reading path, not retrieval, was the ceiling.

---

## Track B - answer faithfulness (local MiniCheck)  · git `73ab062` · 2026-07-08

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

**Interim single-arm faithfulness (B3/B4)** - live agentic arm, 40 LitQA2
questions:

| metric | value (95% CI) |
|---|---|
| **% claims supported** (macro, length-robust) | **0.356 [0.272, 0.425]** |
| mean faithfulness (per-claim support) | 0.404 [0.347, 0.450] |
| per-answer grounding | 14.6 real claims, 82.5 contexts / answer |

(claim-extraction refined 2026-07-09: dropping process-narration/questions/
headers moved the number 0.378 -> 0.356, CIs overlap heavily - the grounding gap
is ROBUST to claim extraction, not a narration artifact. `2026-07-09` scorecard
supersedes `2026-07-08` as the dashboard baseline.)

**What the number is and is not.** (1) One arm; faithfulness is most meaningful as
the Track D **paired** comparison (bare/RAG/agentic - the scorer + per-arm capture
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

## Harness iteration - over-tooling & grounding  · 2026-07-09

Three deploy-measured experiments to improve the agentic harness, each gated on
the routing anchor eval (>= 0.950) + the Track B arm. A disciplined arc: two null/
negative results and one win.

| lever | type | result |
|---|---|---|
| research fragment: deep -> **medium** default | prompt | **backfired** - paired over-tooling median 10.5 -> 20.5 calls (thinner baseline induces more compensatory search). Reverted. |
| "<=3-4 follow-ups" wording | prompt | did not bite (over-tooling flat). Kept (harmless). |
| **T1a** paper_search abstract excerpts | code | **null** for grounding (0.356 -> 0.303, CIs overlap) and over-tooling. So the grounding gap is NOT evidence-availability. |
| **over-tooling code cap** (`CHAT_MAX_TOOL_CALLS=30`) | code | **works** - tool_calls/answer max 43 -> 31, p90 40 -> 30, >30 tail 9/40 -> 2/40; grounding held (0.331), 0 failures. |

Lessons (evidence-backed): over-tooling is not fixable by prompt (needs the code
cap - shipped, routes into the existing wrap-up synthesis); grounding is not an
evidence-availability problem (T1a null) - it is model synthesis + judge
literalness (Track C1). Post-deploy routing anchor **0.963** (no regression; one
`known_doi_read` S2-branch side-effect from a T3 description, fixed). That
number is the tool-trajectory pass rate over 16 anchors x 5 reps, not a
profile-routing accuracy; the router's profile pick is a non-gating diagnostic
that stood at 0.70 in the same run, and the most recent anchor run
(2026-07-25, after the tool consolidation) is 0.835. See
`docs/ROUTING-EVAL-FACTS.md`.
Scorecards: `2026-07-09_faithfulness-agentic-live-{t1a,cap}`,
`2026-07-09_t2-postdeploy`, `2026-07-10_postcap-t3`.

## T8 - LitSearch retrieval benchmark  · 2026-07-28

LitSearch (Ajith et al. 2024, arXiv 2407.18940): **597 real natural-language
literature-search queries** over a **64,183-paper** S2ORC corpus. The closest
public benchmark to Munin's actual usage - finding papers from a question,
rather than BEIR/SciFact's claim-verification framing. Encoder is
**BGE-large-en-v1.5, the production encoder** (the 2026-06/07 BEIR tables above
used SPECTER-v1 - do not compare across them). Binary relevance, mean 1.07
relevant papers per query. Isolated `eval_litsearch` collection.

| retriever | nDCG@10 [95% CI] | R@10 | R@100 | MRR | Δ nDCG@10 vs dense (p) |
|---|---|---|---|---|---|
| BM25 | 0.378 [0.345, 0.413] | 0.511 | 0.699 | 0.349 | −0.107 [−0.137, −0.077] (0.0000) |
| **BGE-dense (production)** | **0.485 [0.453, 0.516]** | **0.637** | 0.829 | 0.451 | - |
| citation-rerank 0.7/0.3 (**fixed**) | 0.469 [0.437, 0.503] | 0.609 | 0.829 | 0.442 | −0.016 [−0.030, −0.003] (0.014) |
| citation-rerank 0.7/0.3 (*pre-fix*) | *0.117 [0.097, 0.138]* | *0.195* | *0.829* | *0.116* | *−0.368 [−0.405, −0.331] (0.0000)* |
| RRF[BM25, BGE] | 0.490 [0.455, 0.526] | 0.628 | **0.830** | 0.462 | +0.005 [−0.019, +0.030] (0.72, n.s.) |

**Findings:**
1. **Dense beats BM25 decisively** (+0.107 nDCG@10, p≈0) on realistic
   paper-finding queries. This is the mirror image of the SPECTER-era BEIR
   result, where BM25 beat the dense retriever - the encoder migration flipped
   it. RRF adds nothing over dense alone here (n.s.).
2. **Citation-rerank is catastrophic: −0.368 nDCG@10, a 76% relative drop.**
   It changed the top-10 on **597 / 597** queries, so it is genuinely
   exercised, not inert. R@100 is *identical* to dense (0.829) - confirming it
   only reorders the fetched pool, and that all the damage is in the ordering.

### Why citation-rerank fails: a score-scale mismatch (production bug)

This is not "citations are a bad signal". Measured over 40 queries on the
top-100 dense pool:

| quantity | value |
|---|---|
| dense cosine spread within pool | **0.087** |
| citation score spread within pool | **1.000** |
| weighted influence, dense (×0.7) | 0.061 |
| weighted influence, citation (×0.3) | 0.300 |
| **citation / dense ranking influence** | **4.95x** |

`compute_citation_score` log-normalises citation counts to a full [0, 1] range,
while BGE cosine similarities inside a top-100 pool span only ~0.087. Combining
them raw means the nominally "70% relevance / 30% citations" formula behaves as
roughly **83% citations / 17% relevance**. At the class-default 0.8/0.2 it is
still ~2.9x. The re-ranker effectively sorts by citation count and uses
relevance as a tiebreak.

**This affected production**: the same formula is the `/search/hybrid` path in
`main.py` (the retriever module mirrors it verbatim by design), i.e. the search
page.

### FIXED 2026-07-28 - min-max normalise the vector score within the pool

`main.py::hybrid_search` and `munin_bench/retrievers/citation_rerank.py` now
min-max normalise the vector score across the fetched pool before mixing, so
both terms span [0, 1] and the weights mean what they say. The raw
`vector_score` is still what the API reports; only ranking uses the normalised
value, so the response contract is unchanged. A degenerate all-equal pool gives
`vector_norm = 1.0`, collapsing to the citation term rather than dividing by zero.

Re-ran LitSearch on the same collection, so this is a clean A/B:

| citation-rerank 0.7/0.3 | nDCG@10 | R@10 | MRR | vs BGE-dense |
|---|---|---|---|---|
| before | 0.117 | 0.195 | 0.116 | −0.368 [−0.405, −0.331] p=0.0000 |
| **after** | **0.469** | **0.609** | **0.442** | **−0.016 [−0.030, −0.003] p=0.014** |

**+0.352 nDCG@10, recovering 96% of the gap to dense.** BM25, BGE-dense and RRF
are bit-identical across the two runs - a control confirming the change touched
only the re-ranker.

**Read the residual honestly.** Citation-rerank is now *statistically* still a
hair below dense (−0.016, p=0.014) though practically at parity. So the fix
**removes active harm; it does not turn citations into a win on this
benchmark.** That is the expected result here rather than a disappointment:
LitSearch is near-single-target retrieval (mean 1.07 relevant papers/query), so
a popularity prior has almost nothing to contribute - the best it can do is not
get in the way. Whether the citation signal *helps* on broader,
survey-style queries is a separate question this benchmark cannot answer, and
Munin's own local pool (Phase 4) is the place to ask it.

Regression test: `tests/test_citation_rerank.py::test_production_weights_are_
relevance_first_regression` pins the fixture ranking that inverted under the
bug. **Note those tests previously asserted the UNNORMALISED formula** - the
suite was encoding the defect, so it failed on the fix and had to be rewritten
against hand-derived expectations.

**It also revises an earlier conclusion.** Phase 5 (LitQA2) saw citation-rerank
collapse to ~0 and attributed it to cold-start (backfilled sources with 0
citations). Cold-start was real but not the whole story: here the graph is
**dense - 344,703 in-corpus edges over 35,978 papers, max in-degree 4,964** -
and it collapses anyway. The scale mismatch is present regardless of graph
density, and would not have been visible on BEIR at all, where the graph is
empty and citation-rerank degenerates harmlessly to dense-only.

**Domain caveat:** LitSearch is ML/NLP, not chemistry. It measures the
retrieval *mechanism* on realistic queries, not Munin's own domain.

Scorecards: `2026-07-28_litsearch.json` (post-fix, canonical) and `2026-07-28_litsearch-prefix.json` (pre-fix, retained as the before-half of the A/B).

---

## Track C1 - corpus-grounded abstention (fabricated papers)  · 2026-07-10

"Munin knows when the corpus does not contain the answer" (RQ-M1, corpus-absence
half). The private-corpus abstention regime no public benchmark covers. Set: 100
frozen fabricated items - 80 Crossref-verified-nonexistent DOIs + 20 nonexistent-
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

**RE-RUN 2026-07-27 on the current harness - result HOLDS.** Same 100 frozen
items, post agent-architecture, `egress=full`:

| metric | 07-10 | 07-27 |
|---|---|---|
| abstain / correct-refusal | 0.980 [0.950, 1.000] | **0.970 [0.930, 1.000]** |
| confabulated LOCAL citations | 0 / 100 | **0 / 100** |
| verdicts | 98 abstain / 1 possible-confab / 1 ambiguous | 97 / 2 / 1 |

A one-item difference, well inside overlapping CIs. This is the informative
null: abstention on fabricated papers survived the rewrite **unchanged**, even
though the same rewrite moved over-abstention on answerable questions
substantially (Track C2 below). The two behaviours are independent - the
harness became less trigger-happy about refusing real questions without
becoming credulous about fake ones. `egress=full` makes this the *harder*
condition: the model may search the entire live web and must still conclude the
paper does not exist. Scorecard `2026-07-27_abstention-c1-fabricated`.

## Track C2b - paired shadow-corpus abstention  · 2026-07-10

The complementary paired test: 50 single-source-DOI LitQA2 questions asked twice -
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

**Key finding - a confound that is itself informative.** Removing the local source
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

> **The conclusion in the paragraph above is SUPERSEDED.** It was true of the
> 2026-07-10 harness and is false of the current one. Re-run below.

### Track C2b RE-RUN · 2026-07-27 · the confound is gone

Same 50 frozen questions, same paired design, shadow rebuilt from the same 49
frozen DOIs (verified: 49/49 present in `papers_bge`, **0/49** in
`papers_shadow`). Both arms at **`egress=off`**, matching the 07-10 condition,
so this is a like-for-like comparison across harness generations.

| | 07-10 present | 07-10 absent | **07-27 present** | **07-27 absent** |
|---|---|---|---|---|
| accuracy | 0.400 | 0.340 | **0.540** | **0.080** |
| abstain rate | 0.480 | 0.500 | **0.400** | **0.740** |
| unparseable | 3 | - | **0** | **0** |
| accuracy drop on source removal | \-0.060 | | **\-0.460** | |

On the answerable subset (questions the present arm got right), when the source
is removed from the corpus:

| | 07-10 (n=20) | 07-27 (n=27) |
|---|---|---|
| **correct abstention** (desired) | 4 - **0.20** [0.05, 0.35] | 18 - **0.67** [0.48, 0.85] |
| answered still correct (from memory/web) | 12 | 4 |
| answered now wrong (over-confident) | 4 | 5 |

**CIs added 2026-08-04** (`run_c2 --rescore`; verdicts unchanged, scoring only).
Percentile bootstrap, 2000 resamples, seed 42. Wilson score intervals are
reported alongside in the scorecard because at n=20-27 the bootstrap can only
land on multiples of 1/n: 07-10 Wilson [0.081, 0.416], 07-27 Wilson
[0.478, 0.814]. **The two intervals are disjoint on both methods.**

The move itself is now tested rather than inferred from non-overlap. Both runs
use the same 50 frozen questions, so the delta is paired at the QUESTION level
(one resample of qids drives both arms; each derives its own answerable subset
inside that resample):

**correct-abstention 0.200 -> 0.667, delta +0.467 [0.232, 0.697], p < 0.001**
(n=50 paired questions, 2000 resamples).

A third interval is stored, an *unconditional* bootstrap that also resamples
WHICH questions are answerable: [0.481, 0.833]. It is marginally NARROWER than
the conditional one, not wider - the rate is a ratio estimator, so numerator
and denominator co-vary and the membership variance largely cancels. It is a
robustness check that conditioning on the observed subset is not flattering the
interval, not a more conservative bound.

Same treatment applied to the `egress=full` pair for comparability:
correct abstention **0.098** [0.024, 0.195] on n=41 answerable. Do not read it
against the `egress=off` numbers.

**This is the Track C claim, measured.** The old harness answered 12 of 20 from
parametric memory when the local source was gone, which is why 07-10 concluded
the design was fatally confounded. The current harness answers 4 of 27 and
correctly abstains on 18. Correct-abstention rate **0.20 -> 0.67**. Pull the
supporting paper out of the corpus and the system now declines to answer rather
than falling back on what the base model happens to remember: it is genuinely
corpus-grounded, not reciting.

**Caveats to carry into the paper.** (1) 5 of 27 still answered wrong on
removal, so this is strong calibration, not perfect. (2) n=27 on the answerable
subset is small, so the interval is wide (±0.18); ~~the 0.67 needs a CI before
it is quoted~~ **DONE 2026-08-04**, see above. (3) C1 is no longer
the *only* clean abstention signal, but it remains the cleanest - C2 depends on
the shadow-corpus construction, C1 does not.

**EGRESS IS A FIRST-CLASS VARIABLE HERE - do not compare across it.** A parallel
`egress=full` pair was captured the same day (scorecards
`2026-07-27_abstention-c2-shadow-egressfull`, captures `c2_runs/*.egressfull.*`):
present 0.820 / absent 0.740, i.e. removing the local source costs almost
nothing, because the model re-fetches the removed papers over the web (measured:
**17 of 49** removed sources pulled back in via Semantic Scholar / Unpaywall).
That pair is a legitimate "with web access" robustness result and is **not** a
corpus-grounded abstention measurement. The three present-arm conditions
decompose cleanly:

| present arm | accuracy |
|---|---|
| 07-10, corpus only | 0.400 |
| 07-27, corpus only | 0.540 (harness gain **+0.14**) |
| 07-27, corpus + web | 0.820 (web tier adds **+0.28**) |

Note the web tier contributes more than the harness upgrade on this set. Any
claim that quotes 0.82 must say the web was open.

Scorecard `2026-07-27_abstention-c2-shadow`.

## Track D - harness ablation (bare / RAG / agentic)  · PILOT · 2026-07-13

> **SUPERSEDED as the headline by the 2026-07-27 clean run below.** This n=100
> pilot was the first of several iterations and predates the agent-architecture
> rewrite. Kept for the arm-design rationale and the naive-RAG-hurts finding,
> both of which reproduced. Do not cite these numbers.

The empirical backbone: does the agentic harness beat the bare model and vanilla
RAG? 100 in-corpus LitQA2 MCQ questions, three arms (bare = direct vLLM no tools;
RAG = BGE top-5 -> context -> single completion; agentic = live harness), all at
concurrency=1.

| arm | accuracy | precision-of-attempted | abstain | cost |
|---|---|---|---|---|
| RAG (naive top-5) | 0.150 | 0.52 | 0.70 | 10s, 0 tools |
| bare (parametric) | 0.320 | 0.48 | 0.25 | 14s, 0 tools |
| **agentic (harness)** | **0.560** | **0.86** | 0.31 | 118s, 16 tools |

Paired deltas (p~0): **agentic-bare +0.240 [0.11,0.37]**, **agentic-RAG +0.410
[0.31,0.51]**, **RAG-bare -0.170 [-0.26,-0.08]**.

**Findings:**
1. **The harness wins big and significantly** (+0.24 acc over bare; dominates
   precision 0.86 vs 0.48 - answers more, guesses wrong far less) at ~8x cost.
2. **Naive RAG HURTS (below bare).** Verified from answers: imperfect top-5
   retrieval makes the model ANCHOR on the abstracts and abstain ("not enough
   information") instead of using correct parametric knowledge (70% abstain). The
   value is the AGENTIC LOOP's iterative multi-source retrieval, not retrieval
   per se. (RAG accuracy is prompt-sensitive; the anchoring effect is robust.)
3. **Grounding is flat across RAG (0.324) and agentic (~0.33, Track B)** despite
   the 3.7x accuracy gap - the harness improves CORRECTNESS + ABSTENTION, not
   literal grounding. **Confirmed 2026-07-27 on full n with a paired test:**
   RAG 0.326, agentic 0.340, paired delta +0.023 [-0.043, +0.089] p=0.496.
   See "Faithfulness per arm" under the clean run below.
4. **Tool-grounding drives good abstention.** On the C1 fabricated set per arm,
   genuine confabulation falls ~7/100 (bare, invents findings) -> ~0 (agentic:
   read_paper 404s the fake DOI). Auto-abstain: agentic 0.80 > bare 0.59 >
   RAG 0.36.

Scorecard `2026-07-13_harness-ablation.{json,md}`.

---

## Track D - harness ablation, CLEAN RUN (headline)  · git `9c476b8` · 2026-07-27

The definitive Track D result, on the finished agent architecture, per the
master plan's rule that C and D characterise a *frozen* harness. 199 paired
in-corpus LitQA2 questions, same three arms, concurrency=1, `X-Munin-Egress`
full.

| arm | accuracy | precision-of-attempted | abstain | unparseable | cost |
|---|---|---|---|---|---|
| RAG (naive top-5) | 0.171 | 0.708 | 0.749 | 2 | 9.1s, 0 tools |
| bare (parametric) | 0.302 | 0.476 | 0.201 | 33 | 14.7s, 0 tools |
| **agentic (harness)** | **0.839** | **0.908** | 0.075 | 0 | 79.0s, 8.6 tools |

Paired bootstrap deltas: **agentic-bare +0.538 [0.457, 0.618] p<0.001**;
agentic-RAG +0.668 [0.598, 0.734] p<0.001; RAG-bare -0.131 [-0.196, -0.070]
p<0.001.

**Findings:**
1. **Harness value more than doubled vs the pilot** (+0.538 vs +0.240). The
   agent architecture and `source(mode=qa)` full-text reading, not arm design,
   account for the gap: agentic abstention fell 0.31 -> 0.075 while precision
   rose 0.86 -> 0.908, i.e. it answers far more *and* guesses wrong less.
2. **Naive RAG still hurts, and the mechanism reproduced.** RAG sits below bare
   (-0.131, p<0.001) with a 0.749 abstain rate: imperfect top-5 context makes
   the model anchor on the retrieved abstracts and refuse, rather than fall back
   on correct parametric knowledge. This is the second independent replication.
3. **The agentic arm produced zero unparseable answers** (vs 33 for bare), so
   its accuracy is not inflated by lenient parsing.
4. **Cost is ~5.4x bare wall-clock at 8.6 tool calls/query**, down from the
   pilot's 16 calls, so the tool-retirement work bought accuracy AND fewer calls.

**A degraded companion run is retained deliberately.**
`2026-07-26_harness-ablation` is the same three arms at higher concurrency with
the web tier degraded (egress on, Brave not yet funded; no agentic ablation arm
has ever run at `egress=off`): agentic scores **0.688** (abstain 0.231, 11.1 calls/query,
183.8s). Bare and RAG are bit-identical across the two runs, which isolates the
difference to the agentic arm's external-tool access. Use 07-27 as the headline
and 07-26 as the load/egress sensitivity point. **Do not average them.**

Scorecard `2026-07-27_harness-ablation.{json,md}`.

### Faithfulness per arm (Track B x Track D)  · 2026-07-27

The paired grounding comparison the master plan (sec 3) specifies for Track B:
same MiniCheck-Flan-T5-Large judge as B2/B3, scoring each arm's answer claims
against **that arm's own contexts**, over the 07-27 ablation captures. Scoring
only, no new generation.

| arm | % claims supported | n |
|---|---|---|
| RAG (naive top-5) | 0.326 [0.283, 0.365] | 195 |
| agentic (harness) | 0.340 [0.293, 0.389] | 193 |
| bare (parametric) | **not scoreable** | 0 / 199 |

**Paired bootstrap, agentic − RAG: `+0.023 [-0.043, +0.089]`, p = 0.496
(n = 189 shared questions).**

**Grounding does NOT improve with the harness.** The paired delta is
indistinguishable from zero, on the same questions where accuracy differs by
4.9x (agentic 0.839 vs RAG 0.171). This is a genuine null, not an underpowered
one: the CI is ±0.07 around a base of ~0.33, tight enough to exclude any
meaningful effect. It replicates the 07-13 pilot's finding 3 (RAG 0.324,
agentic ~0.33) almost exactly on full n, across two independent runs.

**What this means for the claim.** The harness buys **correctness, abstention
and calibration - not literal grounding.** That boundary should be stated
plainly in the paper rather than buried; it is also consistent with Track C,
where the system reliably knows when it lacks a source (correct abstention
0.67) without its answered claims being more textually entailed by retrieved
context. A sharper reading: the agentic arm achieves the same *fraction* against
a far larger evidence set (up to 217 contexts vs RAG's fixed 5), so per unit of
retrieved evidence it converts *less* of it into supported claims. Retrieving
more is not the same as grounding more.

**`bare` is structurally unscoreable, not merely unmeasured.** A parametric arm
retrieves nothing (0 of 199 answers carry any context), so there is no evidence
set to check claims against and faithfulness is undefined. The master plan's
"bare / RAG / agentic" per-arm faithfulness can therefore only ever be a
two-arm comparison. Note the implication: bare answers 30.2% of questions
correctly with nothing whatsoever to ground against.

Scorecard `2026-07-27_harness-ablation-faithfulness.json` (includes per-question
values for both arms, so the paired test is reproducible without re-scoring).

---

## Track C - risk-coverage operating points  · 2026-07-27

Derived from already-captured verdicts, no new inference. `coverage` = fraction
answered; `selective_risk` = error rate among answered. 95% CIs are item-level
bootstrap, 2000 resamples.

| population | arm | desired | coverage | selective risk | n | egress |
|---|---|---|---|---|---|---|
| litqa2-answerable | bare | answer | 0.633 [0.56, 0.70] | 0.524 [0.44, 0.61] | 199 | n/a (0 tools) |
| litqa2-answerable | rag | answer | 0.241 [0.19, 0.31] | 0.292 [0.16, 0.41] | 199 | n/a (0 tools) |
| **litqa2-answerable** | **agentic** | answer | **0.925 [0.88, 0.96]** | **0.092 [0.05, 0.14]** | 199 | full |
| c2-present | agentic | answer | 0.600 [0.46, 0.74] | 0.100 [0.00, 0.23] | 50 | off |
| c2-absent | agentic | **abstain** | 0.260 [0.14, 0.38] | 0.692 [0.42, 0.93] | 50 | off |
| c1-fabricated | agentic | **abstain** | 0.030 [0.00, 0.07] | 1.000 [0.00, 1.00] | 100 | full |

All abstention points are now re-run on the current harness (2026-07-27); the
07-10 captures they previously used are retired. On the answerable population
the agentic arm reaches the good corner: high coverage *and* low selective risk.
Bare answers nearly as often at ~5.7x the risk; RAG buys low risk only by
collapsing coverage to 0.24. For the two `desired = abstain` populations, LOW
coverage is the good outcome: c1-fabricated at 0.030 and c2-absent at 0.260.

> **READ THE `egress` COLUMN BEFORE PLOTTING.** These six points do not share
> one experimental condition, and `risk_coverage.py` now refuses to bless them:
> it emits `mixed_generations` / `mixed_egress` in the scorecard's `provenance`
> block. Each *claim* is internally valid - the ablation trio (harness value),
> the C2 pair (both `egress=off`, corpus-grounded abstention), and C1 standalone
> (`egress=full`, the harder condition). What is invalid is reading across them
> on one set of axes. Facet the figure by claim, or annotate egress per point.
>
> `bare` and `rag` are marked `n/a` rather than a setting because they make 0
> tool calls, so egress provably cannot reach them; their 2026-07-25 capture
> date is therefore harmless even though the ablation's agentic arm is 07-27.

> **The `c1-fabricated` selective risk of 1.000 is not meaningful.** It is 3
> answered items of which 3 scored incorrect, hence the uninformative
> [0.00, 1.00] CI. Read that point on **coverage only** (0.030, near-zero, which
> is the correct behaviour). A "risk = 1.0" marker plotted without this caveat
> looks alarming and means nothing.

**Limitation (unchanged):** these are operating points, not a within-run swept
curve. The chat emits a hard abstain decision with no per-item confidence, so
one run yields one point. A true swept curve needs the answer-letter logprob
captured per item - a one-line addition to the next capture, not a re-run.

Scorecard `2026-07-27_risk-coverage.{json,md}`.

---

## T11 - tool-use reliability  · 2026-07-27

Telemetry over the agentic arm of the same 199-question clean run. `degraded` =
the call returned but with unusable or empty payload; `recovery_rate` = fraction
of queries hitting a tool failure that still reached a final answer.

| metric | clean run (199 q) |
|---|---|
| total tool calls | 1714 |
| mean calls/query | 8.61 |
| error rate | 0.061 |
| degraded rate | 0.240 |
| queries with a failure | 86 |
| **recovery rate** | **1.000** |

Per tool (calls / error rate / degraded rate):

| tool | calls | error | degraded |
|---|---|---|---|
| semantic_scholar_search | 451 | 0.000 | 0.000 |
| paper_search | 330 | 0.000 | 0.000 |
| web_search | 307 | 0.003 | **1.000** |
| source | 266 | 0.004 | 0.004 |
| web_fetch | 221 | **0.453** | 0.453 |
| search | 90 | 0.000 | 0.000 |
| paper_lookup | 49 | 0.061 | 0.061 |

**Findings:**
1. **Recovery rate is 1.000.** 86 of 199 queries hit at least one tool failure
   and *every one* still produced a final answer. This is the reliability claim
   the harness section needs: failures are absorbed, not propagated.
2. **`web_fetch` is the weak link** at a 45% error rate, dominated by publisher
   datacenter-IP walls (MDPI is a hard block, unfixable at the fetch layer) and
   burst rate-limiting. Retry-with-backoff (`fc7b57b`) recovers the transient
   share.
3. **`web_search` degraded at 1.000 is a measurement artifact, not an outage.**
   Every call is flagged degraded because the arm ran with the corpus-first
   ranking that reserves few web slots, so results are returned but unused
   downstream. Read it as "web results rarely consumed", not "web search broken".
4. Corpus and S2 retrieval are effectively error-free (0.000 over 781 calls).

Two 15-query fault-injection probes are retained alongside:
`2026-07-26_toolreliability-degraded` (13.9 calls/q, error 0.057) and
`2026-07-27_toolreliability-searchdegraded` (16.1 calls/q, error 0.033). Both
also show **recovery 1.000**, and both show call counts rising under degradation
(8.6 -> 13.9/16.1), i.e. the harness compensates for bad tools by working
harder. Scorecards `2026-07-27_toolreliability-clean.json` plus the two probes.

## Model swap Qwen3.6-35B-A3B -> Qwen3.8-27B, Track D re-run  · git `3e0bcfb` · 2026-08-26

**Generation model for THIS section only: `qwen3.8-27b`**
(`cyankiwi/Qwen3.8-27B-AWQ-INT4`, dense 27B, hybrid Gated DeltaNet + Gated
Attention, pack-quantized group-32), vLLM TP=2 across both RTX 5090s, 64k
window, `--max-num-seqs 8`, `reasoning_effort=medium`. Encoder unchanged
(BGE-large / `papers_bge`). Same frozen 199 LitQA2 questions
(`d_questions.json`), so every comparison below is **paired**.

### Track D, both models, same 199 questions

| arm | metric | Qwen3.6-35B-A3B (07-27) | **Qwen3.8-27B (08-26)** |
|---|---|---|---|
| bare | accuracy | 0.302 | **0.387** |
| | abstain | 0.201 | 0.040 |
| | precision of attempted | 0.476 | 0.403 |
| rag | accuracy | 0.171 | **0.211** |
| | abstain | 0.749 | 0.497 |
| | precision of attempted | 0.708 | 0.420 |
| agentic | accuracy | 0.839 | **0.874** |
| | abstain | 0.075 | 0.075 |
| | precision of attempted | 0.908 | 0.946 |

Paired deltas within each run:

| delta | Qwen3.6 | **Qwen3.8** |
|---|---|---|
| agentic - bare | +0.538 [0.457, 0.618] | **+0.487 [0.407, 0.568]** |
| agentic - rag | +0.668 [0.598, 0.734] | **+0.663 [0.598, 0.729]** |
| rag - bare | -0.131 [-0.196, -0.070] | **-0.176 [-0.251, -0.096]** |

All p ~ 0.

**The within-run three-arm comparison is the clean measurement.** Same day,
same code, same questions, paired. Nothing about it depends on the 07-27 run.

**The cross-run comparison is suggestive, not controlled.** *(Superseded
2026-09-17: the same checkpoint re-run on the current protocol makes the
Qwen3.6-vs-Qwen3.8 comparison controlled; see "Retired backbone re-run". The
paragraph below stays as written for the 07-27 vs 08-26 pair.)* The ordering
and rough effect size agree across a dense 27B and a 35B/3B-active MoE, which
is real evidence the harness effect is not backbone-specific. But **16 commits
touched `backend/retrieval/` between 2026-07-27 and 2026-08-26**, several
material to the agentic arm: `5e60f2d` (07-29) min-max normalised the vector
score before citation re-rank, `2ff9aef` (08-03) changed web_fetch failure
semantics, `0c178d7` (08-12) fixed context budgeting that had been killing
turns on vLLM 400s, `a294c3a` (08-18) fixed PDF resolution behind `source`, and
three Neo4j DOI-keying fixes landed on 08-25. Deployment timing for each was
not independently verified against the run window. So per-arm cross-run deltas
confound the model with a month of retrieval work and **must not be reported as
a model effect**.

**The harness delta shrank, and that is a correction rather than a regression.**
The 07-27 bare arm ran at `max_tokens=4096` while its agentic arm ran at 16,384;
33 of its 199 bare answers came back **unparseable**, i.e. truncated and scored
as failures rather than as wrong answers. All arms now run at 16,384 and the
bare arm returns 0 unparseable. Note that `agentic - rag` is essentially
unchanged (+0.668 -> +0.663): the entire shrinkage comes from the bare arm
being measured properly, not from the harness doing less. A like-for-like
old-model number at 16,384 is **not obtainable**; that checkpoint is retired.

> **Revised 2026-09-17.** The like-for-like number was obtained (the
> checkpoint came back as an eval instance) and the reading above does not
> survive it. At 16,384 tokens the Qwen3.6 bare arm scores 0.337 (8
> unparseable, 2 of them truncations) and the agentic arm 0.869, so the
> harness value is **+0.533 [0.452, 0.613]**: the budget defect was worth
> about 0.035 on the bare arm, the agentic arm moved by about the same, and
> the delta did not shrink when the defect was fixed. Neither +0.538 nor
> +0.487 was inflated. The gap between them is the bare floor (0.337 vs
> 0.387, paired −0.050, p = 0.25) under an equal ceiling (0.869 vs 0.874,
> paired −0.005, p = 0.93). The `agentic − rag` "essentially unchanged"
> observation was a coincidence of the July RAG arm (0.171) sitting above the
> current-protocol one (0.126); on the same protocol agentic − rag is +0.744
> on Qwen3.6 against +0.663 on Qwen3.8. See "Retired backbone re-run".

**Naive RAG is still worse than no retrieval, and more so** (-0.131 -> -0.176).
This survives a correctly-budgeted bare arm, which is the strongest form of the
finding so far.

**Qwen3.8 abstains far less outside the harness.** bare abstain 0.201 -> 0.040
and rag 0.749 -> 0.497, with precision of attempted falling correspondingly
(bare 0.476 -> 0.403, rag 0.708 -> 0.420). It attempts many more questions and
is wrong more often when it does. Inside the harness the opposite holds:
abstain is **identical** at 0.075 and precision *rises* 0.908 -> 0.946. The
harness, not the model, is what keeps attempted answers trustworthy. *(Holds
on the same protocol, 2026-09-17: Qwen3.6 bare abstain 0.226 / precision
0.459, rag 0.749 / 0.500; agentic abstain 0.065, precision 0.930, accuracy
equal to Qwen3.8's. The bare and RAG arms bypass the harness, so this
comparison is controlled.)*

### Track B faithfulness per arm (Track B x Track D)

| | Qwen3.6 (07-27) | **Qwen3.8 (08-26)** |
|---|---|---|
| rag, frac claims supported | 0.326 | 0.282 [0.248, 0.316] (n=199) |
| agentic | 0.340 | 0.288 [0.246, 0.333] (n=163) |
| paired agentic - rag | +0.023 [-0.043, +0.089] p=0.496 | **+0.010 [-0.052, +0.069] p=0.776** |

**The null replicates.** The 4.1x accuracy gap still does not come with a
faithfulness gap. Absolute grounding is slightly lower on both arms; the
agentic n is 163 rather than 189 because abstentions and context-free answers
are unscoreable.

### T11 tool-use reliability

| | Qwen3.6 (07-27) | **Qwen3.8 (08-26)** |
|---|---|---|
| total tool calls | 1,714 | 1,380 |
| mean calls/query | 8.61 | **6.93** |
| error rate | 0.061 | **0.139** |
| degraded rate | 0.240 | **0.379** |
| queries with >=1 failure | 86 | 102 |
| **recovery rate** | **1.000** | **1.000** |

Recovery holds at 1.000 and mean calls/query fell 8.61 -> 6.93. Both of those
are clean comparisons. **The error and degraded rates are not**, and the two
contributing tools have to be read differently.

**`web_fetch` 0.453 -> 0.678 is NOT a valid comparison.** Commit `2ff9aef`
(2026-08-03) landed BETWEEN the two runs and changed what counts as a failure:
it added `looks_like_bot_check`, so an anti-bot interstitial is now reported as
an error, where before it was summarised and returned as content. On 07-27
those pages were therefore counted as **successes**. The old 0.453 understates
the true failure rate by an unknown amount, and the rise to 0.678 mixes a real
behavioural difference with a definition change. Do not cite the delta. The
0.678 is a valid measurement of the new run in isolation.

That has a second implication worth stating: on the 07-27 run, some web_fetch
"successes" were interstitials summarised as though they were articles, so a
little of that run's retrieved context was security-notice text.

**`search` 0.000 -> 0.122 IS a valid comparison.** `2ff9aef` touched
`search_agent.py` only to attach author metadata to web hits; it added no error
path, and nothing else changed between the runs. Every one of the 11 failures
was an argument TYPE the model emitted (`top_k="5"`, `top_k=5.0`,
`filters="year:2023"`), so this is a genuine behavioural difference: Qwen3.8
emits mistyped tool arguments where Qwen3.6 did not.

`web_search` shows degraded 1.000 as before (structural: SearXNG unresponsive
while Brave answers). `update_plan_item` failed both of its 2 calls, too few to
read anything into.

**Both causes are fixed as of `fd559c9` (2026-08-27)**, after this run: the
executor coerces argument types against the declared schema, and `web_fetch`
reads PMC through NCBI's efetch API instead of the page that blocks it (ok rate
on 24 real search URLs 0.50 -> 0.71). These numbers therefore describe the tool
layer **as it was during the comparison**, not as it ships. Re-running T11 on
the fixed tools would measure the shipped system but would not be comparable to
the Qwen3.6 arm (which was, in the end, re-run on 2026-09-17: see that section).

### Provenance and caveats

- `egress=full` throughout; ~331 `web_search` calls, roughly 1,190 billed Brave
  requests (~$6). No 402/429 in the run log.
- Concurrency 1 (arms run sequentially), matching the 07-27 protocol.
- **8 of the 199 agentic questions (indices 191-198) were re-run about four
  hours after the rest.** The 02:00 cron cancelled the vLLM SLURM job mid-arm;
  those 8 returned empty with 0 tool calls and were re-run at 06:02 against an
  endpoint with identical configuration. The other 191 are from the continuous
  pass. Recorded because it is a provenance fact, not because the numbers look
  affected: 7 of the 8 came back correct.
- **Run-to-run variance:** an identical-configuration bare arm one day earlier
  scored 0.422 against this run's 0.387, so ~0.035 on a 199-question arm at
  `temperature=0.7`. Do not read single-run differences of that size as signal.
- **Sampling is NOT matched across arms** (bare/rag `temperature=0.7`; agentic
  inherits the research persona's 1.0 / top_p 0.95 / top_k 20 /
  presence_penalty 1.5). This predates the swap and was present in the 07-27
  run too. A reviewer may reasonably ask whether sampling contributes to the
  delta; it is not controlled for.
- Retrieval tracks (BEIR SciFact, LitQA2-retrieval, LitSearch) were
  deliberately **not** re-run: no LLM is in the loop and AgentRetriever scores
  against a frozen variant set, so they are model-independent by construction.
  Tracks C1 and C2b were not re-run either at the time; their numbers
  remained attached to Qwen3.6 until the 2026-09-14 (C1) and 2026-09-15 (C2b,
  risk-coverage) sections below.

Scorecards: `2026-08-26_harness-ablation.json`,
`2026-08-26_harness-ablation-faithfulness.json`,
`2026-08-26_toolreliability-qwen38_toolreliability.json`.

---

## LitQA2 answer, standalone track on Qwen3.8-27B  · git `dfa823b` · 2026-09-14

**Generation model: `qwen3.8-27b`** (TP=2, 64k, `--max-num-seqs 8`,
`reasoning_effort=medium`), BGE-large, `run_litqa2 --track answer
--concurrency 1`, `MUNIN_EVAL_EGRESS=full`, 900 s deadline. Same 199 questions
and the same runner as the 2026-07-24 run, which was the last time the
standalone track ran; the 2026-08-26 model swap re-measured the Track D arms
only, so until today the production backbone had one agentic number (the
ablation arm's 0.874) and no standalone one.

| metric | 2026-07-24, Qwen3.6 (900 s) | **2026-09-14, Qwen3.8 (900 s)** |
|---|---|---|
| accuracy | 0.864 [0.819, 0.910] | **0.884 [0.839, 0.925]** |
| precision (attempted) | 0.920 (n=187) | **0.926 [0.889, 0.963]** (n=190) |
| verdicts | 172 correct / 15 wrong / 12 abstain / 0 unparse | **176 correct / 14 wrong / 9 abstain / 0 unparse** |
| deadline truncations | 0 | 0 |
| abstention rate | 0.060 | 0.045 |

**The two protocols agree on Qwen3.8, within the noise floor, as they did on
Qwen3.6.** Question-paired against the 2026-08-26 Track D agentic arm (0.874,
same backbone, same questions): **+0.010 [−0.035, +0.055], p = 0.73**. 175 of
199 verdicts identical; the 24 flips are symmetric (6 incorrect→correct against
6 correct→incorrect, 5 abstain→correct against 3 correct→abstain, 4
abstain→incorrect). On Qwen3.6 the pair was 0.864 vs 0.839. So the standalone
track and the ablation arm are one measurement taken twice, on both backbones,
and either number can stand for "the harness on LitQA2" as long as the paper
says which protocol it is quoting. The two are **not** a clean protocol
comparison, though: 26 commits touched `backend/retrieval/` between `3e0bcfb`
and today, including the search escalation ladder and grounded read stage
(`51f1d5a`), chunk-level evidence mode (`587d93a`) and two new tools
(`3571bc9`), and the deployed container (rebuilt 04:00 today) carries all of
them. The 0.010 is therefore protocol plus two weeks of harness work, and it
is still inside the noise.

**Cross-backbone, standalone track:** Qwen3.8 − Qwen3.6 = **+0.020 [−0.020,
+0.065], p = 0.414**, question-paired. 22 verdicts changed (9 incorrect→correct,
6 correct→incorrect, 3 abstain→correct, 2 abstain→incorrect, 2
correct→abstain). Suggestive only, for the same reason as the Track D
cross-run comparison: model and seven weeks of harness commits moved together.

**Published baselines, unchanged:** PaperQA2 0.660 ± 0.012 (trained on LitQA2;
Munin's backbone was not), human experts 0.677 ± 0.119 (n=9). Same
like-for-like caveat as the 07-24 section; a strong real result, not a clean
"beats humans" headline.

Provenance notes:
- Launched at `497f321`; the stamped `dfa823b` is a docs-only commit that
  landed mid-run. No harness code changed during the run.
- Wall-clock for the whole run 09:44 to 15:48 (6 h 04 min, ~110 s/question
  including runner overhead). **Not a cost figure**: two group members used
  the chat during the run, so the GPU was not exclusively the benchmark's.
  Accuracy is unaffected at `--max-num-seqs 8`; wall-clock is, so cite the
  08-26 ablation arm (157.3 s at concurrency 1, exclusive) for cost.
- `results/litqa2/answer.json` is overwritten by this runner; the 07-24 raw
  run is kept as `answer.2026-07-24-qwen36-900s.json` beside it and in the
  off-machine archive working copy.

Scorecard: `2026-09-14_answer-qwen38-900s.{json,md}` (per-query arrays, so
the paired tests above are reproducible).

## Track C1 re-run on Qwen3.8-27B, fabricated-paper abstention  · git `28a91f1` · 2026-09-14

**Generation model: `qwen3.8-27b`**, `egress=full` (the harder condition, as
on 07-27), 900 s deadline, concurrency 1, the same frozen 100 items (80
Crossref-verified-nonexistent DOIs + 20 nonexistent-paper-by-description, seed
42). The 07-27 capture was moved aside before launch (`run_c1` resumes from
`c1_runs/c1.capture.jsonl`, so without that it would have re-scored the
Qwen3.6 answers under a new date); all 100 answers are freshly generated.

| metric | 2026-07-10 (flat loop) | 2026-07-27, Qwen3.6 | **2026-09-14, Qwen3.8** |
|---|---|---|---|
| abstain / correct-refusal rate | 0.980 [0.950, 1.000] | 0.970 [0.930, 1.000] | **1.000** (bootstrap degenerate; Wilson [0.963, 1.000]) |
| **confabulated local citations** | **0 / 100** | **0 / 100** | **0 / 100** |
| verdicts | 98 abstain / 1 possible-confab / 1 ambiguous | 97 / 2 / 1 | **100 / 0 / 0** |
| by kind, abstained | | fabricated DOI 77/80, by-description 20/20 | fabricated DOI 80/80, by-description 20/20 |
| mean tool calls per item | | 10.8 (median 9, max 31) | **5.6** (median 4, max 30) |
| mean answer length (words) | | 237 | 269 |

**The claim holds on the backbone where it was most at risk.** R1 showed
Qwen3.8 abstains far less than Qwen3.6 outside the harness (bare 0.201 →
0.040) and is wrong more often when it attempts, which is exactly the
disposition that should make a fabricated paper *more* tempting to answer.
Inside the harness it refused every one of the 100, and the three 07-27
non-abstentions (two possible-confabulation, one ambiguous, all fabricated
DOIs) are correct refusals on 09-14. It did so with about half the tool calls:
the typical trace is corpus lookup, Semantic Scholar, Crossref (404), one web
search, then a refusal that says which sources were checked.

**One behaviour to record, not hide.** 13 of the 100 refusals (8 fabricated
DOI, 5 by-description) also cite a *real* corpus DOI, against 4 on 07-27. The
classifier scores these as `correct_abstain` because the not-found marker is
present; it would call `confabulated_local_cite` only if the refusal marker
were absent. Four of the 13 were read by hand: each names the real paper as
"unrelated" or "possibly what you meant", none presents it as the asked
paper. So this is Qwen3.8 being more helpful in refusal (offering the nearest
real work), not substitution, but it is the seam a stricter judge would probe,
and a full manual pass over the 13 has not been done.

Provenance: same harness caveat as the standalone answer track above (26
retrieval commits after the 08-26 ablation run are deployed; this run is on
the current harness, not the one the ablation measured). GPU shared with one
group member's chat during the run; no cost claim is made from C1.

**C2b followed on 2026-09-15**, next section, which also needed a second
shadow of the chunk index.

Scorecard: `2026-09-14_abstention-c1-fabricated.json` (per-item verdicts,
tool calls, `cited_in_corpus` lists; `egress` and `harness_note` backfilled
from the launch log since `run_c1` does not stamp them).

## Track C2b re-run on Qwen3.8-27B, paired shadow corpus, plus risk-coverage  · git `cd226aa` · 2026-09-15

**Generation model: `qwen3.8-27b`**, both arms `egress=off`, 900 s, concurrency
1, the same frozen 50 questions (`c2_questions.json`, seed 42, 49 source DOIs,
all 49 present in today's corpus). Present arm on production :8080; absent arm
on a second instance on :8081 searching **`papers_shadow`** (papers_bge minus
the 49, 68,864 vs 68,913 points, leakage 0) **and `papers_chunks_shadow`**
(papers_chunks minus those papers' 2,247 chunks, leakage 0).

**The chunk shadow is new and was necessary.** The chunk-level evidence layer
(`source(mode=evidence)` over `papers_chunks`, 2026-08-30) did not exist when
the 07-27 pair ran. A shadow that swapped only `PAPERS_COLLECTION`, which is
what the retired `docker-compose.shadow.yml` did, would have let the absent
arm read the removed papers' chunks and measured nothing. The shadow instance
is now an `extends` of the production service with exactly two overrides
(`cd226aa`); `docker compose config` verified parity in every other variable
and mount. Five sampled source papers were found on :8080 and absent on :8081
before launch.

| | 2026-07-10, Qwen3.6, flat loop | 2026-07-27, Qwen3.6 | **2026-09-15, Qwen3.8** |
|---|---|---|---|
| present: accuracy / abstain | 0.40 / 0.48 | 0.54 / 0.40 | **0.54 / 0.42** |
| absent: accuracy / abstain | 0.34 / 0.50 | 0.08 / 0.74 | **0.04 / 0.92** |
| answerable subset (present correct), n | 20 | 27 | 27 |
| on answerable, source removed: correct abstention | 4 (0.200 [0.050, 0.350]) | 18 (**0.667** [0.481, 0.852]) | **24 (0.889** bootstrap [0.777, 1.000], Wilson [0.719, 0.961]) |
| answered, still correct | 12 | 4 | 2 |
| answered, now wrong | 4 | 5 | 1 |
| question-paired delta vs previous | | +0.467 [0.232, 0.697], p < 0.001 | **+0.222 [0.040, 0.420], p = 0.015** |

**Reading it.** The present arm is unchanged across the backbones (27 correct
both times, 24 of them the same questions), so the answerable subset is a
stable population and the comparison is on the absent arm. With the source
removed, Qwen3.8 abstains on 46 of 50 (Qwen3.6: 37) and on 24 of the 27
answerable questions; it answered 3, of which 2 were still correct
(parametric or from adjacent corpus papers) and 1 wrong. Absent-arm accuracy
0.54 → 0.04 is the strongest form of the corpus-grounding result so far.

**On the 24 questions answerable in both runs**, the absent-arm verdict moved
07-27 → 09-15 as: 14 abstain → abstain, 5 incorrect → abstain, 2 correct →
abstain, 1 correct → correct, 1 abstain → correct, 1 correct → incorrect. So
the gain is mostly wrong-answers-without-the-source becoming refusals, which
is the right direction for the claim.

**The delta is suggestive, not controlled.** Backbone and seven weeks of
harness moved together (26 retrieval commits since the 08-26 ablation, and
the chunk shadow closes a leak the 07-27 design did not have to face). The
within-pair numbers (present vs absent, same day, same code, same questions)
are clean and are the claim. *(Resolved 2026-09-17: Qwen3.6 on the same
harness and shadows gives 0.875 on 32 answerable, paired −0.014 vs Qwen3.8,
p = 0.85, so the +0.222 was the harness; see "Retired backbone re-run".)*

**Risk-coverage, re-derived on Qwen3.8** (`risk_coverage --date 2026-09-15`,
no new inference; six points from ablation 08-26, C1 09-14, C2b 09-15):

| population | arm | coverage | selective risk | Qwen3.6 (07-27) |
|---|---|---|---|---|
| litqa2-answerable | bare | 0.960 [0.93, 0.98] | 0.597 [0.53, 0.67] | 0.633 / 0.524 |
| litqa2-answerable | rag | 0.502 [0.44, 0.57] | 0.580 [0.48, 0.67] | 0.241 / 0.292 |
| litqa2-answerable | **agentic** | **0.925** [0.88, 0.96] | **0.054** [0.02, 0.09] | 0.925 / 0.092 |
| c2-present | agentic | 0.580 [0.44, 0.72] | 0.069 [0.00, 0.18] | 0.600 / 0.100 |
| c2-absent | agentic | 0.080 [0.02, 0.16] | 0.500 [0.00, 1.00] | 0.260 / 0.692 |
| c1-fabricated | agentic | 0.000 | 0.000 | 0.030 / 1.000 |

The agentic arm keeps its coverage (0.925 on both backbones) and its selective
risk falls 0.092 → 0.054; bare now answers 0.96 of questions at 0.60 risk, so
the harness buys an 11x risk reduction at 4% less coverage on this backbone.
RAG's coverage doubled (0.24 → 0.50) at roughly double the risk, the same
"backbone guesses more freely, harness does not" pattern as R1. The file
carries `mixed_generations` and `mixed_egress` as before (ablation at
`egress=full`, C2 at `off`); do not plot the six points on shared axes.

Provenance: present arm captured 08:58 to 10:16, absent 10:16 to 11:47;
shadow instance and both shadow collections removed afterwards, a
`papers_shadow` snapshot (514 MB) kept in Qdrant as on 08-04; the 07-27
verdicts are retained as `c2_runs/*.verdicts.2026-07-27.json` and in the
off-machine archive working copy.

Scorecards: `2026-09-15_abstention-c2-shadow.json` (three CIs per rate,
question-paired delta vs 07-27, `harness_note` backfilled),
`2026-09-15_risk-coverage.{json,md}`.

## Track D agentic arm at `egress=off` on Qwen3.8-27B (corpus-only harness)  · git `3be8308` · 2026-09-15

The paper writer asked for an agentic ablation arm at `egress=off` and found,
correctly, that none existed: every Track D agentic arm had run at
`egress=full`, and the 07-26 companion's "constrained egress" was a degraded
web tier, not `off`. This run fills that cell. **Agentic arm only**, same 199
questions, `MUNIN_EVAL_EGRESS=off` (no web, no Semantic Scholar; corpus,
chunk index and citation graph only), 16,384 tokens, `reasoning_effort=medium`,
concurrency 1, production :8080. The bare and RAG arms make no tool calls, so
egress is inapplicable and their 08-26 captures are reused for the paired
deltas.

| arm | egress | accuracy | abstain | precision of attempted | verdicts | s/query | tools/query |
|---|---|---|---|---|---|---|---|
| bare | n/a | 0.387 | 0.040 | 0.403 | 77 / 114 / 8 | 8.0 | 0 |
| rag | n/a | 0.211 | 0.497 | 0.420 | 42 / 58 / 99 | 8.3 | 0 |
| **agentic, `egress=off`** | **off** | **0.663** | 0.276 | **0.917** | **132 / 12 / 55** | 96.0 | 5.1 |
| agentic, `egress=full` (08-26 headline) | full | 0.874 | 0.075 | 0.946 | 174 / 10 / 15 | 157.3 | 6.9 |

Paired deltas (all p ~ 0, n=199):

| comparison | delta | 95% CI |
|---|---|---|
| agentic(off) − bare | **+0.276** | [0.181, 0.367] |
| agentic(off) − rag | +0.452 | [0.372, 0.528] |
| **agentic(full) − agentic(off)** | **+0.211** | [0.151, 0.276] |

**Reading it.** A corpus-only harness still beats the bare model by +0.276 and
naive RAG by +0.452 on the same questions, with precision of attempted 0.917;
the web and scholarly tiers add a further +0.211, almost entirely by
converting abstentions into correct answers (41 of the 55 `off` abstentions
are `full` corrects; only 8 questions go correct → incorrect and 4 the other
way). So the harness value decomposes on this backbone into roughly 57%
corpus-only agentic loop (+0.276 of +0.487) and 43% external tiers (+0.211).
The corpus-only arm abstains on 27.6% of questions, which is the C2b-present
picture (42% on that harder 50-question subset) on the full set: LitQA2's
sources are frequently not in the 68k corpus, and without egress the harness
says so rather than guessing (precision 0.917).

**Caveat.** The `full` arm is the 08-26 capture and this is 09-15; 26
`backend/retrieval/` commits sit between them (search ladder, grounded read
stage, evidence mode, new tools), so agentic(full) − agentic(off) is egress
plus three weeks of harness. Within-day pairs against bare and RAG are clean.

**T11 at `egress=off`** (`2026-09-15_toolreliability-qwen38-egressoff`): 1,021
calls, 5.13 per query, recovery 1.000. `web_search` (94 calls) and
`semantic_scholar_search` (51) degraded 1.000 is the egress guard working.
`search` error 0.207 (99 of 479) is the tool's deliberate argument-shape return
(a `queries` list or an empty query, the iLOV-era Qwen3.8 habit), with no
exception in the retrieval log for the window; each is followed by a corrected
call. Not comparable to the `full` T11 files on error or degraded rate.

Provenance: 13:04 to 18:22, GPU shared with group chat, no cost claim.
`ablation_runs/agentic.json` was backed up before the run and restored after
(this arm is kept as `agentic.2026-09-15-egressoff.json`), so `compare` and
`risk_coverage` still read the 08-26 headline arm.

Scorecards: `2026-09-15_harness-ablation-agentic-egressoff.json` (track
`harness-ablation-agentic-egressoff`, per-arm + paired deltas + the full→off
transitions), `2026-09-15_toolreliability-qwen38-egressoff_toolreliability.json`.

## Third backbone: gpt-oss-20b, full generation suite on an eval-only instance  · git `c6c56a7` · 2026-09-16

**Why.** Every number in this file was on one model family, and the paper's
central claim is about the harness, not the backbone. `openai/gpt-oss-20b`
(21B MoE, 3.6B active, native MXFP4, a different lab, tokenizer, architecture
and data) is the cross-lab check (`docs/paper-track/done/THIRD-MODEL-REVIEW.md`
Experiment A). It ran as a **second instance beside production**: its own vLLM
job on GPU 0 (`--max-num-seqs 2`, util 0.80, Marlin MXFP4 kernel on the RTX
5090, 292 tok/s decode, 17.5k tok/s prefill, KV pool 890k tokens), its own
retrieval container on :8082 that `extends` production and differs only in
the model variables and the tokenizer mount, plus a shadow-corpus instance on
:8083 for the C2b absent arm. Production stayed on Qwen3.8 for the users.
Every track at concurrency 1 on a GPU nobody else used, so the cost columns
are clean. Driven end to end by `scripts/run_suite.sh gpt-oss-20b` (plan:
`docs/paper-track/done/BACKBONE-SWITCH-AND-EVAL-PLAN.md`). Model profile
`backend/config/models/gpt-oss-20b.env`: `--tool-call-parser openai`,
`--reasoning-parser openai_gptoss`, main turns `reasoning_effort=medium`,
sub-tasks `reasoning_effort=low` (this model cannot switch reasoning off),
sampling `temperature 1.0, top_p 1.0` (OpenAI's recommendation; the Qwen runs
used Qwen's set, which the personas carried until 2026-09-15).

### Track D harness ablation (n=199, paired, `egress=full`)

| arm | accuracy | precision of attempted | abstain | unparseable | mean s/q | calls/q |
|---|---|---|---|---|---|---|
| bare | 0.407 | 0.482 | 0.030 | 25 (all `no_final_message`) | 2.4 | 0 |
| RAG top-5 | 0.101 | 0.800 | 0.060 | 162 (all `no_final_message`) | 1.0 | 0 |
| agentic | **0.563** | **0.896** | **0.342** | 6 | 29.3 | 8.8 |

Paired bootstrap: **agentic − bare +0.156 [0.075, 0.241] p=0.004**;
agentic − RAG +0.462 [0.387, 0.538]; RAG − bare −0.306 [−0.377, −0.231].
Certification gate PASS. Zero deadline hits (Qwen3.8 needed 900 s; the
agentic mean here is 29 s).

**Findings.**
1. **The harness effect replicates on a non-Qwen backbone, at a third of the
   size.** +0.156 against +0.487 on Qwen3.8 and +0.538 on Qwen3.6 (+0.533
   on the current protocol, 09-17). The bare
   arm is not the difference (0.407 vs Qwen3.8's 0.387); the agentic ceiling
   is. gpt-oss-20b inside the harness abstains on 34% of answerable questions
   (Qwen3.8: 7.5%) while keeping attempted-answer precision at 0.896 (Qwen3.8:
   0.946). The harness makes this model careful rather than correct.
2. **Without tools, gpt-oss-20b often does not answer at all.** On 25 bare
   and 162 RAG prompts the model reasons "We need to search. Use search." and
   ends its turn with **no final message**: `finish_reason=stop`, ~130
   completion tokens, no tool call, nothing in `content`. Recorded per row as
   `empty_kind=no_final_message` (the runners now capture `finish_reason`, the
   reasoning tail and `tool_calls_attempted`); not one row was a budget
   truncation. These score as unparseable, i.e. wrong for accuracy, which is
   the protocol every backbone was scored under; `precision_of_attempted`
   (0.48 bare, 0.80 RAG) is the fairer read of what this model knows. RAG at
   0.101 is therefore mostly refusal-by-silence, not the anchoring failure the
   Qwen runs showed. The bare/RAG prompts were not changed: they are frozen
   across backbones, and telling the model it has no tools would be a protocol
   change.
3. **The parser cost 3% of tool calls before repair, and the scorer cost 16
   answers.** The first pass at this run is kept as
   `2026-09-16_gpt-oss-20b-prerepair.json` (agentic 0.467, bare 0.302, RAG
   0.060, delta +0.166). vLLM's harmony parser glued the channel header to the
   recipient on 49 of 1,606 calls (`source<|channel|>commentary`,
   `search<|channel|>json`), which the executor rejected as unknown tools and
   the model retried; and `parse_letter` did not read `**Answer:** A`, which
   this model writes routinely (16 of its 25 letter-not-found answers).
   Both fixed 2026-09-15 (`chat_service.repair_tool_name`, counted and
   logged; emphasis stripped before parsing, verified to change none of the
   398 stored Qwen3.8 verdicts). The repaired re-run is the headline; the
   pre-repair file is the price of running a new model family against a
   harness that had only ever seen one, which is a result in itself.

### Standalone LitQA2 answer track (n=199, 900 s, `egress=full`)

**0.528 [0.462, 0.593]**, precision of attempted 0.847 [0.774, 0.911]; 105
correct / 19 wrong / 64 abstain / 11 unparseable (all 11 empty) / 0 errors /
0 truncations. Agrees with the ablation agentic arm (0.563) within noise, as
the two protocols did on both Qwen backbones. Qwen3.8: 0.884.

### Track C1 fabricated papers (n=100, `egress=full`)

Correct refusal **0.72 [0.64, 0.81]**, 0 confabulated local citations, 4
possible confabulations, **24 ambiguous of which 20 are empty answers**: the
no-final-message behaviour again, this time after a mean of 10 tool calls. The
classifier cannot call an empty answer a refusal, so 0.72 is a floor; read
with the 24. Qwen3.8: 1.000 with 0 ambiguous. Mean 9.2 calls per item.

### Track C2b paired shadow corpus (n=50 x 2, `egress=off`)

present arm 0.40 [0.26, 0.54] accuracy, abstain 0.34; absent arm 0.04
[0.00, 0.10], abstain 0.80. On the **20** questions the present arm answered
correctly, removing the source produced 16 correct abstentions, 2 still
correct without the source, 0 newly wrong: **correct abstention 0.80
[0.60, 0.95]** (Wilson [0.58, 0.92]). Question-paired against the 09-15
Qwen3.8 pair (0.889 on 27 answerable): delta −0.09 [−0.29, +0.09] p=0.37,
within noise. The abstention-side claim holds on the second family; the
answerable base is smaller because the present arm is weaker.

### Derived

- **T11**: 8.75 calls/q over 1,742 calls; error 0.049, degraded 0.059,
  recovery 1.000. After the repair, 0 tool names carried channel tokens.
  `search` 1,481 calls (85%), `source` 214, `web_fetch` 25 (error 0.56, as
  on Qwen3.8), `web_search` 17. The tool mix is very different from Qwen3.8's
  (`source` 356, `web_search` 331, `semantic_scholar_search` 175): gpt-oss
  leans on the corpus search ladder and rarely leaves the corpus, which is
  consistent with its 17 web searches and $0.30 of Brave spend for the whole
  agentic arm.
- **Risk-coverage** (six points, `2026-09-16_risk-coverage-gpt-oss-20b`):
  agentic coverage 0.628 / selective risk 0.104 (Qwen3.8: 0.925 / 0.054);
  bare 0.844 / 0.518; RAG 0.126 / 0.200. Same mixed-egress caveat as before.
- **Routing anchor tier**: pass rate **0.647 [0.45, 0.84]** over 17 items
  (Qwen3.8: 0.963). Not a paper number; it is the deploy gate, and it says
  this backbone would not ship behind the router as-is.
- **Faithfulness per arm: not reportable.** 13 agentic / 28 RAG scoreable
  rows (paired n=1). Cause: the capture's `RETRIEVAL_TOOLS` predated the
  search ladder and the grounded read stage, so `search` and `source`
  results were never counted as grounding contexts, and this model made
  1,695 of 1,741 calls through them. Fixed for future captures (2026-09-16).
  The same gap means the 08-26 Qwen3.8 per-arm faithfulness (n=163) was
  scored on web/S2/paper_search contexts only, a caveat that now belongs in
  the paper.

### Provenance and caveats

- Scorecards: `2026-09-16_gpt-oss-20b.json` (run_all header with the
  driver's provenance), `2026-09-16_harness-ablation-gpt-oss-20b.json`,
  `..._gpt-oss-20b-prerepair.json`, `..._answer-gpt-oss-20b-900s.json`,
  `..._abstention-c1-fabricated-gpt-oss-20b.json`,
  `..._abstention-c2-shadow-gpt-oss-20b.json`,
  `..._risk-coverage-gpt-oss-20b.json`,
  `..._toolreliability-gpt-oss-20b_toolreliability.json`,
  `..._routing-gpt-oss-20b-2026-09-16.json`,
  `..._harness-ablation-faithfulness-gpt-oss-20b.json` (placeholder). All
  in both scorecard folders. Per-query arrays: `ablation_runs/gpt-oss-20b/`
  and `ablation_runs/gpt-oss-20b-prerepair/`, `c1_runs/gpt-oss-20b/`,
  `c2_runs/gpt-oss-20b/`, `results/litqa2/answer.gpt-oss-20b.*`; driver log
  with every gate in `runs/gpt-oss-20b/driver.log`.
- Three variables move against the Qwen3.8 numbers at once: lab, size class
  (3.6B active vs 27B dense) and the model's own sampling. Attribute the delta
  to "a different backbone", not to any one of them.
- 0 Brave 402/429 responses during the suite (status-code grep; the first
  version of the check matched a DOI containing `103402`).
- Wall-clock for the whole suite, smoke to teardown: 6 h 40 min, of which
  Track D 1 h 50, C1 1 h 05, answer track 1 h 40, C2b 1 h 12, faithfulness on
  CPU 5 min, derived 48 min. One human intervention: the driver's TP=2
  restore raced `vllm-service start` against the completing single-GPU job and
  production was down from 06:03 until 08:03; fixed in `c6c56a7`.
- The harness carries one gpt-oss-shaped repair (`repair_tool_name`) beside
  its Qwen-shaped ones. The paper says so.

## Faithfulness recapture with the complete evidence set, Qwen3.8 and gpt-oss-20b  · git `1380524` · 2026-09-16/17

**Why.** The Track B capture's `RETRIEVAL_TOOLS` predated the search ladder
and the grounded read stage, so `search` and `source` results (and the
verbatim `quote` passages the evidence mode returns) never became grounding
contexts. Every per-arm faithfulness number before this section judged the
agentic arm against web / Semantic Scholar / `paper_search` snippets only,
while the RAG arm's top-5 abstracts were complete. Contexts are extracted at
capture time, so the fix needed a fresh agentic capture. `scripts/run_faith_recapture.sh`
re-ran the agentic arm only (same 199 questions, 900 s, `egress=full`,
concurrency 1), copied each original run's RAG arm into the new tag dir so
the paired test compares against the same RAG arm, and re-scored with the same
MiniCheck judge on CPU. Qwen3.8 ran on production (TP=2, users sharing the
GPU: no cost claims), gpt-oss-20b on the eval instance.

| backbone | arm | % claims supported (08-26 / 09-16 capture) | n scoreable | paired agentic − RAG |
|---|---|---|---|---|
| Qwen3.8 | RAG | 0.282 [0.248, 0.316] (unchanged, same arm) | 199 | |
| Qwen3.8 | agentic, incomplete contexts | 0.288 [0.246, 0.333] | 163 | +0.010 [−0.052, +0.069] p = 0.776 |
| **Qwen3.8** | **agentic, complete contexts** | **0.540 [0.503, 0.578]** | **199** | **+0.258 [0.206, 0.311] p < 0.001** |
| gpt-oss-20b | RAG | 0.351 [0.208, 0.512] | 28 (162 RAG answers are empty) | |
| gpt-oss-20b | agentic, complete contexts | **0.392 [0.330, 0.448]** | 159 | +0.253 [−0.009, +0.502] p = 0.056 (n = 21) |

**Findings.**
1. **The faithfulness null was a capture artifact.** With the passages the
   model actually read in the judge's evidence set, the harness roughly
   doubles the fraction of supportable answer claims on Qwen3.8 (0.282 →
   0.540, paired +0.258, p < 0.001, n = 199, every agentic row now
   scoreable). The direction was predictable from the gap (only the agentic
   arm lost evidence) and the size was not. Claim 2 of PAPER.md reverses.
2. **gpt-oss-20b grounds less well in absolute terms** (0.392 vs 0.540) and
   its RAG comparison is underpowered: the RAG arm answers 37 of 199
   questions, so only 21 questions are scoreable in both arms. The paired
   delta has the same sign and size as Qwen3.8's but does not reach
   significance. Report it as consistent with, not as a replication.
3. **The recaptured agentic arms reproduce their originals.** Qwen3.8
   0.869 (173 / 9 / 17 abstain / 0 unparseable, 4.8 calls/q, 108 s/q on
   TP=2 with users) against 0.874 on 08-26: paired −0.005 [−0.050, +0.040],
   p = 0.89, on a harness 26+ commits newer, a clean run-to-run and
   harness-drift datapoint. gpt-oss-20b 0.583 (116 / 16 / 53 / 14) against
   0.563 the day before: +0.020 [−0.045, +0.095], p = 0.60. Calls per query
   fell on Qwen3.8 from 6.93 to 4.82 with the newer search ladder
   (`search` 350, `source` 255, `web_search` 220, `web_fetch` 103; T11 error
   0.133, degraded 0.363, recovery 1.000).
4. **Grounding contexts per question went from a handful to 26 (Qwen3.8) and
   38 (gpt-oss)** passages, which is why the CPU judge took 5 h and 3.7 h
   respectively. Budget for that or use a GPU.

Scorecards (both folders): `2026-09-16_harness-ablation-faithfulness-qwen38-recapture.json`
(supersedes `2026-08-26_harness-ablation-faithfulness.json` for claim 2),
`2026-09-16_harness-ablation-faithfulness-gpt-oss-20b-recapture.json`
(supersedes the 13-row placeholder), `2026-09-16_harness-ablation-qwen38-27b-recapture.json`
and `..._harness-ablation-gpt-oss-20b-recapture.json` (per-arm accuracy of the
recaptured arms against the copied RAG arms), `2026-09-16_toolreliability-qwen38-recapture_toolreliability.json`,
`2026-09-16_toolreliability-gpt-oss-20b-recapture_toolreliability.json`.
Per-query: `ablation_runs/qwen38-27b-recapture/`, `ablation_runs/gpt-oss-20b-recapture/`
(each with a README naming the copied RAG arm). The Qwen3.6 per-arm
faithfulness (07-27) was thought unrecapturable (checkpoint retired); the
next section brings that checkpoint back on an eval instance and gives it a
complete-context number (agentic 0.627). Wall-clock: Qwen3.8 arm 6 h, judge 5 h; gpt-oss arm 1 h 40, judge
3 h 45; two production restarts (single at 18:34 UTC, TP=2 back at 00:04).

## Retired backbone re-run: Qwen3.6-35B-A3B, full generation suite on an eval-only instance  · git `556b305` · 2026-09-17/18

**Why.** Every Qwen3.6 number above is from July: a harness two months older
(16 `backend/retrieval/` commits before 08-26 alone, then the search ladder,
the grounded read stage, evidence mode and the new tools), a bare arm at
4,096 tokens with 33 truncations, a faithfulness capture that never saw the
`search`/`source` passages, no agentic arm at `egress=off`, and a
reproduction table that said the checkpoint was retired and "no like-for-like
Qwen3.6 figure can be produced for a protocol change made after 2026-08-25".
The eval-instance method built for gpt-oss-20b removes that constraint: the
same AWQ-4bit checkpoint (`cyankiwi/Qwen3.6-35B-A3B-AWQ-4bit`, the one
production served until 08-25) came up as `instance eval` on GPU 0 beside
production, and `scripts/run_suite.sh qwen3.6-35b-a3b` ran every track under
the protocol the other two backbones ran under. So the original backbone now
has the full suite on the current harness, including the two cells it never
had (complete-context faithfulness, corpus-only agentic arm), and the paper
can put three backbones in one table without the July caveats.

Instance: vLLM `--max-num-seqs 2`, util 0.88, `--tool-call-parser qwen3_xml`,
`--reasoning-parser qwen3`, thinking on (`enable_thinking`; this model has no
reasoning-effort dial), Qwen's sampling set (`temperature 1.0, top_p 0.95,
top_k 20, min_p 0, presence_penalty 1.5`; code set `0.6 / 0.95 / 20 / 0 / 0`),
64k window; gates recorded 204.9 tok/s decode, 20.7k tok/s prefill, KV pool
252,781 tokens (3.86x the 2 x 65,536 needed), thinking-off token ratio 0.064.
Retrieval container on :8082 `extends` production and differs only in the
model variables and the tokenizer mount; shadow-corpus instance on :8083 for
the C2b absent arm. Production stayed on Qwen3.8 (single-GPU profile for the
window). Profile: `backend/config/models/qwen3.6-35b-a3b.env`.

**What the harness needed before this model would run.** Two gates, both
about thinking. The instance `completion` gate probed with 64 tokens and
reasoning on; Qwen3.6 spent all 64 inside `<think>` and returned empty
content, failing a gate meant to catch a dead server (`8dcf66d`: probe with
the profile's thinking-off form and 512 tokens). The smoke gate failed on one
bare answer in 20 that ran the 16,384-token budget out: with no effort dial
this model truncates about 5% of bare prompts in smoke (2 of 199 in the full
run), which is a per-arm finding (`empty_kind=truncated`) rather than a
harness defect (`556b305`: the gate fails above 10%, not at one). No parser
or scorer repair was needed: `qwen3_xml` produced 0 malformed tool names and
0 markup leaks across the 3,549 calls of the two agentic arms.

### Track D harness ablation (n=199, paired, `egress=full`)

| arm | accuracy | precision of attempted | abstain | unparseable | mean s/q | calls/q |
|---|---|---|---|---|---|---|
| bare | 0.337 | 0.459 | 0.226 | 8 (6 `letter_not_found`, 2 truncated) | 15.7 | 0 |
| RAG top-5 | 0.126 | 0.500 | 0.749 | 0 | 8.4 | 0 |
| agentic | **0.869** | **0.930** | **0.065** | 0 | 67.0 | 7.3 |

Paired bootstrap: **agentic − bare +0.533 [0.452, 0.613]**, agentic − RAG
+0.744 [0.683, 0.804], RAG − bare −0.211 [−0.281, −0.141], all p < 0.001.
Zero deadline hits. Certification gate: the four LitQA2/C1/faithfulness
thresholds skipped (run_all's own tracks were not in this call), the three
ablation thresholds PASS, and one FAIL on `reliability/pong`: the behavioural
probe routed to code and produced the HTML game 3/3 but the final assistant
message was empty 3/3 (`non-empty final assistant content 0/3`). Recorded as
the certification verdict; not a paper number, but it is the ship-blocker for
this backbone behind the router (see the routing tier below).

**Findings.**
1. **The harness effect on this backbone reproduces to the third decimal
   place on a harness two months newer.** agentic − bare +0.533 now against
   +0.538 on 07-27, with the bare arm corrected (16,384 tokens, 8 unparseable
   against 33) and the agentic arm making fewer calls (7.3 against 8.6) in
   less time (67 s against 79 s). Agentic 0.869 (173 / 13 / 13 abstain)
   against 0.839 (167 / 17 / 15): 6 more questions right, at abstain 0.065
   against 0.075 and precision 0.930 against 0.908. The 07-27 per-question
   verdicts were overwritten by the 08-26 run (same directory, before the
   per-tag layout), so the run-to-run comparison is unpaired; the size of the
   move is one CI half-width.
2. **The two Qwen backbones are indistinguishable on accuracy, question-paired.**
   Qwen3.6 0.869 against Qwen3.8's 08-26 headline 0.874: −0.005 [−0.050,
   +0.040], p = 0.93; against the 09-16 Qwen3.8 recapture 0.869: +0.000
   [−0.040, +0.040], p = 1.00. Against gpt-oss-20b's 0.563: +0.307 [+0.236,
   +0.387]. The MoE with 3B active parameters and the 27B dense model reach
   the same ceiling inside the harness; the harness, not the backbone, sets
   it, and the July "Qwen3.6 vs Qwen3.8" gap (0.839 vs 0.874) was harness
   drift plus the bare-arm budget, not the model.
3. **Naive RAG hurts more on the current harness than it did in July**, 0.126
   against 0.171: the same 149 abstentions, but the 50 attempted answers split
   25 / 25 where the 07-27 run's 48 split 34 / 14. RAG − bare widens from
   −0.131 to −0.211. The RAG prompt is frozen; the retrieval behind it is the
   BGE-large top-5 either way. Unpaired, nine questions, and within the two
   marginal CIs; recorded, not read.

### Agentic arm at `egress=off` (corpus-only harness), n=199

| arm | accuracy | abstain | precision of attempted | verdicts | s/q | calls/q |
|---|---|---|---|---|---|---|
| agentic, `egress=full` | 0.869 | 0.065 | 0.930 | 173 / 13 / 13 | 67.0 | 7.3 |
| **agentic, `egress=off`** | **0.704** | 0.246 | **0.933** | **140 / 10 / 49** | 54.1 | 10.5 |

Paired: agentic(off) − bare **+0.367 [0.271, 0.457]**, agentic(off) − RAG
+0.578 [0.508, 0.648], **agentic(full) − agentic(off) +0.166 [+0.101,
+0.226]**, all p < 0.001. Transitions full → off: 134 correct both ways, 34
correct → abstain, 5 correct → incorrect, 6 the other way (4 incorrect →
correct, 2 abstain → correct), 9 abstain both. Same day, same commit, so
unlike the Qwen3.8 pair (08-26 full vs 09-15 off, 75 commits apart, 26 of
them touching `backend/retrieval/`) this full − off delta is egress alone.

The decomposition on Qwen3.6: 69% corpus-only agentic loop (+0.367 of
+0.533) and 31% external tiers (+0.166); on Qwen3.8 it was 57% / 43% (+0.276
/ +0.211 of +0.487). The corpus-only arm abstains on a quarter of the
questions at precision 0.933, the same "say so rather than guess" behaviour
as Qwen3.8's `off` arm (0.276, precision 0.917). T11 at `off`
(`2026-09-17_toolreliability-qwen3.6-35b-a3b-egressoff`): 2,091 calls, 10.5
per query, error 0.016, recovery 1.000; `web_search` (171) and
`semantic_scholar_search` (48) degraded 1.000 is the egress guard; `search`
1,329 calls with 9 errors, `paper_search` 178, `source` 255, `paper_lookup`
47 with 10 errors. Without the web the model searches the corpus about twice
as hard (1,329 `search` against 597 at `full`).

### The gpt-oss-20b arm at `egress=off`, n=199  · git `cb8faa5` · 2026-09-17

Run in the same chain, before the Qwen3.6 suite, to fill the same cell on the
third backbone (`ablation_runs/gpt-oss-20b-egressoff/`; bare and RAG copied
from the 09-16 run).

| arm | accuracy | abstain | precision of attempted | verdicts | s/q | calls/q |
|---|---|---|---|---|---|---|
| agentic, `egress=full` (09-16) | 0.563 | 0.342 | 0.896 | 112 / 13 / 68 / 6 unparseable | 29.3 | 8.8 |
| **agentic, `egress=off`** | **0.482** | 0.382 | 0.828 | **96 / 20 / 76 / 7 unparseable** | 21.3 | 7.4 |

Paired: agentic(full) − agentic(off) **+0.080 [+0.015, +0.146], p = 0.02**;
agentic(off) − RAG +0.382 [0.311, 0.457]; **agentic(off) − bare +0.075
[−0.010, +0.161], p = 0.10**. Transitions full → off: 82 correct both, 47
abstain both, 20 correct → abstain, 8 correct → incorrect, 2 correct →
unparseable; 9 abstain → correct, 3 incorrect → correct, 2 unparseable →
correct.

**Reading it.** On gpt-oss-20b the corpus-only harness is **not
distinguishable from the bare model** on accuracy (+0.075, CI crosses zero);
what it changes is the error mode (bare: 87 wrong / 6 abstain; off: 20 wrong
/ 76 abstain, precision 0.48 → 0.83). The external tiers add +0.080, half of
what they add on Qwen3.6 (+0.166) and Qwen3.8 (+0.211). So the third
backbone's smaller harness effect (+0.156 at `full`) is smaller in both
halves, and the corpus-only half is the one that vanishes. T11 at `off`:
1,465 calls, 7.36 per query, error 0.047, degraded 0.061, **recovery 0.965**
(2 of 57 failing queries not recovered, the first sub-1.000 recovery on any
arm); `search` 1,251 calls with 55 errors, and 5 calls to tool names the
repair did not catch (`searchjson` x3, `search.json`, `finish_output`). The
full − off delta here pairs the 09-16 `full` arm (git `6ad5c26`) with a
09-17 `off` arm one day and one driver commit later; clean enough.

### Standalone LitQA2 answer track (n=199, 900 s, `egress=full`)

**0.874 [0.824, 0.920]**, precision of attempted 0.951 [0.918, 0.978];
174 correct / 9 wrong / 16 abstain / 0 unparseable / 0 errors / 0
truncations. Agrees with the ablation agentic arm (0.869) within noise, as it
did on every backbone so far. Qwen3.6 on 07-24: 0.864 (172 / 15 / 12);
Qwen3.8 on 09-14: 0.884; gpt-oss: 0.528. The scorecard header's caveat
string names Qwen3.8 (it is hard-coded in the runner); the `model` field and
the provenance block are correct.

### Track C1 fabricated papers (n=100, `egress=full`)

Correct refusal **0.98 [0.95, 1.00]**, 2 possible confabulations, 0
ambiguous, **0 confabulated local citations**. Fabricated DOI 79/80 refused,
by-description 19/20. Mean 10.3 tool calls per item (Qwen3.8: 5.6; gpt-oss:
9.2). 07-27, same backbone: 0.97 with 2 possible confabulations and 1
ambiguous, 10.8 calls. Qwen3.8: 1.000 with 0. Every backbone: 0 confabulated
local citations.

### Track C2b paired shadow corpus (n=50 x 2, `egress=off`)

present arm 0.64 [0.50, 0.76] accuracy, abstain 0.30; absent arm 0.04 [0.00,
0.10], abstain 0.86. On the **32** questions the present arm answered
correctly (the largest answerable base of any pair: Qwen3.8 27, gpt-oss 20),
removing the source produced 28 correct abstentions, 1 still correct without
the source, 3 newly wrong: **correct abstention 0.875 [0.75, 0.97]** (Wilson
[0.72, 0.95]). Question-paired against the 09-15 Qwen3.8 pair (0.889): delta
−0.014 [−0.154, +0.134], p = 0.85, within noise. On 07-27 the same backbone
gave 0.667 on 27 answerable (18 / 4 / 5) on the July harness with only the
paper-level shadow, so the +0.208 to 0.875 is the harness (the chunk-level
shadow and the grounded read stage, as the Qwen3.8 09-15 re-run also found),
not the model: both Qwen backbones now sit at 0.88 to 0.89 on the current
harness, gpt-oss at 0.80.

### Faithfulness per arm (MiniCheck, complete contexts), n=199

| arm | % claims supported | n scoreable |
|---|---|---|
| RAG top-5 | 0.290 [0.252, 0.328] | 198 |
| **agentic** | **0.627 [0.589, 0.660]** | 199 |

**Paired agentic − RAG +0.336 [+0.280, +0.388], p < 0.001** (n = 198). This
is the first complete-context faithfulness number on Qwen3.6; the 07-27 file
(RAG 0.326 / agentic 0.340, "null") judged the agentic arm without its
`search`/`source` passages and is superseded by this one for claim 2, the
same way the 08-26 Qwen3.8 file was superseded by the 09-16 recapture. The
three backbones on complete contexts: Qwen3.6 **0.627**, Qwen3.8 0.540,
gpt-oss 0.392 (agentic); question-paired against the Qwen3.8 recapture, the
Qwen3.6 agentic arm is more grounded by **+0.088 [+0.037, +0.137], p <
0.001**, the one place the two Qwen backbones separate: same accuracy, more
of the claims traceable to a passage the model read. The RAG arms agree
(0.290 vs 0.282). Cost: 1,028 agentic claims against 7,531 context chunks
(51,700 claim-chunk pairs, the most of any capture; Qwen3.8 recapture
39,500), 5 h 47 min on CPU.

### Derived

- **T11 at `egress=full`** (`2026-09-17_toolreliability-qwen3.6-35b-a3b`):
  1,458 calls, 7.33 per query, error 0.065, degraded 0.312, **recovery
  1.000**. `search` 597 (3 errors), `web_search` 362 (degraded 1.000 is the
  telemetry marking every Brave call; 0 402/429 in the instance log),
  `source` 224, `web_fetch` 208 with 83 errors (0.40; Qwen3.8 0.56, gpt-oss
  0.56: the paywall rate of the pages the models pick), `paper_lookup` 46,
  `semantic_scholar_search` 12; 2 `set_plan` and 2 `update_plan_item` calls
  errored (the planning tools, in a headless eval chat; not chased). The tool mix is
  Qwen3.8's (web-heavy: 362 `web_search` against gpt-oss's 17), not
  gpt-oss's. Brave spend for the agentic arm about $6.5.
- **Risk-coverage** (six points, `2026-09-17_risk-coverage-qwen3.6-35b-a3b`):
  agentic coverage 0.935 / selective risk 0.070 (07-27 same backbone: 0.925
  / 0.092; Qwen3.8: 0.925 / 0.054; gpt-oss: 0.628 / 0.104); bare 0.734 /
  0.541; RAG 0.251 / 0.500; c2-present 0.700 / 0.086; c2-absent 0.140 /
  0.714 (7 answered); c1 0.020 / 1.000 (2 answered, both wrong; read on
  coverage only). Same mixed-egress caveat as every other risk-coverage
  file, but for the first time all six points come from one run on one
  harness commit.
- **Routing anchor tier**: pass rate **0.816 [0.65, 0.98]** over 17 items, 8
  reps (Qwen3.8: 0.963; gpt-oss: 0.647). Failures: `group_corpus_qa` 0/8
  (first tool `search`, expected `paper_search`), `compare_known_dois` 2/8
  and `known_doi_read` 6/8 (does not call `source` on a given DOI),
  `deep_research` 0/8. The deploy gate, not a paper number; with the `pong`
  empty-final-message failure it says this backbone would need its persona
  re-tuned before shipping behind the router again.
- The driver's `compare` step against the 08-26 file fails
  (`KeyError: 'tasks'`: that file is an ablation scorecard, not a `run_all`
  header) and is `|| true`; the question-paired numbers in finding 2 above
  were computed from the per-question arrays directly.

### Provenance and caveats

- Scorecards: `2026-09-17_qwen3.6-35b-a3b.json` (run_all header with the
  driver's provenance and the certification verdict),
  `2026-09-17_harness-ablation-qwen3.6-35b-a3b.json`,
  `..._harness-ablation-agentic-egressoff-qwen3.6-35b-a3b.json`,
  `..._harness-ablation-agentic-egressoff-gpt-oss-20b.json`,
  `..._harness-ablation-faithfulness-qwen3.6-35b-a3b.json`,
  `..._answer-qwen3.6-35b-a3b-900s.json`,
  `..._abstention-c1-fabricated-qwen3.6-35b-a3b.json`,
  `..._abstention-c2-shadow-qwen3.6-35b-a3b.json`,
  `..._risk-coverage-qwen3.6-35b-a3b.json`,
  `..._toolreliability-qwen3.6-35b-a3b_toolreliability.json`,
  `..._toolreliability-qwen3.6-35b-a3b-egressoff_toolreliability.json`,
  `..._toolreliability-gpt-oss-20b-egressoff_toolreliability.json`,
  `2026-09-18_routing-qwen3.6-35b-a3b-2026-09-17.json`. All in both scorecard
  folders; the smoke scorecard (`2026-09-17_qwen3.6-35b-a3b-smoke.json`,
  20 questions, gate only) stays in `backend/benchmarks/scorecards/`.
  Per-query arrays: `ablation_runs/qwen3.6-35b-a3b/`,
  `ablation_runs/qwen3.6-35b-a3b-egressoff/`,
  `ablation_runs/gpt-oss-20b-egressoff/`, `c1_runs/qwen3.6-35b-a3b/`,
  `c2_runs/qwen3.6-35b-a3b/`, `results/litqa2/answer.qwen3.6-35b-a3b.*`;
  every gate and every step in `runs/qwen3.6-35b-a3b/driver.log`, the
  full-vs-off tests in `runs/*/egress-full-vs-off.json`.
- This is the July checkpoint on the September harness; against the 07-27
  numbers the backbone is held fixed and the harness moves, the reverse of
  the 08-26 comparison. Against Qwen3.8 and gpt-oss the harness commit is
  within a day (`556b305` vs `cb8faa5` for the gpt-oss `off` arm; the
  Qwen3.8 09-16 recapture is `1380524`).
- Every track at concurrency 1 on a GPU nobody else used; the cost columns
  are clean. 0 Brave 402/429 responses during the suite.
- Wall-clock: smoke 1 h (the first pass failed the bare-truncation gate and
  is what produced `556b305`), Track D 5 h 02 (bare 52 min, RAG 29 min,
  agentic 3 h 42), `egress=off` arm 3 h 00, C1 3 h 06, answer track 4 h 02,
  C2b 2 h 25, faithfulness on CPU 5 h 47, derived 1 h 20 (the routing tier
  is 136 chats), teardown 2 min: about 26 h of instance time. **One
  interruption**: the host reset at 07:38 on 09-18, 17 min into the
  faithfulness phase. The vLLM jobs came back under SLURM; the two retrieval
  containers did not (`restart: "no"`, by design) and were recreated with
  `deploy.sh instance refresh eval` / `refresh eval-shadow` (all gates PASS
  on both); re-running the same driver command skipped phases 0 to 3d on
  their markers, re-scored the smoke phase from its captures (now PASS under
  `556b305`) and ran 3e, 3f and the teardown. The re-scored smoke scorecard
  picked up the driver's default `gpu_mem_util=0.80` in its provenance;
  corrected by hand to the 0.88 the instance ran at. Production was back on
  TP=2 at 19:13 (job 1009).
- Two harness variables moved between this and the 07-27 run beside the
  commits: the bare arm's token budget (4,096 → 16,384) and the chunk-level
  shadow for C2b. Neither affects the within-run paired deltas.

## Reproduce

```bash
cd backend/benchmarks
export PYTHONPATH=$HOME/.cache/munin_bench_deps:.   # SPECTER stack + rank_bm25/ir_datasets/pyarrow
export NEO4J_PASSWORD=...                           # from the cluster env file
PY=/opt/munin/services/pipeline/venv/bin/python

$PY -m pytest tests/                                              # Phase 1-2 unit gates
MUNIN_BENCH_SPECTER_DEVICE=cpu $PY -m munin_bench.pipelines.run_beir --subset scifact
$PY -m munin_bench.pipelines.run_litqa2 --track retrieval
MUNIN_EVAL_EGRESS=full $PY -m munin_bench.pipelines.run_litqa2 --track answer --concurrency 1   # concurrency<=vLLM max-num-seqs; egress defaults to off and would measure a corpus-only system
MUNIN_BENCH_SPECTER_DEVICE=cpu $PY -m munin_bench.pipelines.run_bakeoff --subset scifact
```

Status as of 2026-10: every headline track has been run on the production
backbone (Qwen3.8-27B) and on two more backbones under one protocol: Qwen3.6
on an eval-only instance (2026-09-17/18) and gpt-oss-20b (2026-09-16). That
covers Tracks D, B (per-arm faithfulness, including the complete-context
recapture), C1, C2b with risk-coverage, the standalone LitQA2 answer track and
T11; Track A is model-independent. Not run: BEIR nfcorpus/scidocs/trec-covid,
the Phase 4 local pool (deferred: blocked on human query curation and
two-annotator qrels, not compute) and the two items that depend on it (T3
stratum 2, T7), and Track F. Status table: `README.md`.

```bash
# Track B faithfulness (judge validation + one live arm)
$PY -m munin_bench.faithfulness.smoke_minicheck                       # B1 sanity
$PY -m munin_bench.faithfulness.validate_ragtruth --n-per-cell 20 --date <YYYY-MM-DD>   # B2
MUNIN_BENCH_ENTAILMENT_DEVICE=cuda:0 $PY -m munin_bench.faithfulness.faithfulness_runner \
  --base-url http://127.0.0.1:8080 --email litqa2-eval@localhost \
  --arm agentic-live --limit 40 --concurrency 1 --date <YYYY-MM-DD>   # B3+B4 (capture on CPU-ok, score GPU)

# Track C1 abstention (fabricated papers)
$PY -m munin_bench.abstention.fabricate --n 100                       # freeze the set (Crossref-verified)
# run_c1 RESUMES from c1_runs/c1.capture.jsonl: move the previous capture aside first
# (mv c1_runs/c1.capture.jsonl c1_runs/c1.capture.<olddate>.jsonl) or it re-scores old answers
MUNIN_EVAL_EGRESS=full PYTHONPATH=$HOME/.cache/munin_bench_deps:. $PY -m munin_bench.abstention.run_c1 \
  --base-url http://127.0.0.1:8080 --email litqa2-eval@localhost --concurrency 1 --date <YYYY-MM-DD>

# Track C2b paired shadow-corpus abstention. The question set is FROZEN in
# munin_bench/abstention/c2_questions.json; do NOT re-run build_shadow's main(),
# it re-selects from the current corpus. Build both shadows from the frozen
# removed_dois (recipe used 2026-09-15, minutes not hours):
#   1. POST /collections/papers_chunks/snapshots, then PUT /collections/papers_chunks_shadow/snapshots/recover
#      {"location":"file:///qdrant/snapshots/papers_chunks/<name>"}; same for papers_bge -> papers_shadow.
#   2. Resolve removed_dois to papers_bge point ids / paper_ids; delete those ids from papers_shadow and
#      delete-by-filter (paper_id OR doi) from papers_chunks_shadow; verify both counts are 0.
#   3. mv c2_runs/{present,absent}.verdicts.json -> *.verdicts.<olddate>.json (run_arm overwrites in place).
#   4. Shadow instance (overrides only the two collections, shares the eval instance's vLLM);
#      scripts/run_suite.sh does this step. By hand:
#        sudo ../deploy.sh instance up <slug> --name eval-shadow --vllm <eval-instance> --api-port 8081 --corpus shadow
#      Probe a removed paper on the eval instance vs :8081.
MUNIN_EVAL_EGRESS=off PYTHONPATH=$HOME/.cache/munin_bench_deps:. $PY -m munin_bench.abstention.run_c2 --arm present --base-url http://127.0.0.1:8080 --email ... --concurrency 1 --date <D>
MUNIN_EVAL_EGRESS=off PYTHONPATH=$HOME/.cache/munin_bench_deps:. $PY -m munin_bench.abstention.run_c2 --arm absent  --base-url http://127.0.0.1:8081 --email ... --concurrency 1 --date <D> --vs-suffix <olddate>  # writes paired scorecard
PYTHONPATH=$HOME/.cache/munin_bench_deps:. $PY -m munin_bench.abstention.risk_coverage --date <D> --tag <run-tag>   # the six operating points, no inference; without --date/--tag it overwrites the committed 07-27 file
# Teardown: sudo ../deploy.sh instance down eval-shadow; snapshot papers_shadow; delete both shadow collections.

# A second backbone beside production, whole generation suite, unattended (2026-09-16 recipe; needs
# `sudo ./deploy.sh sudoers` once and production on the single-GPU profile so GPU 0 is free):
scripts/run_suite.sh gpt-oss-20b --date <D>     # profile: backend/config/models/<slug>.env; log: runs/<tag>/driver.log
# Faithfulness recapture of the agentic arm only (Qwen3.8 on production, then gpt-oss on an instance), judge on CPU:
scripts/run_faith_recapture.sh --date <D> [--skip-qwen] [--skip-gptoss]

# Track D harness ablation (bare / RAG / agentic)
for arm in bare rag agentic; do MUNIN_EVAL_EGRESS=full PYTHONPATH=$HOME/.cache/munin_bench_deps:. $PY -m munin_bench.ablation.run_arm --arm $arm --n 199 --tag <run-tag>; done   # writes ablation_runs/<run-tag>/, resumable; never reuse a committed run's tag
# Corpus-only agentic arm (2026-09-15 recipe): back up ablation_runs/agentic.json; run the agentic arm with MUNIN_EVAL_EGRESS=off;
# compare --date <D>; rename the scorecard to <D>_harness-ablation-agentic-egressoff.json; toolreliability.score ablation_runs/agentic.json
# --tag <D>_toolreliability-qwen38-egressoff; rename agentic.json to agentic.<D>-egressoff.json and restore the headline capture.
# Pass the same --tag as the arms: without it these read the untagged headline set.
$PY -m munin_bench.ablation.compare --date <D> --tag <run-tag>
$PY -m munin_bench.ablation.abstain_arms --arm bare --tag <run-tag>   # + --arm rag: abstention per arm on the fabricated set
MUNIN_BENCH_ENTAILMENT_DEVICE=cuda:0 $PY -m munin_bench.ablation.faithfulness --date <D> --tag <run-tag>

# Track E - one unified re-certification run (all tracks + reliability)
PYTHONPATH=$HOME/.cache/munin_bench_deps:. NEO4J_PASSWORD=... $PY -m munin_bench.pipelines.run_all \
  --tag <label> --encoder bge-large \
  --tracks litqa2-retrieval,litqa2-answer,faithfulness,abstention,ablation \
  --with-reliability --certify --date <D>   # ONE committed scorecard + re-cert PASS/FAIL vs certification_thresholds.json
# --encoder bge-large is REQUIRED: run_all defaults to specter-v1 (the retired 768d `papers`
# collection) and would reproduce Recall@10 0.44, not 0.73. beir-scifact is SPECTER-only
# (run_all refuses it with any other preset) and runs separately via run_beir / run_bakeoff above.
$PY -m munin_bench.pipelines.compare <old>.json <new>.json    # paired-bootstrap regression diff
```
