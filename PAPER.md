# Munin — publication artifact index

What the paper claims, which measurement backs each claim, and how to
reproduce it. Every number below is copied from
[`backend/benchmarks/RESULTS.md`](backend/benchmarks/RESULTS.md), which is
copied in turn from committed scorecard JSON, not from memory.

**Provenance moved to the production model between 2026-08-26 and 2026-09-15.** Claims 1, 2 and 5 (Track D,
faithfulness, T11) were re-measured on `qwen3.8-27b`
(`cyankiwi/Qwen3.8-27B-AWQ-INT4`, dense 27B, TP=2, 64k,
`reasoning_effort=medium`), which is what production serves. Claim 3
(abstention) was re-run on `qwen3.8-27b` on 2026-09-14 (C1) and 2026-09-15
(C2b, risk-coverage), so **every headline is now on the production backbone**;
the retired `qwen3.6-35b-a3b` (Qwen3.6-35B-A3B-AWQ-4bit) figures are kept
beside each as the second backbone, and **since 2026-09-17 those are
same-protocol figures**: the retired checkpoint was brought back as an
eval-only instance and run through every track on the current harness
(RESULTS.md "Retired backbone re-run"), so the Qwen3.6-vs-Qwen3.8
comparison is question-paired and controlled rather than suggestive. The
July Qwen3.6 numbers stay in the scorecard folder as the July harness.
Claim 4 (retrieval) is
**model-independent at scoring time** and was not re-run: AgentRetriever
scores against frozen Qwen3.6-era query variants generated once by the
production expander, and regenerating them would change the benchmark, not
the system. Retrieval
encoder throughout is BGE-large-en-v1.5
(1024d, collection `papers_bge`) since the 2026-07 cutover, SPECTER-v1 (768d,
`papers`) before it. **Every section of RESULTS.md states its own encoder**,
because the two are not comparable and were never meant to be pooled.
Corpus at time of writing: 68,462 papers.

