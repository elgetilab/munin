# Munin eval suite — results log

Consolidated, committed record of every eval-suite run so far. The raw
per-query artifacts live under `results/` (gitignored, regenerable); this file
is the durable summary.

> **`ablation_runs/`, `c1_runs/`, `c2_runs/` and `faithfulness_runs/` are NOT
> regenerable**, despite being gitignored alongside `results/`. They hold the
> per-query verdicts behind the headline numbers, the committed scorecards carry
> only `per_arm` and `deltas` (no per-query arrays), and the model that produced
> them is no longer deployed. `run_arm.run()` overwrites `ablation_runs/<arm>.json`
> in place, so a single re-run destroys the only copy and with it any paired
> old-vs-new comparison. The 2026-07-27 clean run is archived **off-machine** at
> `varghele@<vps>:~/backups/munin-bench-artifacts/` (5.3 MB tar.gz + sha256 +
> manifest, verified on arrival 2026-08-25), with a working copy at
> `~/munin-bench-artifacts-2026-08-25-pre-qwen38/` on hugin. Take a fresh
> off-machine copy before any re-run that writes these directories. Numbers are copied from the result JSONs, not memory.

**Provenance shared by all runs below**
- Generation model: this changed on 2026-08-26. Every section up to and
  including T11 (2026-07-27) ran on `qwen3.6-35b-a3b`
  (Qwen3.6-35B-A3B-AWQ-4bit, MoE, now retired). The final section, "Model
  swap ... Track D re-run", is on **`qwen3.8-27b`**
  (`cyankiwi/Qwen3.8-27B-AWQ-INT4`, dense), which is what production serves,
  and it re-measured Track D, per-arm faithfulness and T11 on the same 199
  questions. Track C (C1/C2b) has **not** been re-run on Qwen3.8; retrieval
  sections are model-independent by construction. `PAPER.md` says which model
  each headline claim is on.
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
> 0.211**, harness value **+0.487 [0.407, 0.568]**, recovery still 1.000; the
> shrink from +0.538 is the bare arm being measured properly at 16k tokens, not
> the harness losing value (see the final section). Full arc below.

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
(Recall@10 p=0.71); (2) citation-rerank **collapses to ~0** — a cold-start
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

## Phase 5 — LitQA2 answer (end-to-end, research profile)  · git `42b027b` · 2026-07-01

199 questions through the full agentic chat pipeline. 85 correct / 19 incorrect
/ 74 abstain / 21 unparseable.

| Metric | Munin | PaperQA2 (published) |
|---|---|---|
| Accuracy | 0.427 [0.36, 0.50] | 0.660 |
| Precision (of attempted) | **0.817** [0.74, 0.89] | 0.852 |
| Abstention rate | 0.372 | — |

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
   is the highest-confidence recall win. Untested — an encoder bake-off on
   `eval_*` collections would quantify it without a production re-embed.
3. **`title\n\nabstract` vs `[SEP]` costs ~1.5 nDCG@10** on SciFact (0.479 vs
   0.494). Production embedding could switch to the tokenizer `[SEP]` token
   (needs a corpus re-embed). See memory `project_specter_sep_finding`.
4. **The agentic fan-out did not rescue recall** here (answer acc ≈ single-pass
   recall) — but that's a hint, not a measurement. The harness's marginal value
   is Track D (bare vs vanilla-RAG vs agentic) — since built, and it confirmed
   the hint emphatically: +0.538 harness value on the finished architecture.

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

## LitQA2 answer — post agent-architecture  · git `9f0c02a` · 2026-07-24

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
(Skarlinski et al. 2024, arXiv:2409.13740v2 — accuracy = correct / all asked,
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
distinct sources a Deep Research report can cite - see `docs/paper-track/TODO.md`), but on
single-source MCQ answering the reading path, not retrieval, was the ceiling.

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

## T8 — LitSearch retrieval benchmark  · 2026-07-28

LitSearch (Ajith et al. 2024, arXiv 2407.18940): **597 real natural-language
literature-search queries** over a **64,183-paper** S2ORC corpus. The closest
public benchmark to Munin's actual usage — finding papers from a question,
rather than BEIR/SciFact's claim-verification framing. Encoder is
**BGE-large-en-v1.5, the production encoder** (the 2026-06/07 BEIR tables above
used SPECTER-v1 — do not compare across them). Binary relevance, mean 1.07
relevant papers per query. Isolated `eval_litsearch` collection.

| retriever | nDCG@10 [95% CI] | R@10 | R@100 | MRR | Δ nDCG@10 vs dense (p) |
|---|---|---|---|---|---|
| BM25 | 0.378 [0.345, 0.413] | 0.511 | 0.699 | 0.349 | −0.107 [−0.137, −0.077] (0.0000) |
| **BGE-dense (production)** | **0.485 [0.453, 0.516]** | **0.637** | 0.829 | 0.451 | — |
| citation-rerank 0.7/0.3 (**fixed**) | 0.469 [0.437, 0.503] | 0.609 | 0.829 | 0.442 | −0.016 [−0.030, −0.003] (0.014) |
| citation-rerank 0.7/0.3 (*pre-fix*) | *0.117 [0.097, 0.138]* | *0.195* | *0.829* | *0.116* | *−0.368 [−0.405, −0.331] (0.0000)* |
| RRF[BM25, BGE] | 0.490 [0.455, 0.526] | 0.628 | **0.830** | 0.462 | +0.005 [−0.019, +0.030] (0.72, n.s.) |

