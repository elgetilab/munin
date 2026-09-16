# 05 Results

Every result, stripped of narrative, with the encoder stated per table and the
scorecard filename attached. Narrative interpretation is in `07-FINDINGS.md`;
caveats that must travel with each number are in `08-LIMITATIONS.md`.

**Read the encoder column.** SPECTER-v1 and BGE-large results are never
comparable and were never meant to be pooled.

**Read the backbone column too.** The generation model changed on 2026-08-26
from Qwen3.6-35B-A3B (MoE, retired) to Qwen3.8-27B (dense, what production
serves). R1, R2 and R6 were re-measured on Qwen3.8 over the same 199 questions
and carry both columns; the Qwen3.8 column is the headline. R3 (abstention)
was re-run on Qwen3.8 in two steps (C1 2026-09-14, C2b and risk-coverage
2026-09-15) and holds on both counts. R4 (retrieval) is backbone-independent
at scoring time, against frozen Qwen3.6-era query variants (see R4). Within a
run, comparisons are paired and clean;
**across the two backbones they are suggestive, not controlled**, because 16
commits touched `backend/retrieval/` between the runs (see `08-LIMITATIONS.md`).

**A third backbone from a different lab, gpt-oss-20b, was measured on
2026-09-16** (R1, R3, R5, R6, R7) as an eval-only instance beside production:
same 199 questions, same arms, same budgets and deadline, concurrency 1 on a
GPU nothing else used. It is reported in its own rows and tables, never
pooled. Three variables move against Qwen3.8 at once (lab, size class, the
model's own recommended sampling); attribute its deltas to "a different
backbone". Bare and RAG on this model are dominated by a behaviour the Qwen
runs did not show, a turn that ends with **no final message**; the rows say
so. R2 has no gpt-oss number (see R2).

All CIs are 95% percentile bootstrap, 1000 resamples, seed 42. All p-values are
two-sided paired bootstrap unless stated.

---

## R1. Headline: harness ablation (Track D)

199 paired in-corpus LitQA2 questions, three arms, concurrency 1,
`egress=full`, BGE-large encoder, finished agent architecture, all arms at
`max_tokens=16,384`. **Headline run: Qwen3.8-27B, git `3e0bcfb`, 2026-08-26.**
Second backbone: Qwen3.6-35B-A3B, git `9c476b8`, 2026-07-27, same questions.

| Arm | Backbone | Accuracy | Precision of attempted | Abstain | Unparseable | Wall-clock | Tool calls |
|---|---|---|---|---|---|---|---|
| RAG (naive top-5) | **Qwen3.8** | **0.211** | 0.420 | 0.498 | 0 | 8.3 s | 0 |
| Bare (parametric) | **Qwen3.8** | **0.387** | 0.403 | 0.040 | 0 | 8.0 s | 0 |
| **Agentic (harness)** | **Qwen3.8** | **0.874** | **0.946** | 0.075 | 0 | 157.3 s | 6.9 |
| RAG (naive top-5) | Qwen3.6 | 0.171 | 0.708 | 0.749 | 2 | 9.1 s | 0 |
| Bare (parametric) | Qwen3.6 | 0.302 | 0.476 | 0.201 | 33 | 14.7 s | 0 |
| Agentic (harness) | Qwen3.6 | 0.839 | 0.908 | 0.075 | 0 | 79.0 s | 8.6 |

Verdict counts, Qwen3.8: agentic 174 correct / 10 incorrect / 15 abstain; bare
77 / 114 / 8; RAG 42 / 58 / 99. Qwen3.6: agentic 167 / 17 / 15; bare 60 / 66 /
40 (+33 unparseable); RAG 34 / 14 / 149 (+2).

Paired bootstrap deltas, within each run:

| Comparison | Qwen3.8 (headline) | 95% CI | Qwen3.6 | 95% CI | p (both) |
|---|---|---|---|---|---|
| **agentic − bare** | **+0.487** | [0.407, 0.568] | +0.538 | [0.457, 0.618] | **< 0.001** |
| agentic − RAG | +0.663 | [0.598, 0.729] | +0.668 | [0.598, 0.734] | < 0.001 |
| RAG − bare | **−0.176** | [−0.251, −0.096] | −0.131 | [−0.196, −0.070] | < 0.001 |

**Third backbone, gpt-oss-20b (`openai/gpt-oss-20b`, 21B MoE, 3.6B active,
native MXFP4), 2026-09-16, git `c6c56a7`.** Same 199 questions and arms,
`max_tokens=16,384`, 900 s, `egress=full`, concurrency 1; served on its own
GPU (Marlin MXFP4 kernel, `--max-num-seqs 2`, `reasoning_effort=medium`,
sampling `temperature 1.0, top_p 1.0` per OpenAI's recommendation).

| Arm | Backbone | Accuracy | Precision of attempted | Abstain | Unparseable | Wall-clock | Tool calls |
|---|---|---|---|---|---|---|---|
| RAG (naive top-5) | gpt-oss-20b | 0.101 | 0.800 | 0.060 | 162 (all no final message) | 1.0 s | 0 |
| Bare (parametric) | gpt-oss-20b | 0.407 | 0.482 | 0.030 | 25 (all no final message) | 2.4 s | 0 |
| **Agentic (harness)** | gpt-oss-20b | **0.563** | **0.896** | **0.342** | 6 | 29.3 s | 8.8 |

Verdict counts: agentic 112 correct / 13 incorrect / 68 abstain / 6
unparseable; bare 81 / 87 / 6 / 25; RAG 20 / 5 / 12 / 162.

| Comparison | gpt-oss-20b | 95% CI | p |
|---|---|---|---|
| **agentic − bare** | **+0.156** | [0.075, 0.241] | **0.004** |
| agentic − RAG | +0.462 | [0.387, 0.538] | < 0.001 |
| RAG − bare | −0.306 | [−0.377, −0.231] | < 0.001 |

The arm ordering and the direction replicate outside the Qwen family; the
size of the harness effect is a third of Qwen3.8's. The bare arm is not the
difference (0.407 vs 0.387); the agentic arm abstains on a third of answerable
questions (0.342 vs 0.075) while keeping precision of attempted at 0.896. The
25 bare and 162 RAG "unparseable" rows are a distinct failure: the model
reasons "we need to search" and ends its turn with no final message
(`finish_reason=stop`, ~130 tokens, no tool call, zero budget truncations),
recorded per row as `empty_kind=no_final_message`. The protocol scores it as
wrong, for every backbone alike; precision of attempted (0.48 bare, 0.80 RAG)
is the fairer read of what the model knows. Certification gate PASS. A first
pass at this run, before a tool-name repair and a scorer fix (see R6 and
`08-LIMITATIONS.md`), scored agentic 0.467 and is kept as
`2026-09-16_gpt-oss-20b-prerepair.json`. Scorecards:
`2026-09-16_gpt-oss-20b.json` (run header with serving provenance),
`2026-09-16_harness-ablation-gpt-oss-20b.json` (per arm, with
`unparseable_reasons` and `empty_kinds`).

**Why the harness delta moved from +0.538 to +0.487.** The Qwen3.6 bare arm ran
at `max_tokens=4096` against its agentic arm's 16,384, and 33 of its 199
answers were truncated and scored as failures. All arms now run at 16,384 and
bare returns 0 unparseable. `agentic − RAG` is essentially unchanged
(+0.668 → +0.663), so the whole shrink is the bare arm being measured
properly. The Qwen3.6 figure was inflated by a defect; +0.487 is the
correction. A like-for-like Qwen3.6 number at 16,384 cannot be obtained, that
checkpoint is retired.

**Wall-clock is not comparable across backbones** (dense 27B vs 3B-active MoE
on a different serving profile). Within the Qwen3.8 run, the harness costs
~20x bare at 6.9 tool calls per query.

**Corpus-only agentic arm (`egress=off`), Qwen3.8, 2026-09-15.** The one
agentic ablation arm ever run at `egress=off`; not a headline, a decomposition
of it. Same 199 questions, production harness with the web and Semantic
Scholar tiers blocked, bare and RAG reused from 08-26 (they make no tool
calls).

| Arm | Egress | Accuracy | Precision of attempted | Abstain | Wall-clock | Tool calls |
|---|---|---|---|---|---|---|
| Agentic (harness) | **off** | **0.663** | 0.917 | 0.276 | 96.0 s | 5.1 |
| Agentic (harness) | full (08-26) | 0.874 | 0.946 | 0.075 | 157.3 s | 6.9 |

Paired, n=199, all p ≈ 0: agentic(off) − bare **+0.276** [0.181, 0.367];
agentic(off) − RAG +0.452 [0.372, 0.528]; agentic(full) − agentic(off)
**+0.211** [0.151, 0.276]. The external tiers convert abstentions into correct
answers (41 of the 55 `off` abstentions are `full` corrects; 8 questions go
correct → incorrect, 4 the reverse), so on this backbone the harness value
splits roughly 57 / 43 between the corpus-only loop and the external tiers.
The `full` arm is three weeks older than the `off` arm (26 retrieval commits),
so that last delta is egress plus harness drift; the two within-day pairs are
clean. Scorecards: `2026-09-15_harness-ablation-agentic-egressoff.json`,
`2026-09-15_toolreliability-qwen38-egressoff_toolreliability.json` (T11 at
`off`: 5.13 calls/query, recovery 1.000; `web_search` and
`semantic_scholar_search` degraded 1.000 is the egress guard; `search` error
0.207 is the tool's argument-shape return, not a failure).

Scorecards: `2026-08-26_harness-ablation.json` (headline),
`2026-07-27_harness-ablation.{json,md}` (Qwen3.6).

**Companion run, retained deliberately:** `2026-07-26_harness-ablation` is the
same three arms at concurrency above `--max-num-seqs` with the web tier
degraded (egress was **on**, but Brave was not yet funded, so `web_search`
returned nothing useful). It is not an `egress=off` arm; the only one of those
is the 2026-09-15 corpus-only arm above. The 07-26 agentic arm scores **0.688** (abstain 0.231, 11.1 calls/query,
183.8 s). Bare and RAG are bit-identical across the two runs, which isolates
the difference to the agentic arm's external-tool access and load. Use 07-26 only as the load/egress
sensitivity point beside the Qwen3.6 clean run. **Do not average them.**

---

## R2. Faithfulness per arm (Track B x Track D)

Same MiniCheck-Flan-T5-Large judge as the validation run, scoring each arm's
answer claims against **that arm's own contexts**, over the R1 ablation
captures. Scoring only, no new generation. BGE-large.

| Arm | Backbone | % claims supported | 95% CI | n |
|---|---|---|---|---|
| RAG (naive top-5) | **Qwen3.8** | 0.282 | [0.248, 0.316] | 199 |
| Agentic (harness) | **Qwen3.8** | 0.288 | [0.246, 0.333] | 163 |
| RAG (naive top-5) | Qwen3.6 | 0.326 | [0.283, 0.365] | 195 |
| Agentic (harness) | Qwen3.6 | 0.340 | [0.293, 0.389] | 193 |
| Bare (parametric) | both | **not scoreable** | | 0 / 199 |
| RAG / Agentic | gpt-oss-20b | **not reported** | | 28 / 13 |

**Paired bootstrap, agentic − RAG, Qwen3.8: +0.010 [−0.052, +0.069],
p = 0.776** (n = 163 shared questions). Qwen3.6: +0.023 [−0.043, +0.089],
p = 0.496 (n = 189). **The null replicates across backbones.** Absolute
grounding is slightly lower on Qwen3.8 for both arms; the agentic n is lower
because abstentions and context-free answers cannot be scored.

`bare` is **structurally** unscoreable, not merely unmeasured: a parametric arm
retrieves nothing, so there is no evidence set to check claims against and
faithfulness is undefined. Note the implication: the bare arm answers 38.7% of
questions correctly (Qwen3.8) with nothing whatsoever to ground against.

**Capture caveat (found 2026-09-16; travels with every number in this
section).** The capture's list of retrieval tools predated the search ladder
and the grounded read stage, so `search` and `source` results were never
counted as grounding contexts. The Qwen3.8 agentic n=163 and the Qwen3.6
agentic n=193 were therefore scored against web, Semantic Scholar and
`paper_search` evidence only, not against the full-text passages the harness
actually read on those turns; both nulls stand on that evidence set, and the
missing evidence can only have understated the agentic arm (the RAG arm's
top-5 abstracts were fully captured). gpt-oss-20b made 97% of its calls through
`search` and `source`, leaving 13 scoreable agentic rows, so no faithfulness
number is reported for it. Fixed for future captures; neither existing run
can be re-scored because contexts are extracted at capture time.

Scorecards: `2026-08-26_harness-ablation-faithfulness.json` (headline),
`2026-07-27_harness-ablation-faithfulness.json` (Qwen3.6). Both include
per-question values for both arms, so the paired test is reproducible without
re-scoring. `2026-09-16_harness-ablation-faithfulness-gpt-oss-20b.json` is a
placeholder and must not be quoted.

### Faithfulness judge validation (Track B2)

MiniCheck-Flan-T5-Large against RAGTruth, 120 responses.

| Task | AUROC | BA@0.5 |
|---|---|---|
| **QA** (Munin's task) | **0.950** | 0.725 |
| Summary | 0.708 | 0.650 |
| Data-to-text | 0.723 | 0.525 |

Gate (QA-AUROC >= 0.70) passed. Scorecard:
`2026-07-08_faithfulness-judge-ragtruth`.

### Single-arm faithfulness (Track B3/B4), interim

Live agentic arm, 40 LitQA2 questions, BGE-large.

| Metric | Value | 95% CI |
|---|---|---|
| **% claims supported** (macro, length-robust) | **0.356** | [0.272, 0.425] |
| Mean faithfulness (per-claim support) | 0.404 | [0.347, 0.450] |
| Per-answer grounding | 14.6 real claims, 82.5 contexts / answer | |

Claim extraction refined 2026-07-09 (dropping process narration, questions,
headers) moved the number 0.378 to 0.356 with heavily overlapping CIs.
Scorecards: `2026-07-09_faithfulness-agentic-live` (baseline), `-t1a`, `-cap`.

---

## R3. Abstention (Track C)

> **All three Track C results are on Qwen3.8 as the headline** (C1
> 2026-09-14; C2b and risk-coverage 2026-09-15), with the Qwen3.6 figures
> kept beside them. R1 shows Qwen3.8 abstains far less than Qwen3.6
> **outside** the harness (bare 0.201 → 0.040, RAG 0.749 → 0.498) and
> identically inside it (0.075); Track C is where that disposition would
> show up as confabulation or as answering without the source, and on both
> counts the harness result held or improved. The C2b re-run needed a second
> shadow, of the chunk index, because the evidence layer that reads it did
> not exist when the 07-27 pair ran; see the C2b block. The third backbone,
> gpt-oss-20b (2026-09-16), is added to each table in its own column.

### C1: fabricated papers

100 frozen items (80 Crossref-verified-nonexistent DOIs + 20
nonexistent-paper-by-description), zero collisions with the 67,675-DOI corpus.

| Metric | 2026-07-10, Qwen3.6, flat loop | 2026-07-27, Qwen3.6, agent architecture | **2026-09-14, Qwen3.8** | 2026-09-16, gpt-oss-20b |
|---|---|---|---|---|
| Abstain / correct-refusal rate | 0.980 [0.950, 1.000] | 0.970 [0.930, 1.000] | **1.000** (Wilson [0.963, 1.000]; bootstrap degenerate) | 0.720 [0.640, 0.810] (a floor, see below) |
| **Confabulated local citations** | **0 / 100** | **0 / 100** | **0 / 100** | **0 / 100** |
| Verdicts | 98 abstain / 1 possible-confab / 1 ambiguous | 97 / 2 / 1 | **100 / 0 / 0** | 72 / 4 / 24 |
| Abstained, by kind | | fabricated DOI 77/80, by-description 20/20 | 80/80, 20/20 | |
| Mean tool calls per item | | 10.8 (median 9) | **5.6** (median 4) | 9.2 |
| Refusals that also cite a real corpus DOI | | 4 | 13 | 1 |

The 2026-07-27 and 2026-09-14 runs were at `egress=full`, which is the
**harder** condition: the model may search the entire live web and must still
conclude the paper does not exist. The three 07-27 non-abstentions are correct
refusals on 09-14. The 13 refusals citing a real corpus DOI score as
`correct_abstain` because the not-found marker is present; four were read by
hand and each names the real paper as unrelated or as the likely intended
target rather than substituting it. A full manual pass over the 13 has not
been done (`08-LIMITATIONS.md`). Scorecards: `2026-07-10_abstention-c1-fabricated`,
`2026-07-27_abstention-c1-fabricated`, `2026-09-14_abstention-c1-fabricated`
(the last carries `harness_note`: 26 retrieval commits newer than the 08-26
ablation harness), `2026-09-16_abstention-c1-fabricated-gpt-oss-20b`.

On gpt-oss-20b, 20 of the 24 "ambiguous" items are **empty answers**: the
model ended its turn after a mean of 10 tool calls with no final message (the
same behaviour as its bare and RAG arms in R1). The classifier cannot call an
empty answer a refusal, so 0.720 is a floor on correct refusal, not an
estimate of it; the 4 possible confabulations are the ceiling on the failure
side. The number that holds on all three backbones without qualification is
**zero confabulated local citations**.

### C2b: paired shadow corpus

50 single-source-DOI LitQA2 questions asked twice: against the live corpus, and
against an isolated retrieval instance on `papers_shadow` (= `papers_bge` minus
the 49 source papers). Shadow verified each time: 49/49 present in
`papers_bge`, **0/49** in `papers_shadow`. **Both arms at `egress=off`.**
The 2026-09-15 run additionally shadowed the chunk index
(`papers_chunks_shadow` = `papers_chunks` minus those papers' 2,247 chunks,
leakage 0): the chunk-level evidence layer (2026-08-30) post-dates the 07-27
design, and without it the absent arm could have read the removed papers'
chunks. The shadow instance is an `extends` of the production service with
only the two collection variables overridden, verified with `docker compose
config`.

| | 07-10 present | 07-10 absent | 07-27 present | 07-27 absent | **09-15 present** | **09-15 absent** | 09-16 present | 09-16 absent |
|---|---|---|---|---|---|---|---|---|
| Backbone | Qwen3.6 | Qwen3.6 | Qwen3.6 | Qwen3.6 | **Qwen3.8** | **Qwen3.8** | gpt-oss-20b | gpt-oss-20b |
| Accuracy | 0.400 | 0.340 | 0.540 | 0.080 | **0.540** | **0.040** | 0.400 | 0.040 |
| Abstain rate | 0.480 | 0.500 | 0.400 | 0.740 | **0.420** | **0.920** | 0.340 | 0.800 |
| Unparseable | 3 | n/a | 0 | 0 | **0** | **0** | see note | see note |
| Accuracy drop on source removal | −0.060 | | −0.460 | | **−0.500** | | −0.360 | |

On the answerable subset (questions the present arm answered correctly), when
the source is removed:

| | 07-10 (n=20) | 07-27 (n=27) | **09-15 (n=27)** | 09-16 gpt-oss-20b (n=20) |
|---|---|---|---|---|
| **Correct abstention** (desired) | 4 (**0.200**) | 18 (**0.667**) | **24 (0.889)** | 16 (0.800) |
| Answered, still correct (from memory or adjacent corpus papers) | 12 (0.600) | 4 (0.148) | 2 (0.074) | 2 (0.100) |
| Answered, now wrong (over-confident) | 4 (0.200) | 5 (0.185) | 1 (0.037) | **0** |

The present arm is stable across the backbones (27 correct both times, 24 the
same questions), so the answerable population is the same and the comparison
is on the absent arm. On the 24 questions answerable in both runs, the
absent-arm verdict moved 07-27 → 09-15 as 14 abstain → abstain, 5 incorrect →
abstain, 2 correct → abstain, 1 correct → correct, 1 abstain → correct, 1
correct → incorrect: the gain is wrong-answers-without-the-source becoming
refusals.

**Confidence intervals on correct abstention** (added 2026-08-04 by re-scoring
the existing captures; verdicts unchanged). Percentile bootstrap, 2000
resamples, seed 42, with the Wilson score interval alongside because at
n = 20-27 the bootstrap can only land on multiples of 1/n:

| Run | Backbone | n | Rate | Bootstrap | Wilson |
|---|---|---|---|---|---|
| 2026-07-10 | Qwen3.6, flat loop | 20 | 0.200 | [0.050, 0.350] | [0.081, 0.416] |
| 2026-07-27 | Qwen3.6 | 27 | 0.667 | [0.481, 0.852] | [0.478, 0.814] |
| **2026-09-15** | **Qwen3.8** | **27** | **0.889** | **[0.777, 1.000]** | **[0.719, 0.961]** |
| 2026-09-16 | gpt-oss-20b | 20 | 0.800 | [0.600, 0.950] | [0.584, 0.919] |

**The 07-10 and 07-27 intervals are disjoint on both methods.** The 09-15
interval overlaps the 07-27 one; the question-paired test below is what
settles that comparison, not the overlap.

**The move is a tested delta, not an inference from non-overlap.** Both runs
use the same 50 frozen questions, so the comparison is paired at the question
level: one resample of question ids drives both arms, and each arm derives its
own answerable subset inside that resample.

**correct abstention 0.200 → 0.667: delta +0.467 [0.232, 0.697], p < 0.001**
(n = 50 paired questions, 2000 resamples, seed 42). That is the harness
rewrite on one backbone, and it is the controlled comparison.

**correct abstention 0.667 → 0.889 (Qwen3.6 → Qwen3.8): delta +0.222 [0.040,
0.420], p = 0.015**, same pairing. This one is **suggestive, not
controlled**: backbone and seven weeks of harness commits moved together,
and the chunk shadow closes a leak the 07-27 design did not face. The
within-pair numbers on 09-15 (present vs absent, same day, same code, same
questions) are clean and are the claim.

**Third backbone, gpt-oss-20b (2026-09-16), same 50 frozen questions, both
arms at `egress=off`, both shadows rebuilt from the frozen DOIs (leakage 0):**
present 0.400 [0.26, 0.54] / absent 0.040 [0.00, 0.10]; abstain 0.34 → 0.80.
The present arm answered 20 correctly (Qwen3.8: 27), so the answerable base
is smaller. Correct abstention **0.800** (bootstrap [0.600, 0.950], Wilson
[0.584, 0.919]), 2 still correct, **0 newly wrong**. Question-paired against
the 09-15 Qwen3.8 pair: **−0.089 [−0.292, +0.092], p = 0.37**, within noise.
The corpus-grounded abstention behaviour therefore holds on a backbone from a
different lab, at a smaller answerable base. Scorecard:
`2026-09-16_abstention-c2-shadow-gpt-oss-20b.json`.

A third interval is stored in each scorecard: an **unconditional** bootstrap
that also resamples *which* questions are answerable, giving [0.481, 0.833] on
07-27 and [0.750, 1.000] on 09-15. It
is marginally **narrower** than the conditional interval, not wider, because
the rate is a ratio estimator whose numerator and denominator co-vary, so the
membership variance largely cancels. Its role is a robustness check that
conditioning on the observed subset is not flattering the interval, not a more
conservative bound. Quote the conditional bootstrap [0.481, 0.852] for
consistency with every other CI in the suite.

The other two cells of the answerable subset (they are a **multinomial** over
the same 27 items, so these marginals are not independent and cannot move
separately): on 09-15 answered-still-correct 0.074 [0.000, 0.185],
answered-now-wrong 0.037 [0.000, 0.111]; on 07-27 0.148 [0.037, 0.296] and
0.185 [0.037, 0.333].

The remaining rates on the full n = 50 pair, for completeness. 09-15: present
accuracy 0.540 [0.400, 0.680], present abstain 0.420 [0.280, 0.560], absent
accuracy 0.040 [0.000, 0.100], absent abstain 0.920 [0.840, 0.980]. 07-27:
0.540 [0.400, 0.680], 0.400 [0.260, 0.540], 0.080 [0.020, 0.160], 0.740
[0.620, 0.840].

Scorecards: `2026-09-15_abstention-c2-shadow` (headline; carries the
question-paired delta vs 07-27 and a `harness_note`),
`2026-07-27_abstention-c2-shadow` (Qwen3.6).

### C2b at `egress=full` (a different experiment, not comparable)

Present 0.820 / absent 0.740. Removing the local source costs almost nothing
because the model re-fetches removed papers over the web: **17 of 49** removed
sources were pulled back in through Semantic Scholar / Unpaywall. This is a
robustness result, **not** an abstention measurement.

Correct abstention on this pair's answerable subset is **0.098 [0.024, 0.195]**
(n = 41), against 0.667 at `egress=off`. Reported for comparability of method
only. **Do not read the two against each other**: they are different
experiments, and the gap is the web tier, not a calibration change.

Decomposition of the three present-arm conditions:

| Present arm | Accuracy |
|---|---|
| 07-10, corpus only | 0.400 |
| 07-27, corpus only | 0.540 (harness gain **+0.14**) |
| 07-27, corpus + web | 0.820 (web tier adds **+0.28**) |

**The web tier contributes more than the harness upgrade on this set.** Any
claim quoting 0.82 must say the web was open. Scorecards:
`2026-07-27_abstention-c2-shadow-egressfull`.

### Risk-coverage operating points

Derived from already-captured verdicts, no new inference. `coverage` = fraction
answered; `selective risk` = error rate among answered. 95% CIs are item-level
bootstrap, 2000 resamples.

Headline, **Qwen3.8** (ablation 08-26, C1 09-14, C2b 09-15):

| Population | Arm | Desired | Coverage | Selective risk | n | Egress |
|---|---|---|---|---|---|---|
| litqa2-answerable | bare | answer | 0.960 [0.93, 0.98] | 0.597 [0.53, 0.67] | 199 | n/a (0 tools) |
| litqa2-answerable | rag | answer | 0.502 [0.44, 0.57] | 0.580 [0.48, 0.67] | 199 | n/a (0 tools) |
| **litqa2-answerable** | **agentic** | answer | **0.925 [0.88, 0.96]** | **0.054 [0.02, 0.09]** | 199 | full |
| c2-present | agentic | answer | 0.580 [0.44, 0.72] | 0.069 [0.00, 0.18] | 50 | off |
| c2-absent | agentic | **abstain** | 0.080 [0.02, 0.16] | 0.500 [0.00, 1.00] | 50 | off |
| c1-fabricated | agentic | **abstain** | 0.000 [0.00, 0.00] | 0.000 (no items answered) | 100 | full |

Second backbone, Qwen3.6 (all 07-27):

| Population | Arm | Desired | Coverage | Selective risk | n | Egress |
|---|---|---|---|---|---|---|
| litqa2-answerable | bare | answer | 0.633 [0.56, 0.70] | 0.524 [0.44, 0.61] | 199 | n/a (0 tools) |
| litqa2-answerable | rag | answer | 0.241 [0.19, 0.31] | 0.292 [0.16, 0.41] | 199 | n/a (0 tools) |
| litqa2-answerable | agentic | answer | 0.925 [0.88, 0.96] | 0.092 [0.05, 0.14] | 199 | full |
| c2-present | agentic | answer | 0.600 [0.46, 0.74] | 0.100 [0.00, 0.23] | 50 | off |
| c2-absent | agentic | **abstain** | 0.260 [0.14, 0.38] | 0.692 [0.42, 0.93] | 50 | off |
| c1-fabricated | agentic | **abstain** | 0.030 [0.00, 0.07] | 1.000 [0.00, 1.00] | 100 | full |

Third backbone, gpt-oss-20b (all 2026-09-16):

| Population | Arm | Desired | Coverage | Selective risk | n | Egress |
|---|---|---|---|---|---|---|
| litqa2-answerable | bare | answer | 0.844 [0.79, 0.89] | 0.518 [0.44, 0.60] | 199 | n/a (0 tools) |
| litqa2-answerable | rag | answer | 0.126 [0.08, 0.17] | 0.200 [0.05, 0.38] | 199 | n/a (0 tools) |
| litqa2-answerable | agentic | answer | 0.628 [0.56, 0.69] | 0.104 [0.05, 0.16] | 199 | full |
| c2-present | agentic | answer | 0.500 [0.36, 0.64] | 0.200 [0.05, 0.36] | 50 | off |
| c2-absent | agentic | **abstain** | 0.060 [0.00, 0.14] | 0.333 [0.00, 1.00] | 50 | off |
| c1-fabricated | agentic | **abstain** | 0.280 [0.19, 0.37] | 1.000 [1.00, 1.00] | 100 | full |

On gpt-oss-20b the harness cuts selective risk ~5x against bare (0.518 →
0.104) by answering a third less often (0.844 → 0.628), where on the Qwen
backbones it kept coverage. Bare and RAG coverage here count the
no-final-message rows as not answered (R1); the `c1-fabricated` coverage of
0.280 is 28 items answered, of which 20 are those empty turns and 4 possible
confabulations, so read it on coverage only as before.

Across the swap the agentic arm keeps its coverage (0.925 on both) and its
selective risk falls 0.092 → 0.054, while bare's coverage rises to 0.960 at
0.597 risk (the harness buys an ~11x risk reduction for 4% less coverage) and
RAG's coverage doubles at roughly double the risk. Both `desired = abstain`
populations moved toward zero coverage.

Two mandatory reading instructions:

1. **These six points do not share one experimental condition.** Facet any
   figure by claim, or annotate egress per point. The scorer emits
   `mixed_generations` / `mixed_egress` into the provenance block for exactly
   this reason.
2. **The `c1-fabricated` selective risk is not meaningful on either
   backbone.** On Qwen3.6 it is 3 answered items of which 3 scored incorrect
   (1.000 with a [0.00, 1.00] CI); on Qwen3.8 no item was answered, so the
   risk is undefined and the scorer reports 0. Read that point on **coverage
   only** (0.030 → 0.000). The same applies to `c2-absent` on Qwen3.8: 4
   answered items, risk 0.500 [0.00, 1.00].

For the two `desired = abstain` populations, **low coverage is the good
outcome**. Scorecards: `2026-09-15_risk-coverage.{json,md}` (headline),
`2026-07-27_risk-coverage.{json,md}` (Qwen3.6),
`2026-09-16_risk-coverage-gpt-oss-20b.{json,md}` (third backbone).

---

## R4. Retrieval (Track A)

Backbone-independent at scoring time; scored against frozen Qwen3.6-era query
variants generated once by the production expander. Not re-run for the model
swap because regenerating the variants would change the benchmark, not the
system.

### R4.1 BEIR / SciFact, external validity anchor

300 queries, 5,183 docs. **SPECTER-v1 era.** Git `eb1cf73`, 2026-06-29.

| Retriever | nDCG@10 | 95% CI | Note |
|---|---|---|---|
| BM25 | **0.652** | [0.61, 0.70] | Reproduces published BEIR BM25 (~0.665); validates the harness |
| SPECTER-dense | 0.479 | [0.43, 0.53] | Dated citation-embedder, weak on retrieval |
| Citation re-rank (0.7/0.3) | 0.479 | | Degenerates to dense on BEIR (no graph) |
| RRF[BM25, SPECTER] | 0.621 | [0.58, 0.67] | Best Recall@100 (0.936) |

Significance: SPECTER < BM25 (Δ −0.174, p ≈ 0); RRF > SPECTER (Δ +0.143,
p ≈ 0); RRF vs BM25 not significant (Δ −0.031, p = 0.10).

### R4.2 LitSearch, realistic paper-finding queries

597 queries, 64,183-paper corpus, isolated `eval_litsearch` collection.
**BGE-large, the production encoder.** 2026-07-28.

| Retriever | nDCG@10 | 95% CI | R@10 | R@100 | MRR | Δ nDCG@10 vs dense (p) |
|---|---|---|---|---|---|---|
| BM25 | 0.378 | [0.345, 0.413] | 0.511 | 0.699 | 0.349 | −0.107 [−0.137, −0.077] (0.0000) |
| **BGE-dense (production)** | **0.485** | [0.453, 0.516] | **0.637** | 0.829 | 0.451 | n/a |
| Citation re-rank 0.7/0.3 (**fixed**) | 0.469 | [0.437, 0.503] | 0.609 | 0.829 | 0.442 | −0.016 [−0.030, −0.003] (0.014) |
| Citation re-rank 0.7/0.3 (*pre-fix*) | *0.117* | *[0.097, 0.138]* | *0.195* | *0.829* | *0.116* | *−0.368 [−0.405, −0.331] (0.0000)* |
| RRF[BM25, BGE] | 0.490 | [0.455, 0.526] | 0.628 | **0.830** | 0.462 | +0.005 [−0.019, +0.030] (0.72, n.s.) |

Scorecards: `2026-07-28_litsearch.json` (post-fix, canonical),
`2026-07-28_litsearch-prefix.json` (pre-fix, the before-half of the A/B).

### R4.3 LitQA2 retrieval, SPECTER-v1 baseline

199 in-corpus questions, retrieve depth 20. Git `360367d`, 2026-07-01.

| Metric | AgentRetriever (production) | SPECTER-dense | Citation re-rank |
|---|---|---|---|
| Recall@1 | 0.191 | 0.234 | 0.000 |
| Recall@5 | 0.369 | 0.379 | 0.000 |
| **Recall@10** | **0.442** [0.37, 0.51] | 0.452 [0.38, 0.53] | 0.000 |
| MRR | 0.277 [0.23, 0.33] | 0.308 [0.25, 0.37] | 0.001 |

Multi-query fan-out does **not** beat single-query dense (Recall@10 p = 0.71).

### R4.4 Encoder migration, full-corpus validation

BGE-large re-embed of all 68k papers (`papers_bge`, 1024d), LitQA2 retrieval
over the full corpus, paired bootstrap against the committed SPECTER baseline
on the same 199 questions. Git `bc35b4d`, 2026-07-03.

| System | Metric | SPECTER-v1 | BGE-large | Δ (p) |
|---|---|---|---|---|
| AgentRetriever (production) | Recall@1 | 0.191 | 0.487 | +0.30 (~0) |
| | **Recall@10** | **0.437** | **0.729** | **+0.29 (~0)** |
| | MRR | 0.276 | 0.573 | +0.30 (~0) |
| SPECTER-dense | Recall@10 | 0.447 | 0.691 | +0.24 (~0) |

Scorecards: `2026-07-03_baseline-specter-v1.json`, `2026-07-03_bge-large.json`.

---

## R5. End-to-end answering (LitQA2, 199 questions)

The full arc on one axis. Each row states its encoder, backbone and harness
generation.

| Date | Encoder | Backbone | Harness | Accuracy | 95% CI | Precision (attempted) | Withheld |
|---|---|---|---|---|---|---|---|
| 2026-07-01 | SPECTER-v1 | Qwen3.6 | flat tool loop | 0.427 | [0.36, 0.50] | 0.817 [0.74, 0.89] | 74 abstain / 21 unparseable |
| 2026-07-06 | SPECTER-v1 (rollback) | Qwen3.6 | flat tool loop, fixed parser, 300 s | 0.422 | | 0.832 | 97 abstain / 17 wrong |
| 2026-07-06 | **BGE-large** | Qwen3.6 | flat tool loop, fixed parser, 300 s | **0.497** | [0.432, 0.563] | 0.853 (n=116) | 83/199 (42%) |
| 2026-07-24 | BGE-large | Qwen3.6 | **agent architecture**, 900 s | **0.864** | [0.819, 0.910] | **0.920** (n=187) | 12/199 (6%) |
| 2026-07-27 | BGE-large | Qwen3.6 | agent architecture (Track D agentic arm) | 0.839 | | 0.908 | abstain 0.075, 0 unparseable |
| 2026-08-26 | BGE-large | **Qwen3.8** | agent architecture (Track D agentic arm) | **0.874** | | **0.946** | abstain 0.075, 0 unparseable |
| 2026-09-14 | BGE-large | **Qwen3.8** | agent architecture, standalone answer track, 900 s, `egress=full` | **0.884** | [0.839, 0.925] | **0.926** [0.889, 0.963] (n=190) | 9/199 (4.5%), 0 unparseable, 0 truncated |
| 2026-09-16 | BGE-large | gpt-oss-20b | agent architecture (Track D agentic arm) | 0.563 | | 0.896 | abstain 0.342, 6 unparseable |
| 2026-09-16 | BGE-large | gpt-oss-20b | agent architecture, standalone answer track, 900 s, `egress=full` | 0.528 | [0.462, 0.593] | 0.847 [0.774, 0.911] (n=124) | 64 abstain (32%), 11 unparseable (all empty), 0 truncated |

The 2026-07-24 and 2026-09-14 rows are the standalone `litqa2-answer` track
(`run_litqa2 --track answer`); the 07-27 and 08-26 rows are the agentic arm of
the R1 ablation. Same 199 questions, same research profile, same 900 s
deadline, concurrency 1, and full egress in all four (the 07-24 run predates
the egress guard; the later three set `egress=full` explicitly); the ablation
arm additionally records per-arm cost.

**The two protocols agree on Qwen3.8 within the ±0.035 noise floor, as they
did on Qwen3.6.** Question-paired, standalone track − ablation arm on Qwen3.8:
**+0.010 [−0.035, +0.055], p = 0.73**, 175 of 199 verdicts identical, the 24
flips symmetric. On Qwen3.6 the pair was 0.864 vs 0.839. Either number can
stand for the harness on LitQA2 provided the protocol is named. Note that 26
harness commits landed between the ablation run (`3e0bcfb`) and the standalone
run (search ladder, grounded read stage, evidence mode, two new tools), so
the 0.010 is protocol plus harness drift and is still inside the noise.
Cross-backbone on the standalone track, Qwen3.8 − Qwen3.6: +0.020 [−0.020,
+0.065], p = 0.414, suggestive for the usual reason. Scorecard:
`2026-09-14_answer-qwen38-900s.json`. On the retired
backbone the two protocols gave 0.864 and 0.839, a gap inside the measured
run-to-run noise (`08-LIMITATIONS.md`). On gpt-oss-20b the two protocols give
0.528 and 0.563, the same agreement on a third backbone; the 11 unparseable
standalone answers are the no-final-message behaviour of R1. Scorecard:
`2026-09-16_answer-gpt-oss-20b-900s.json`.

**Encoder step (paired, same 199 questions, both arms re-run with a fixed
parser):**

| Metric | SPECTER-v1 | BGE-large | Δ (paired) | p |
|---|---|---|---|---|
| Accuracy | 0.422 | 0.497 | +0.075 [0.015, 0.136] | **0.028** |
| Precision (attempted) | 0.832 | 0.853 | +0.022 | n/a |
| Verdicts | 84 correct / 97 abstain / 17 wrong | 99 correct / 80 abstain / 17 wrong | +15 correct, −17 abstain | |

Scorecards: `2026-07-06_answer-specter-v1-v2.json`,
`2026-07-06_answer-bge-large-v2.json`.

**Architecture step (2026-07-06 baseline to 2026-07-24):** accuracy
0.497 → **0.864** (+0.367), precision 0.853 → 0.920, withheld 42% → 6%. The
CIs are disjoint (the baseline tops out at 0.563). Scorecard:
`2026-07-24_answer-full-900s.json`.

### Published baselines for context (quoted, not re-measured)

Verified 2026-07-25 against the primary source (Skarlinski et al. 2024,
arXiv:2409.13740v2). Accuracy = correct / all asked; precision = correct /
answered, identical to the definitions used here.

| System | Accuracy | Precision |
|---|---|---|
| **Munin (2026-07-24)** | **0.864 [0.819, 0.910]** | **0.920** |
| PaperQA2 | 0.660 ± 0.012 (n=3) | 0.852 ± 0.011 |
| Human experts | 0.677 ± 0.119 (n=9) | 0.738 ± 0.096 |

**This comparison is not like-for-like and must carry its caveat.** PaperQA2
was trained on LitQA2 and Munin's off-the-shelf Qwen was not, so the accuracy
comparison flatters Munin. The human number carries a very large SD (±0.119) on
n=9. See `08-LIMITATIONS.md` §2.

---

## R6. Tool-use reliability (T11)

Telemetry over the agentic arm of the R1 runs. `degraded` = the call returned
but with an unusable or empty payload; `recovery_rate` = fraction of queries
hitting a tool failure that still reached a final answer.

| Metric | **Qwen3.8 (08-26)** | Qwen3.6 (07-27) | Comparable? | gpt-oss-20b (09-16) |
|---|---|---|---|---|
| Total tool calls | 1,380 | 1,714 | yes | 1,742 |
| Mean calls / query | **6.93** | 8.61 | yes | 8.75 |
| Error rate | 0.139 | 0.061 | **no** (see below) | 0.049 |
| Degraded rate | 0.379 | 0.240 | **no** (see below) | 0.059 |
| Queries with at least one failure | 102 | 86 | no | 58 |
| **Recovery rate** | **1.000** | **1.000** | yes | **1.000** |

Per tool, gpt-oss-20b (09-16): `search` 1,481 calls (85%, error 0.038),
`source` 214 (0.061), `web_fetch` 25 (**0.560**, same failure definition as
08-26), `web_search` 17 (degraded 1.000, the SearXNG-unresponsive marker as
before), `paper_lookup` 3. The mix is unlike either Qwen run: this backbone
stays inside the corpus search ladder and left it 17 times in 199 questions
(Qwen3.8: 331 web searches, 175 Semantic Scholar). Before a tool-name repair
landed, 49 of its 1,606 first-pass calls (3%) arrived with a harmony channel
token glued to the recipient (`source<|channel|>commentary`) and were
rejected as unknown tools; that is a serving-stack (parser) property, fixed at
the parser boundary and counted, and the pre-repair run is kept
(`2026-09-16_gpt-oss-20b-prerepair.json`). Scorecard:
`2026-09-16_toolreliability-gpt-oss-20b_toolreliability.json`.

Per tool, Qwen3.8 (08-26):

| Tool | Calls | Error rate | Degraded rate |
|---|---|---|---|
| source | 356 | 0.003 | 0.003 |
| web_search | 331 | 0.000 | **1.000** |
| web_fetch | 261 | **0.678** | 0.678 |
| semantic_scholar_search | 175 | 0.000 | 0.000 |
| paper_search | 157 | 0.000 | 0.000 |
| search | 90 | **0.122** | 0.122 |
| paper_lookup | 8 | 0.125 | 0.125 |
| update_plan_item | 2 | 1.000 | 1.000 |

Per tool, Qwen3.6 (07-27):

| Tool | Calls | Error rate | Degraded rate |
|---|---|---|---|
| semantic_scholar_search | 451 | 0.000 | 0.000 |
| paper_search | 330 | 0.000 | 0.000 |
| web_search | 307 | 0.003 | **1.000** |
| source | 266 | 0.004 | 0.004 |
| web_fetch | 221 | **0.453** | 0.453 |
| search | 90 | 0.000 | 0.000 |
| paper_lookup | 49 | 0.061 | 0.061 |

**Recovery 1.000 replicates and calls per query fell 8.61 → 6.93.** Those are
clean comparisons. **The aggregate error and degraded rates are not**, and the
two tools that drive them have to be read separately:

- **`web_fetch` 0.453 → 0.678 is not a valid comparison.** Commit `2ff9aef`
  (2026-08-03) landed between the runs and changed what counts as a failure:
  an anti-bot interstitial is now an error, where before it was summarised and
  returned as content. On 07-27 those pages were counted as successes, so
  0.453 understates the true failure rate by an unknown amount. 0.678 is a
  valid measurement of the 08-26 run in isolation; the delta is not.
- **`search` 0.000 → 0.122 is a valid comparison** and a genuine behavioural
  difference: nothing in the tool's error path changed between the runs, and
  every one of the 11 failures was an argument *type* the model emitted
  (`top_k="5"`, `top_k=5.0`, `filters="year:2023"`). Qwen3.8 emits mistyped
  tool arguments where Qwen3.6 did not.

Both causes were fixed after the run (`fd559c9`, 2026-08-27: schema-driven
argument coercion in the executor; `web_fetch` reads PMC via NCBI efetch).
These figures therefore describe the tool layer **during the comparison**, not
as shipped. `update_plan_item` failed both of its 2 calls, too few to read.

Two 15-query fault-injection probes (Qwen3.6 era) are retained alongside:
`2026-07-26_toolreliability-degraded` (13.9 calls/query, error 0.057) and
`2026-07-27_toolreliability-searchdegraded` (16.1 calls/query, error 0.033).
**Both also show recovery 1.000**, and both show call counts rising under
degradation (8.6 → 13.9 / 16.1), that is, the harness compensates for bad tools
by working harder.

Scorecards: `2026-08-26_toolreliability-qwen38_toolreliability.json`
(headline), `2026-07-27_toolreliability-clean.json` (Qwen3.6).

**`web_search` degraded at 1.000 is a measurement artifact, not an outage.**
Every call is flagged degraded because the arm ran with corpus-first ranking
that reserves few web slots, so results are returned but unused downstream.
Read it as "web results rarely consumed", not "web search broken".

---

## R7. Routing

Routing anchor eval, used as a written deploy gate for harness changes during
the 2026-06/07 migration and harness iteration. **The metric is a
tool-trajectory pass rate, not a routing accuracy**: an anchor passes a rep
when its gated tool checks hold; the router's profile pick is recorded only as
a non-gating diagnostic.

| Run | Anchors x reps | Mean pass rate | 95% CI (rough, normal over item means) | `profile_match` (diagnostic) |
|---|---|---|---|---|
| 2026-07-10 `postcap-t3` (after the tool-call cap) | 16 x 5 | **0.963** (77/80) | 0.911-1.000 | 7/10 labelled anchors, 0.70 |
| 2026-07-25 `toolretire-after` (after tool consolidation, most recent on Qwen) | 17 x 5 | 0.835 | | 8/11 |
| 2026-09-16 `routing-gpt-oss-20b` (third backbone, eval instance) | 17 x 8 | 0.647 | 0.452-0.842 | |

The gpt-oss-20b row is the deploy gate read against a backbone that never
was production: at 0.647 it says that backbone would not ship behind the
router as-is, and nothing more. The `>= 0.950` gate was a procedure written
in two plan documents and read off the scorecard by a person; no code, CI or deploy-script check enforces
it. The KNN example set changed again on 2026-08-27 with no anchor run since,
so 0.963 describes the 2026-07-10 deploy, not the current tree. Scorecards
under the `2026-06-2x_routing-*` and `2026-06-2x_a5-*` series (24 files
covering the A0 through A5 migration) plus the anchor-tier files above;
provenance, anchor-set size (18 items, 17 runnable, 11 profile-labelled),
disjointness from the 244-example KNN index, and the cannot-claim list are in
`docs/ROUTING-EVAL-FACTS.md`.