**A third backbone from a different lab was measured on 2026-09-16:**
`gpt-oss-20b` (`openai/gpt-oss-20b`, 21B MoE with 3.6B active, native
MXFP4, text only), run as an eval-only instance beside production with the
whole generation suite at concurrency 1 on its own GPU (RESULTS.md "Third
backbone"). It is the cross-lab check on claims 1, 3 and 5; it is **not**
what production serves, and its numbers are reported beside the Qwen ones,
never pooled with them. Three variables move at once against Qwen3.8 (lab,
size class, the model's own recommended sampling), so its deltas are
attributed to "a different backbone", not to any one of them.

> **Drafting the paper?** [`docs/paper-kit/`](docs/paper-kit/) is a
> self-contained bundle (system, architecture, corpus, methods, results,
> ablations, findings, limitations, related work, reproduce, plus the 54
> headline scorecards) written to be read without repository access. This file
> stays the short claim-to-scorecard index.

---

## 1. Headline claim: the agentic harness is what produces the accuracy

Track D, three arms over the same 199 LitQA2 questions, paired.
`RESULTS.md` "Model swap ... Track D re-run", git `3e0bcfb`, 2026-08-26.
**Measured on three backbones from two labs** (Qwen3.6-35B-A3B, Qwen3.8-27B,
gpt-oss-20b); the table is the production model.

| arm | accuracy | precision of attempted | abstain | cost |
|---|---|---|---|---|
| RAG (naive top-5) | 0.211 | 0.420 | 0.498 | 8.3s, 0 tools |
| bare (parametric) | 0.387 | 0.403 | 0.040 | 8.0s, 0 tools |
| **agentic (harness)** | **0.874** | **0.946** | 0.075 | 157.3s, 6.9 tools |

Paired bootstrap: **agentic − bare = +0.487 [0.407, 0.568], p ≈ 0**;
agentic − RAG = +0.663 [0.598, 0.729]; RAG − bare = −0.176 [−0.251, −0.096].

**What is measured cleanly here is the WITHIN-RUN comparison.** All three arms
above ran on the same day, same code, same 199 questions, paired. That is the
claim, and it does not depend on anything outside this run.

**The same three arms on Qwen3.6, same protocol (git `556b305`,
2026-09-17; RESULTS.md "Retired backbone re-run").** The retired MoE brought
back as an eval-only instance, all arms at 16,384 tokens, 900 s,
`egress=full`, concurrency 1:

| arm | accuracy | precision of attempted | abstain | cost |
|---|---|---|---|---|
| RAG (naive top-5) | 0.126 | 0.500 | 0.749 | 8.4s, 0 tools |
| bare (parametric) | 0.337 | 0.459 | 0.226 | 15.7s, 0 tools; 8 unparseable (2 truncated) |
| **agentic (harness)** | **0.869** | **0.930** | 0.065 | 67.0s, 7.3 tools |

Paired bootstrap: **agentic − bare = +0.533 [0.452, 0.613], p ≈ 0**;
agentic − RAG = +0.744 [0.683, 0.804]; RAG − bare = −0.211 [−0.281, −0.141].

**The two Qwen backbones are a controlled comparison, question-paired.**
Agentic: Qwen3.6 − Qwen3.8 = **−0.005 [−0.050, +0.040], p = 0.93** (and
+0.000, p = 1.00, against the 09-16 Qwen3.8 recapture, one day of harness
apart). Bare: −0.050 [−0.126, +0.030], p = 0.25. RAG: **−0.085 [−0.141,
−0.035], p = 0.002**. The bare and RAG arms bypass the harness (frozen
prompts, same budget, temperature 0.7 on both), so nothing but the model
moves in those two; the agentic arm pairs against the recapture. The
sentence the paper can now say: **a dense 27B and a 35B/3B-active MoE from
the same lab reach the same accuracy inside the harness and differ only
outside it**, so the harness value is larger on Qwen3.6 (+0.533 vs +0.487)
because its floor is lower, not because its ceiling is higher. The July
version of this comparison (git `9c476b8`, 2026-07-27: RAG 0.171 / bare
0.302 / agentic 0.839, +0.538 [0.457, 0.618]) stays suggestive, since 16
`backend/retrieval/` commits sat between it and the 08-26 run; it is no
longer needed for the claim.

**On the delta, +0.538 → +0.533 → +0.487, revised.** The earlier reading here
was that the July Qwen3.6 bare arm's 4,096-token budget (33 truncated
answers) had inflated +0.538 and that +0.487 was the correction. The same
checkpoint at 16,384 tokens measures the budget at about 0.035 on the bare
arm (0.302 → 0.337, 33 → 8 unparseable) and leaves the harness value at
+0.533: the delta did not shrink when the defect was fixed, and neither
figure was inflated. The gap to Qwen3.8's +0.487 is the bare floor, inside
the noise.

Two secondary findings, both of which survive on both backbones:

- **Naive RAG is worse than no retrieval at all**: −0.176 on Qwen3.8,
  −0.211 on Qwen3.6 under the same protocol (−0.131 in July, against the
  under-budgeted bare arm), −0.306 on gpt-oss-20b. Five runs, three
  backbones, every one p < 0.001.
- **Cost is real**: ~20x bare wall-clock at 6.9 tool calls per query on
  Qwen3.8 (TP=2, users sharing); ~4.3x at 7.3 calls on Qwen3.6 alone on one
  card.

A third finding, now controlled. **Qwen3.8 abstains far less outside the
harness** (bare 0.226 → 0.040, RAG 0.749 → 0.498) and its precision of
attempted falls with it (bare 0.459 → 0.403, RAG 0.500 → 0.420). Inside the
harness abstention is 0.065 and 0.075, precision 0.930 and 0.946, accuracy
the same. The harness, not the backbone, is what keeps attempted answers
trustworthy.

**Corpus-only harness (agentic arm at `egress=off`), three backbones.** A
decomposition of the headline, not a substitute for it: Qwen3.8 (2026-09-15)
**0.663**, precision 0.917, abstain 0.276, +0.276 [0.181, 0.367] over bare,
with the web and Semantic Scholar tiers worth a further +0.211 [0.151, 0.276]
(41 of its 55 abstentions become correct at `full`; the `full` arm is three
weeks older, so that delta is egress plus drift). Qwen3.6 (2026-09-17, same
day as its `full` arm, so egress alone) **0.704**, precision 0.933, abstain
0.246, +0.367 [0.271, 0.457] over bare, external tiers +0.166 [0.101,
0.226]. gpt-oss-20b (2026-09-17) **0.482**, precision 0.828, abstain 0.382,
**+0.075 [−0.010, +0.161] over bare, p = 0.10**, external tiers +0.080
[0.015, 0.146]. The split between the corpus-only loop and the external
tiers is 57 / 43, 69 / 31 and 48 / 52; on the third backbone the corpus-only
harness is not distinguishable from bare on accuracy and changes only the
error mode (87 wrong / 6 abstain → 20 / 76). Scorecards
`2026-09-15_harness-ablation-agentic-egressoff.json`,
`2026-09-17_harness-ablation-agentic-egressoff-qwen3.6-35b-a3b.json`,
`2026-09-17_harness-ablation-agentic-egressoff-gpt-oss-20b.json`.

**Standalone LitQA2 number on the production model: 0.884 [0.839, 0.925]**,
precision 0.926, 2026-09-14, `run_litqa2 --track answer` at 900 s and
`egress=full` (RESULTS.md "LitQA2 answer, standalone track on Qwen3.8-27B").
It agrees with the ablation arm's 0.874 question-paired (+0.010 [−0.035,
+0.055], p = 0.73): one measurement taken twice. Quote 0.874 for the delta,
0.884 as the standalone result; the latter is on a harness two weeks newer
than the ablation's. **On Qwen3.6, same protocol: 0.874 [0.824, 0.920]**,
precision 0.951, 174 / 9 / 16 abstain, 2026-09-17; agrees with its ablation
arm's 0.869 (+0.005, p = 0.85) and with Qwen3.8's 0.884 question-paired
(+0.010 [−0.030, +0.050], p = 0.78). The same checkpoint on this track two
months of harness apart (07-24: 0.864) is +0.010, p = 0.70, the harness-drift
floor.