**Findings:**
1. **Dense beats BM25 decisively** (+0.107 nDCG@10, p≈0) on realistic
   paper-finding queries. This is the mirror image of the SPECTER-era BEIR
   result, where BM25 beat the dense retriever — the encoder migration flipped
   it. RRF adds nothing over dense alone here (n.s.).
2. **Citation-rerank is catastrophic: −0.368 nDCG@10, a 76% relative drop.**
   It changed the top-10 on **597 / 597** queries, so it is genuinely
   exercised, not inert. R@100 is *identical* to dense (0.829) — confirming it
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

### FIXED 2026-07-28 — min-max normalise the vector score within the pool

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
are bit-identical across the two runs — a control confirming the change touched
only the re-ranker.

**Read the residual honestly.** Citation-rerank is now *statistically* still a
hair below dense (−0.016, p=0.014) though practically at parity. So the fix
**removes active harm; it does not turn citations into a win on this
benchmark.** That is the expected result here rather than a disappointment:
LitSearch is near-single-target retrieval (mean 1.07 relevant papers/query), so
a popularity prior has almost nothing to contribute — the best it can do is not
get in the way. Whether the citation signal *helps* on broader,
survey-style queries is a separate question this benchmark cannot answer, and
Munin's own local pool (Phase 4) is the place to ask it.

Regression test: `tests/test_citation_rerank.py::test_production_weights_are_
relevance_first_regression` pins the fixture ranking that inverted under the
bug. **Note those tests previously asserted the UNNORMALISED formula** — the
suite was encoding the defect, so it failed on the fix and had to be rewritten
against hand-derived expectations.

**It also revises an earlier conclusion.** Phase 5 (LitQA2) saw citation-rerank
collapse to ~0 and attributed it to cold-start (backfilled sources with 0
citations). Cold-start was real but not the whole story: here the graph is
**dense — 344,703 in-corpus edges over 35,978 papers, max in-degree 4,964** —
and it collapses anyway. The scale mismatch is present regardless of graph
density, and would not have been visible on BEIR at all, where the graph is
empty and citation-rerank degenerates harmlessly to dense-only.

**Domain caveat:** LitSearch is ML/NLP, not chemistry. It measures the
retrieval *mechanism* on realistic queries, not Munin's own domain.

Scorecards: `2026-07-28_litsearch.json` (post-fix, canonical) and `2026-07-28_litsearch-prefix.json` (pre-fix, retained as the before-half of the A/B).

---

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

**RE-RUN 2026-07-27 on the current harness — result HOLDS.** Same 100 frozen
items, post agent-architecture, `egress=full`:

| metric | 07-10 | 07-27 |
|---|---|---|
| abstain / correct-refusal | 0.980 [0.950, 1.000] | **0.970 [0.930, 1.000]** |
| confabulated LOCAL citations | 0 / 100 | **0 / 100** |
| verdicts | 98 abstain / 1 possible-confab / 1 ambiguous | 97 / 2 / 1 |

A one-item difference, well inside overlapping CIs. This is the informative
null: abstention on fabricated papers survived the rewrite **unchanged**, even
though the same rewrite moved over-abstention on answerable questions
substantially (Track C2 below). The two behaviours are independent — the
harness became less trigger-happy about refusing real questions without
becoming credulous about fake ones. `egress=full` makes this the *harder*
condition: the model may search the entire live web and must still conclude the
paper does not exist. Scorecard `2026-07-27_abstention-c1-fabricated`.

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
| unparseable | 3 | — | **0** | **0** |
| accuracy drop on source removal | \-0.060 | | **\-0.460** | |

On the answerable subset (questions the present arm got right), when the source
is removed from the corpus:

| | 07-10 (n=20) | 07-27 (n=27) |
|---|---|---|
| **correct abstention** (desired) | 4 — **0.20** [0.05, 0.35] | 18 — **0.67** [0.48, 0.85] |
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
the conditional one, not wider — the rate is a ratio estimator, so numerator
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
the *only* clean abstention signal, but it remains the cleanest — C2 depends on
the shadow-corpus construction, C1 does not.

**EGRESS IS A FIRST-CLASS VARIABLE HERE — do not compare across it.** A parallel
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

## Track D — harness ablation (bare / RAG / agentic)  · PILOT · 2026-07-13

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