**Third backbone, a different lab: gpt-oss-20b, 2026-09-16** (RESULTS.md
"Third backbone", git `c6c56a7`). Same 199 questions, same arms, same
16,384-token budget, same 900 s deadline, `egress=full`, concurrency 1:

| arm | accuracy | precision of attempted | abstain | cost |
|---|---|---|---|---|
| RAG (naive top-5) | 0.101 | 0.800 | 0.060 | 1.0s, 0 tools; 162 no final message |
| bare (parametric) | 0.407 | 0.482 | 0.030 | 2.4s, 0 tools; 25 no final message |
| **agentic (harness)** | **0.563** | **0.896** | **0.342** | 29.3s, 8.8 tools |

Paired bootstrap: **agentic − bare = +0.156 [0.075, 0.241], p = 0.004**;
agentic − RAG = +0.462 [0.387, 0.538]; RAG − bare = −0.306 [−0.377, −0.231].
Certification gate PASS.

Three things to say, in this order:

- **The harness effect replicates outside the Qwen family, at a third of
  the size.** +0.156 against +0.487 (Qwen3.8) and +0.533 (Qwen3.6). The
  ordering of the arms is the same, RAG is again below bare, and the
  direction is not in doubt (p = 0.004). What differs is the agentic
  ceiling, not the floor: gpt-oss-20b's bare arm matches Qwen3.8's (0.407
  vs 0.387), while inside the harness it **abstains on a third of answerable
  questions** (0.342 vs 0.075) and keeps attempted-answer precision high
  (0.896 vs 0.946). The harness makes this backbone careful rather than
  correct. The paper should say that the size of the harness effect is
  backbone-dependent and that one non-Qwen point is not a generalisation
  curve.
- **Without tools, gpt-oss-20b frequently does not answer at all.** On 25
  bare and 162 RAG prompts it reasons "we need to search" and ends its turn
  with no final message (`finish_reason=stop`, ~130 tokens, no tool call,
  not one budget truncation). The protocol scores that as unparseable, i.e.
  wrong, for every backbone alike, and the prompts are frozen, so the RAG
  0.101 is mostly refusal by silence rather than the retrieval-anchoring
  failure the Qwen runs showed. `precision_of_attempted` (bare 0.48, RAG
  0.80) is the fairer read of what the model knows; both numbers belong in
  the paper. `empty_kinds` in the scorecard separates the two failure modes.
- **A new model family costs the harness something before it costs the
  model anything.** The first pass at this run is kept as
  `2026-09-16_gpt-oss-20b-prerepair.json` (agentic 0.467): vLLM's harmony
  tool parser glued channel tokens to 3% of tool names
  (`source<|channel|>commentary`), which the executor rejected, and the
  scorer did not read `**Answer:** A`, which this model writes routinely.
  Both were repaired before the headline re-run (a tool-name repair in
  `chat_service`, counted and logged; emphasis stripped before letter
  parsing, verified to change none of the 398 stored Qwen3.8 verdicts). The
  harness therefore carries one gpt-oss-shaped accommodation beside its
  Qwen-shaped ones, and the paper reports the pre-repair number as the
  price of that.

The **standalone answer track** on gpt-oss-20b gives 0.528 [0.462, 0.593],
precision 0.847, 64 abstentions, 0 truncations, agreeing with the ablation
arm's 0.563 within noise as it did on both Qwen backbones.

Scorecards: `scorecards/2026-08-26_harness-ablation.json` (current),
`scorecards/2026-09-17_harness-ablation-qwen3.6-35b-a3b.json` (Qwen3.6,
same protocol; `2026-09-17_qwen3.6-35b-a3b.json` is its run header),
`scorecards/2026-07-27_harness-ablation.json` (Qwen3.6, July harness),
`scorecards/2026-09-14_answer-qwen38-900s.json` (standalone),
`scorecards/2026-09-17_answer-qwen3.6-35b-a3b-900s.json` (Qwen3.6 standalone),
`scorecards/2026-09-16_gpt-oss-20b.json` and
`scorecards/2026-09-16_harness-ablation-gpt-oss-20b.json` (third backbone),
`scorecards/2026-09-16_gpt-oss-20b-prerepair.json` (parser-cost point),
`scorecards/2026-09-16_answer-gpt-oss-20b-900s.json` (third-backbone standalone).
Do **not** cite the 2026-07-13 pilot (n=100, 0.56/0.32/0.15); it is superseded.

## 2. Grounding improves with the harness (revised 2026-09-17)

Track B faithfulness, per-arm paired, same MiniCheck-Flan-T5-Large judge,
each arm's answer claims scored against **that arm's own retrieved
contexts**. `RESULTS.md` "Faithfulness recapture", git `1380524`.

**On `qwen3.8-27b`, with the complete evidence set (n=199): RAG 0.282
[0.248, 0.316] vs agentic 0.540 [0.503, 0.578], paired delta +0.258 [0.206,
0.311], p < 0.001.** The harness roughly doubles the fraction of answer
claims the judge can support.