## Track D — harness ablation, CLEAN RUN (headline)  · git `9c476b8` · 2026-07-27

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
`2026-07-26_harness-ablation` is the same three arms with constrained egress and
higher concurrency: agentic scores **0.688** (abstain 0.231, 11.1 calls/query,
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
and calibration — not literal grounding.** That boundary should be stated
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

## Track C — risk-coverage operating points  · 2026-07-27

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
> block. Each *claim* is internally valid — the ablation trio (harness value),
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
captured per item — a one-line addition to the next capture, not a re-run.

Scorecard `2026-07-27_risk-coverage.{json,md}`.

---

## T11 — tool-use reliability  · 2026-07-27

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
| | abstain | 0.749 | 0.498 |
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

**The cross-run comparison is suggestive, not controlled.** The ordering and
rough effect size agree across a dense 27B and a 35B/3B-active MoE, which is
real evidence the harness effect is not backbone-specific. But **16 commits
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

**Naive RAG is still worse than no retrieval, and more so** (-0.131 -> -0.176).
This survives a correctly-budgeted bare arm, which is the strongest form of the
finding so far.

**Qwen3.8 abstains far less outside the harness.** bare abstain 0.201 -> 0.040
and rag 0.749 -> 0.498, with precision of attempted falling correspondingly
(bare 0.476 -> 0.403, rag 0.708 -> 0.420). It attempts many more questions and
is wrong more often when it does. Inside the harness the opposite holds:
abstain is **identical** at 0.075 and precision *rises* 0.908 -> 0.946. The
harness, not the model, is what keeps attempted answers trustworthy.

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
the Qwen3.6 arm, which cannot be re-run at all.

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
  Tracks C1 and C2b were not re-run either; their numbers remain attached to
  Qwen3.6.

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

## Reproduce

```bash
cd backend/benchmarks
export PYTHONPATH=$HOME/.cache/munin_bench_deps:.   # SPECTER stack + rank_bm25/ir_datasets/pyarrow
export NEO4J_PASSWORD=...                           # from the cluster .env
PY=/opt/munin/services/pipeline/venv/bin/python

$PY -m pytest tests/                                              # Phase 1-2 unit gates
MUNIN_BENCH_SPECTER_DEVICE=cpu $PY -m munin_bench.pipelines.run_beir --subset scifact
$PY -m munin_bench.pipelines.run_litqa2 --track retrieval
MUNIN_EVAL_EGRESS=full $PY -m munin_bench.pipelines.run_litqa2 --track answer --concurrency 1   # concurrency<=vLLM max-num-seqs; egress defaults to off and would measure a corpus-only system
MUNIN_BENCH_SPECTER_DEVICE=cpu $PY -m munin_bench.pipelines.run_bakeoff --subset scifact
```

Not yet run (as of 2026-07-27): BEIR nfcorpus/scidocs/trec-covid; **Phase 4
local pool** (deferred, blocked on human query curation + two-annotator qrels,
not compute) and the two P0 items that depend on it (T3 stratum 2, T7); Track F
throughout. Track B: judge validated, and the **per-arm paired faithfulness is now DONE**
(2026-07-27, RAG vs agentic, delta n.s.) — see the ablation section. **Track C
re-run DONE 2026-07-27**: C1 held (0.970 abstain, 0 confabulated local cites),
C2 correct-abstention 0.20 -> 0.67 at matched `egress=off`. **Model swap
2026-08-26**: Tracks D, B-per-arm and T11 re-run on Qwen3.8-27B and current;
standalone LitQA2 answer track re-run on Qwen3.8 2026-09-14 (0.884, agrees
with the ablation arm within noise);
Track A is model-independent and current; **Track C is still on the retired
Qwen3.6** and needs the `papers_shadow` collection rebuilt plus the :8081
instance to re-run C2b. Status table: `README.md`.

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

# Track D harness ablation (bare / RAG / agentic)
for arm in bare rag agentic; do PYTHONPATH=$HOME/.cache/munin_bench_deps:. $PY -m munin_bench.ablation.run_arm --arm $arm --n 100; done
$PY -m munin_bench.ablation.compare --date <D>
$PY -m munin_bench.ablation.abstain_arms --arm bare   # + --arm rag: abstention per arm on the fabricated set
MUNIN_BENCH_ENTAILMENT_DEVICE=cuda:0 $PY -m munin_bench.ablation.faithfulness --date <D>

# Track E — one unified re-certification run (all tracks + reliability)
PYTHONPATH=$HOME/.cache/munin_bench_deps:. NEO4J_PASSWORD=... $PY -m munin_bench.pipelines.run_all \
  --tag <label> --encoder bge-large \
  --tracks litqa2-retrieval,litqa2-answer,faithfulness,abstention,ablation \
  --with-reliability --certify --date <D>   # ONE committed scorecard + re-cert PASS/FAIL vs certification_thresholds.json
# --encoder bge-large is REQUIRED: run_all defaults to specter-v1 (the retired 768d `papers`
# collection) and would reproduce Recall@10 0.44, not 0.73. beir-scifact is SPECTER-only
# (run_all refuses it with any other preset) and runs separately via run_beir / run_bakeoff above.
$PY -m munin_bench.pipelines.compare <old>.json <new>.json    # paired-bootstrap regression diff
```