**This reverses the null reported until 2026-09-16, and the reason is a
measurement defect, not a model change.** The capture's list of retrieval
tools predated the search ladder and the grounded read stage, so the
`search` and `source` results (the full-text passages the harness reads
before answering) were never counted as grounding contexts, while the RAG
arm's top-5 abstracts were captured completely. The 08-26 agentic arm was
therefore judged against web, Semantic Scholar and `paper_search` snippets
only (n=163; 36 rows unscoreable, 15 abstentions and 21 answers with no
captured context), and the "null" (+0.010,
p = 0.776) was the agentic arm being scored without most of its evidence.
Contexts are extracted at capture time, so the arm was re-captured on
2026-09-16 with the complete tool set (Qwen3.8 on production, 0.869
accuracy, paired −0.005 against the 08-26 arm's 0.874, p = 0.89) and judged
against the same RAG arm as before. Both the pre- and post-fix scorecards are
kept; the paper reports the corrected number and says why it moved.

**On the third backbone, gpt-oss-20b**: agentic 0.392 [0.330, 0.448] (n=159);
RAG 0.351 [0.208, 0.512] on the 28 questions its RAG arm answered at all;
paired +0.253 [−0.009, +0.502], p = 0.056 on the 21 questions both arms
answered. Same direction and size as Qwen3.8, underpowered by the RAG arm's
silence (claim 1). Consistent with, not a replication.

**On the retired Qwen3.6**: the 07-27 per-arm numbers (RAG 0.326, agentic
0.340, null) have the same capture gap. The checkpoint was brought back on an
eval instance on 2026-09-17 and re-captured with the complete tool set:
**agentic 0.627 [0.589, 0.660] vs RAG 0.290 [0.252, 0.328], paired +0.336
[+0.280, +0.388], p < 0.001** (n = 198). The most grounded of the three
backbones, and question-paired against the Qwen3.8 recapture +0.088 [+0.037,
+0.137], p < 0.001. Drop the 07-27 file from the faithfulness table.

The bare arm is structurally unscoreable (no retrieved context to entail
against). Absolute levels are judged by a sub-1B entailment model whose
literalness under-counts claims supported by two passages jointly
(`08-LIMITATIONS.md`).

Scorecards: `scorecards/2026-09-16_harness-ablation-faithfulness-qwen38-recapture.json`
(current), `scorecards/2026-09-16_harness-ablation-faithfulness-gpt-oss-20b-recapture.json`,
`scorecards/2026-08-26_harness-ablation-faithfulness.json` (superseded, incomplete
contexts), `scorecards/2026-09-17_harness-ablation-faithfulness-qwen3.6-35b-a3b.json`
(Qwen3.6, complete contexts, supersedes `2026-07-27_harness-ablation-faithfulness.json`).

## 3. Abstention behaviour (the novel benchmark)

Track C. Prior art exists (KnowOrNot, arXiv 2505.13545), so the claim is
narrowed to corpus-grounded abstention with a paired shadow corpus.

> All three Track C results are now on **`qwen3.8-27b`** (C1 2026-09-14, C2b
> and risk-coverage 2026-09-15), with the Qwen3.6 figures beside them from
> the 2026-09-17 re-run on the same harness and the same shadows (the July
> figures are kept in the scorecard folder). The C2b re-run had to shadow the
> chunk index (`papers_chunks_shadow`), a leak path that did not exist when
> the 07-27 pair ran; see RESULTS.md.

- **C1, fabricated papers** (n=100), **on `qwen3.8-27b`**: abstain **1.000**
  (Wilson [0.963, 1.000]), **0 confabulated local citations**, 100/100 correct
  refusals at `egress=full`, mean 5.6 tool calls per item. On Qwen3.6, same
  harness (2026-09-17): **0.980 [0.950, 1.000]**, 0/100 confabulated local
  citations, 2 possible confabulations, 10.3 calls (July: 0.970, 0/100, 10.8
  calls, so this result on this backbone is unchanged by two months of
  harness, and the halving of tool calls on Qwen3.8 is the backbone). The
  backbone that guesses more freely on its own refused every fabricated
  paper inside the harness. 13 refusals also cite a real corpus paper as
  related or as the likely intended target; spot-checked, none substitutes
  it for the asked one.
- **C2b, paired shadow corpus** at matched `egress=off` (n=50), **on
  `qwen3.8-27b`**: accuracy drops **0.540 → 0.040** when the source paper is
  removed, abstain 0.420 → 0.920. On the answerable subset (n=27), correct
  abstention is **0.889** (bootstrap [0.777, 1.000], Wilson [0.719, 0.961]);
  2 answered still correct, 1 wrong. On Qwen3.6, same harness and shadows
  (2026-09-17): **0.640 → 0.040**, abstain 0.30 → 0.86; on the answerable
  subset (n=32, the largest of any pair) correct abstention **0.875**
  (bootstrap [0.750, 0.969], Wilson [0.719, 0.950]), 1 still correct, 3
  wrong; question-paired against the Qwen3.8 pair **−0.014 [−0.154, +0.134],
  p = 0.85**. The two backbones are indistinguishable on this behaviour. The
  July pair on Qwen3.6 (2026-07-27, paper-level shadow only) gave 0.540 →
  0.080 and 0.667 [0.481, 0.852]; the +0.222 [0.040, 0.420], p = 0.015 step
  from there to Qwen3.8 read at the time as suggestive (backbone and seven
  weeks of harness moved together) and is now attributed to the harness (the
  grounded read stage and the chunk shadow), since the same backbone on the
  current harness lands at 0.875. The 2026-07-10 reading (0.200) was
  confounded by egress; every pair since runs both arms at `off`.
- **Risk-coverage**, litqa2-answerable, n=199, **on `qwen3.8-27b`**: agentic
  reaches coverage **0.925 [0.88, 0.96]** at selective risk **0.054 [0.02,
  0.09]**; bare answers more often (0.960) at ~11x the risk (0.597); RAG
  answers half (0.502) at 0.580. Qwen3.6, same harness: agentic **0.935 /
  0.070**, bare 0.734 / 0.541, RAG 0.251 / 0.500, all six points from one run
  on one commit (July: 0.925 / 0.092, 0.633 / 0.524, 0.241 / 0.292). The
  harness keeps coverage and cuts risk on both backbones while both
  baselines answer more often at ~0.55 risk.

**On the third backbone, gpt-oss-20b (2026-09-16):**

- **C1**: correct refusal **0.72 [0.64, 0.81]**, **0 confabulated local
  citations**, 4 possible confabulations, and 24 "ambiguous" of which 20 are
  empty answers (the same no-final-message behaviour as claim 1, after a mean
  of 10 tool calls). The classifier cannot call an empty answer a refusal, so
  0.72 is a floor; the number to lean on is the zero confabulated local
  citations, which holds on all three backbones.
- **C2b** at `egress=off`: present 0.40 → absent 0.04, abstain 0.34 → 0.80; on
  the 20 answerable questions, correct abstention **0.80 [0.60, 0.95]**
  (Wilson [0.58, 0.92]), 2 still correct, **0 newly wrong**. Question-paired
  against the Qwen3.8 pair: −0.09 [−0.29, +0.09], p = 0.37, within noise. The
  corpus-grounded abstention claim holds across labs; the answerable base is
  smaller because the present arm is weaker.
- **Risk-coverage**: agentic coverage 0.628 at selective risk 0.104; bare
  0.844 / 0.518; RAG 0.126 / 0.200. The harness again cuts risk by ~5x
  against bare, here by answering less rather than by being right more.

Scorecards (current): `2026-09-14_abstention-c1-fabricated.json`,
`2026-09-15_abstention-c2-shadow.json`, `2026-09-15_risk-coverage.json`.
Qwen3.6, same harness: `2026-09-17_abstention-c1-fabricated-qwen3.6-35b-a3b.json`,
`2026-09-17_abstention-c2-shadow-qwen3.6-35b-a3b.json`,
`2026-09-17_risk-coverage-qwen3.6-35b-a3b.json`.
Third backbone: `2026-09-16_abstention-c1-fabricated-gpt-oss-20b.json`,
`2026-09-16_abstention-c2-shadow-gpt-oss-20b.json`,
`2026-09-16_risk-coverage-gpt-oss-20b.json`.
Qwen3.6, July harness: `2026-07-27_abstention-c1-fabricated.json`,
`2026-07-27_abstention-c2-shadow.json`, `2026-07-27_risk-coverage.json`.

## 4. Retrieval

- **Encoder migration** (SPECTER-v1 → BGE-large): Recall@10 0.44 → 0.73 on the
  LitQA2 pool; end-to-end answer accuracy +0.075, p = 0.028. Live in
  production since 2026-07-06.
- **T8 LitSearch**: BGE-dense 0.485 nDCG@10 beats BM25 0.378 (p ≈ 0). The same
  benchmark surfaced a **production bug**: citation re-rank scored −0.368
  nDCG@10 because the citation signal spans [0,1] while BGE cosine spans 0.087,
  giving citations ~5x the intended influence. Fixed 2026-07-28 by min-max
  normalising within the pool: 0.117 → 0.469, parity with dense.
- **BEIR / SciFact** anchors external validity; see RESULTS.md Phase 3.

Scorecards: `2026-07-28_litsearch.json`, `2026-07-03_bge-large.json`,
`2026-07-06_answer-bge-large-v2.json`.

## 5. Tool-use reliability

T11, telemetry over the same 199-question run. On `qwen3.8-27b`: 1,380 tool
calls, mean **6.93 calls/query**, error rate 0.139, degraded rate 0.379, 102
queries hit at least one failure, and **recovery rate 1.000**, i.e. every
failure was recovered from within the turn.

**Recovery 1.000 replicates** (Qwen3.6, July: 1,714 calls, 8.61/query;
Qwen3.6 on the current harness, 2026-09-17: 1,458 calls, 7.33/query, 84
queries with a failure, recovery 1.000), and mean calls/query fell 8.61 →
6.93 across the swap. Those are clean comparisons. On the current harness,
one day apart under one failure definition, the two Qwen backbones differ on
`search` alone: Qwen3.8 (09-16 recapture) 0.231, a `queries` list or an
empty query each followed by a corrected call, against Qwen3.6's 0.005 in
597 calls; `web_fetch` is 0.44 and 0.40, the same paywall rate.

**The error-rate deltas are not, and must not be cited as one number.**
`web_fetch` (0.453 → 0.678) is **not comparable**: commit `2ff9aef` landed
between the runs and changed what counts as a failure, so anti-bot
interstitials that were previously summarised as content are now errors. The
old figure counted those as successes. `search` (0.000 → 0.122) **is**
comparable and is a genuine behavioural finding: every failure was an argument
type Qwen3.8 emitted and Qwen3.6 did not.

Both causes were fixed after this run (`fd559c9`), so these figures describe
the tool layer during the comparison rather than as shipped. Reported per tool,
with the full reasoning, in RESULTS.md.

**On gpt-oss-20b (2026-09-16, after the tool-name repair):** 1,742 calls,
**8.75 calls/query**, error rate 0.049, degraded rate 0.059, **recovery
1.000**. Recovery 1.000 now holds on three backbones. The tool mix is very
different: 85% of calls go through the corpus `search` ladder, 12% through
`source`, and the model left the corpus 17 times in 199 questions (Qwen3.8:
331 web searches, 175 Semantic Scholar). `web_fetch`'s 0.56 error rate matches
Qwen3.8's under the current failure definition. Before the repair, 3% of tool
names arrived with a harmony channel token attached and were rejected as
unknown tools; that is a serving-stack (parser) property, reported as such.

Scorecards: `2026-08-26_toolreliability-qwen38_toolreliability.json` (current),
`2026-09-17_toolreliability-qwen3.6-35b-a3b_toolreliability.json` (Qwen3.6,
current harness), `2026-07-27_toolreliability-clean.json` (Qwen3.6, July),
`2026-09-16_toolreliability-gpt-oss-20b_toolreliability.json` (third backbone).

---

## What is NOT claimed

Stating these plainly is cheaper than being asked.

| Item | Status | Why |
|---|---|---|
| Phase 4 local query pool | **deferred** | blocked on human query curation and two-annotator qrels, not compute |
| T3 stratum 2, T7 answer-level local pool | deferred | both depend on Phase 4 |
| MiniCheck as a headline judge (T4) | dropped | validated but not used for the headline; faithfulness is reported from the local judge |
| T9 RAGAS external cross-check | dropped | documented in the benchmark TODO |
| Track F | not run | follow-up proposal scope |
| BEIR nfcorpus / scidocs / trec-covid | not run | SciFact only |
| Human-expert comparison | context, not a claim | PaperQA2 0.660 and expert mean 0.677 are quoted from their sources, not re-measured here |
| A controlled backbone comparison beyond the Qwen pair | Qwen pair only | since 2026-09-17 the Qwen3.6-vs-Qwen3.8 comparison is controlled (same protocol, question-paired; bare/RAG bypass the harness, agentic pairs against the 09-16 recapture); the July-vs-August version stays suggestive, and the cross-lab comparison moves lab, size class and sampling at once |
| Cross-lab generalisation beyond one model | one point, not a curve | gpt-oss-20b is the only non-Qwen backbone measured; the harness effect replicated at a third of the size. "The harness works on any model" is not claimed |
| A 16 GB reproduction | not measured | the Qwen3.5-9B hardware-floor run (THIRD-MODEL-REVIEW Experiment B) was deferred; gpt-oss-20b needs a 24 GB card at the production profile (12.8 GiB weights + 1.5 GiB KV) |
| Faithfulness on gpt-oss-20b as a replication | measured, underpowered | agentic 0.392 (n=159); the paired RAG comparison has n=21 because the RAG arm answers 37 of 199. Consistent with claim 2, not a replication of it |
| Routing on gpt-oss-20b as a paper number | deploy gate only | anchor-tier pass rate 0.647 [0.45, 0.84] vs 0.963 on Qwen3.8; it says the backbone would not ship behind the router as-is, nothing more |

## Reproducing

Full command set, per track, lives in
[`RESULTS.md` → "Reproduce"](backend/benchmarks/RESULTS.md#reproduce). The
short version:

```bash
cd backend/benchmarks
export PYTHONPATH=$HOME/.cache/munin_bench_deps:.
export NEO4J_PASSWORD=...                      # from /opt/hugin/config/cluster.env
PY=/opt/munin/services/pipeline/venv/bin/python

$PY -m pytest tests/                           # unit gates
$PY -m munin_bench.pipelines.run_all \
   --tag <label> --encoder bge-large \
   --tracks litqa2-retrieval,litqa2-answer,faithfulness,abstention,ablation \
   --with-reliability --certify --date <YYYY-MM-DD>
```

A second backbone beside production, whole generation suite, unattended:
`scripts/run_suite.sh <slug>` with a profile in `backend/config/models/`
(RESULTS.md "Third backbone"; `docs/paper-kit/10-REPRODUCE.md` §5a).

`--certify` checks the run against `certification_thresholds.json` and emits
PASS/FAIL; `munin_bench.pipelines.compare <old>.json <new>.json` gives a
paired-bootstrap regression diff between two runs.

**`--encoder bge-large` is not optional.** `run_all` defaults to `specter-v1`,
the retired 768d `papers` collection, so the command without it reproduces the
pre-migration retrieval numbers (Recall@10 0.44) rather than the ones claimed
here (0.73). `beir-scifact` is deliberately absent from the track list: that
track builds a 768d eval collection and `run_all` refuses it with any preset
other than `specter-v1`, so it cannot share a run with the BGE tracks. The
SciFact anchor is its own command, `run_beir --subset scifact` (SPECTER) and
`run_bakeoff --subset scifact` (per-encoder), see RESULTS.md "Reproduce".

Two operational notes that will otherwise cost you a day:

- **Concurrency must not exceed vLLM `max-num-seqs`**, or the run silently
  degrades (this is what produced the misleading 0.688 agentic figure).
- **Evals default to `egress=off`** (`X-Munin-Egress`), so a benchmark cannot
  spend the Brave or Semantic Scholar quota. Set `MUNIN_EVAL_EGRESS=full`
  deliberately for a live-web run, and record which you used: the C2 result
  above changed sign on exactly this.

## Where everything lives

| Artifact | Path |
|---|---|
| **Paper kit** (self-contained drafting bundle, 54 scorecards) | `docs/paper-kit/` |
| Results log (canonical numbers) | `backend/benchmarks/RESULTS.md` |
| Scorecards (83 JSON, 53 Markdown) | `backend/benchmarks/scorecards/` |
| Backbone profiles (one file per model: checkpoint, parsers, thinking mode, sampling) | `backend/config/models/` |
| Second-backbone driver and instance machinery | `backend/benchmarks/scripts/run_suite.sh`, `backend/deploy.sh instance` |
| Benchmark harness | `backend/benchmarks/munin_bench/` |
| Certification thresholds | `backend/benchmarks/certification_thresholds.json` |
| Paper track: plans, specs, open items | `docs/paper-track/` |
| Agent track: architecture, Deep Research | `docs/agent-track/` |
| Design decisions (the *why*) | `shared/docs/DECISIONS.md` |
| Harness audit vs Claude Code | `docs/architecture/HARNESS-AUDIT-2026-05.md` |
| Routing eval fact sheet (what 0.963 measures, anchor-set provenance, cannot-claim list) | `docs/ROUTING-EVAL-FACTS.md` |
| Corpus quality evidence | `backend/docs/corpus-quality/` (aggregate summaries; the per-record outputs stay with the deployment) |
