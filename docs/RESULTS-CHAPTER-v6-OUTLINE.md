# Results chapter, clean outline for v6 (2026-09-17, Qwen3.6 column refreshed 2026-09-20)

Scope: what stays in §5, table by table, with numbers from the kit refreshed
2026-09-20 (`05-RESULTS.md`, 54 scorecards). Prose is bullets; you write the
sentences. Development history is in §6.1 (outline at the end); run-level
forensics stay in the scorecards.

**What changed on 2026-09-20.** The Qwen3.6 column throughout is now the
2026-09-17 run: the retired checkpoint brought back as an eval-only instance
and run through every track on the current protocol (all arms at 16,384
tokens, chunk-level shadow, complete faithfulness contexts, an egress = off
arm). Every Qwen3.6 cell is therefore same-protocol with the Qwen3.8 and
gpt-oss cells, the July caveats (4,096-token bare arm, unmeasurable
faithfulness, "suggestive" cross-backbone comparisons) are gone from §5, and
the July numbers move to §6.1 as history. Two readings changed as a result
and are flagged inline: the harness delta did not shrink when the bare-arm
budget was fixed (5.1), and the C2b step from 0.667 to 0.889 was the
harness, not the backbone (5.3.2).

**Conventions**

- Three measured backbones, in this order in every table:
  **Qwen3.6-35B-A3B** (retired from production 2026-08-25; MoE, 3B active;
  measured 2026-09-17 on the current protocol as an eval instance) |
  **Qwen3.8-27B** (production; dense) |
  **gpt-oss-20b** (third backbone, different lab; 21B MoE, 3.6B active,
  native MXFP4, ~13 GB — the ≤16 GB single-GPU class). A fourth column can
  be added if a small Qwen run still happens; nothing is placeholdered for
  it.
- gpt-oss-20b is reported in its own column, never pooled: three variables
  move at once (lab, size class, the vendor's recommended sampling), and its
  bare/RAG arms show a behaviour the Qwen runs never did — the turn ends
  with **no final message** (25/199 bare, 162/199 RAG), scored as wrong by
  the frozen protocol. Precision of attempted is the fairer read of those
  two arms on that backbone; say so once in §5.1 and once in the caption.
- Headline backbone is Qwen3.8; Qwen3.6 stays the narrative spine of §6.1.
- The two Qwen backbones are a controlled comparison (bare/RAG arms bypass
  the harness; the agentic arm pairs against the 09-16 Qwen3.8 recapture one
  day of harness apart). gpt-oss is not (lab, size class, sampling move at
  once). Say this once, in 5.4.
- `—` = not measured on that backbone; `n/a` = undefined for that arm.
- Every LLM-in-the-loop table carries one provenance line: scorecard files,
  n, egress, output budget.
- Retrieval tables: *backbone-independent at scoring time; frozen
  Qwen3.6-era query variants*.
- Judge validation (RAGTruth) lives in §4.5, not here.
- Provisional numbering 5.1–5.12; renumber at the end.

---

## 5.1 Does the harness improve the base model?

- Three arms, same 199 in-corpus LitQA2 questions, paired: bare (no tools),
  naive RAG (fixed top-5, one completion), full harness. All arms:
  concurrency 1, 16,384-token output budget, 900 s; agentic at
  egress = full; bare and RAG make no outbound requests.
- Caption note, not prose: the Qwen3.6 column is the 2026-09-17 run of the
  retired checkpoint on this protocol; its July run (bare arm at 4,096
  tokens, 33 truncated) is §6.1 history. Qwen3.6's 8 unparseable bare
  answers are 6 without a letter and 2 that ran the 16,384 budget out (no
  reasoning-effort dial on that model).

**Table 5.1 Three-arm ablation, n = 199 paired.**

| Arm | Metric | Qwen3.6-35B-A3B | Qwen3.8-27B | gpt-oss-20b |
|---|---|---|---|---|
| Bare | accuracy | 0.337 | 0.387 | 0.407 |
| | precision (attempted) | 0.459 | 0.403 | 0.482 |
| | abstain | 0.226 | 0.040 | 0.030 |
| | unparseable | 8 (2 truncated) | 0 | 25 (no final message) |
| RAG (top-5) | accuracy | 0.126 | 0.211 | 0.101 |
| | precision (attempted) | 0.500 | 0.420 | 0.800 |
| | abstain | 0.749 | 0.498 | 0.060 |
| | unparseable | 0 | 0 | 162 (no final message) |
| **Agentic** | **accuracy** | **0.869** | **0.874** | **0.563** |
| | precision (attempted) | 0.930 | 0.946 | 0.896 |
| | abstain | 0.065 | 0.075 | 0.342 |
| | unparseable | 0 | 0 | 6 |
| | tool calls / query | 7.3 | 6.9 | 8.8 |
| Agentic, egress = off | accuracy | 0.704 | 0.663 | 0.482 |
| (corpus-only) | precision (attempted) | 0.933 | 0.917 | 0.828 |
| | abstain | 0.246 | 0.276 | 0.382 |
| | unparseable | 0 | 0 | 7 |
| | tool calls / query | 10.5 | 5.1 | 7.4 |

Provenance: `2026-09-17_harness-ablation-qwen3.6-35b-a3b` (git 556b305),
`2026-08-26_harness-ablation` (3e0bcfb, headline),
`2026-09-16_harness-ablation-gpt-oss-20b` (c6c56a7); egress = off arms
`2026-09-17_…-agentic-egressoff-qwen3.6-35b-a3b` (same day as its full
arm), `2026-09-15_harness-ablation-agentic-egressoff` (3be8308; bare/RAG
copied from 08-26), `2026-09-17_…-agentic-egressoff-gpt-oss-20b` (cb8faa5).
Wall-clock in Table 5.11.

**Table 5.2 Paired accuracy differences (bootstrap, seed 42).**

| Comparison | Qwen3.6 Δ [95% CI] | Qwen3.8 Δ [95% CI] | gpt-oss-20b Δ [95% CI] |
|---|---|---|---|
| **agentic − bare** | **+0.533 [0.452, 0.613]** | **+0.487 [0.407, 0.568]** | **+0.156 [0.075, 0.241]**, p = 0.004 |
| agentic − RAG | +0.744 [0.683, 0.804] | +0.663 [0.598, 0.729] | +0.462 [0.387, 0.538] |
| RAG − bare | −0.211 [−0.281, −0.141] | −0.176 [−0.251, −0.096] | −0.306 [−0.377, −0.231] |
| agentic (egress off) − bare | +0.367 [0.271, 0.457] | +0.276 [0.181, 0.367] | +0.075 [−0.010, +0.161], p = 0.10 |
| agentic (full) − agentic (off) | +0.166 [0.101, 0.226] | +0.211 [0.151, 0.276] | +0.080 [0.015, 0.146], p = 0.02 |

All p < 0.001 unless stated.

**Table 5.2a The two Qwen backbones, question-paired on the same protocol
(Qwen3.6 − Qwen3.8).**

| Arm | Δ [95% CI] | p | Controlled by |
|---|---|---|---|
| Bare | −0.050 [−0.126, +0.030] | 0.25 | the arm bypasses the harness (frozen prompt, same budget, temperature 0.7) |
| RAG | −0.085 [−0.141, −0.035] | 0.002 | same |
| **Agentic** | **−0.005 [−0.050, +0.040]** | **0.93** | +0.000 vs the 09-16 Qwen3.8 recapture, one day of harness apart |

Prose bullets:
- Harness value +0.487 on the production backbone; not from abstaining more
  (0.075 vs 0.040); every arm parseable on all 199.
- The two Qwen backbones reach the same ceiling inside the harness (0.869
  vs 0.874) and differ only outside it: Qwen3.8 is the stronger model bare
  (n.s.) and under RAG (p = 0.002). So the harness value is larger on
  Qwen3.6 (+0.533 vs +0.487) because its floor is lower, not because its
  ceiling is higher. **Reading changed 2026-09-20**: the earlier draft said
  the July +0.538 was inflated by the 4,096-token bare arm and +0.487 was
  the correction; the same checkpoint at 16,384 measures the budget at
  ~0.035 on the bare arm with the harness value unchanged (+0.538 → +0.533).
  Neither figure was inflated. §6.1 keeps one sentence.
- Ordering, sign of every delta and significance replicate on gpt-oss; the
  size does not (finding 8c). Its bare arm is within 0.02 of Qwen3.8's, its
  agentic arm 0.31 below both Qwen arms: the harness makes that model
  careful rather than correct (abstain 0.342 at precision 0.896).
- Decomposition, three backbones: corpus-only loop / external tiers split
  57 / 43 on Qwen3.8 (+0.276 / +0.211), 69 / 31 on Qwen3.6 (+0.367 /
  +0.166, same day, so egress alone), 48 / 52 on gpt-oss (+0.075 / +0.080).
  On gpt-oss the corpus-only harness is not distinguishable from bare on
  accuracy (p = 0.10); it changes the error mode only (87 wrong / 6 abstain
  → 20 / 76). Transitions full → off on Qwen3.8: 125 stay correct, 41
  correct → abstain, 8 correct → wrong, 4 the reverse; on Qwen3.6: 134, 34,
  5, 6. With the web gone the Qwen harness abstains rather than guesses.
  The Qwen3.8 +0.211 is egress plus 26 commits of harness drift; the
  Qwen3.6 +0.166 is egress alone.
- gpt-oss RAG 0.101 is mostly refusal by silence (162 empty turns), not the
  anchoring effect below; precision of attempted 0.80 is what it knows.

### 5.1.1 Naive retrieval is worse than no retrieval

- RAG − bare negative on all three backbones, five runs, one sign; largest
  on Qwen3.6 under the current protocol (−0.211).
- Mechanism on the Qwen family: anchors on retrieved abstracts and declines
  (abstain 0.498 vs 0.040 on Qwen3.8; 0.749 vs 0.226 on Qwen3.6). On
  gpt-oss the same sign arises differently (silence, not refusal); say so.
- Drop the 100-question pilot.

---

## 5.2 Does the harness improve grounding?

- Same judge (§4.5), claim-level entailment against **each arm's own
  contexts**; per-answer mean of supported-claim fraction; bare has no
  evidence set and is undefined.
- The Qwen3.8 agentic arm was re-captured 2026-09-16 with the complete
  retrieval-tool set (the earlier capture omitted `search`/`source`
  contexts, 446 of 1,380 calls); accuracy of the recaptured arm is the
  headline's within noise (0.869 vs 0.874, paired −0.005, p = 0.89). The
  Qwen3.6 arm (2026-09-17) was captured complete from the start. Quote only
  complete-context rows.

**Table 5.3 Faithfulness, supported-claim fraction, per-answer mean.**

| Arm | Qwen3.6 | n | Qwen3.8 | n | gpt-oss-20b | n |
|---|---|---|---|---|---|---|
| RAG (top-5) | 0.290 [0.252, 0.328] | 198 | 0.282 [0.248, 0.316] | 199 | 0.351 [0.208, 0.512] | 28 |
| **Agentic** | **0.627 [0.589, 0.660]** | 199 | **0.540 [0.503, 0.578]** | 199 | 0.392 [0.330, 0.448] | 159 |
| Bare | n/a | | n/a | | n/a | |
| **Paired Δ agentic − RAG** | **+0.336 [0.280, 0.388], p < 0.001** | 198 | **+0.258 [0.206, 0.311], p < 0.001** | 199 | +0.253 [−0.009, +0.502], p = 0.056 | 21 |

Cross-backbone, agentic arm, question-paired: Qwen3.6 − Qwen3.8 = +0.088
[0.037, 0.137], p < 0.001; the RAG arms agree (0.290 vs 0.282).

Provenance: `2026-09-17_harness-ablation-faithfulness-qwen3.6-35b-a3b`,
`2026-09-16_harness-ablation-faithfulness-qwen38-recapture` (headline),
`…-gpt-oss-20b-recapture`; RAG arms from the same runs (top-5 abstracts
always complete). 26 (Qwen3.8) and 38 (gpt-oss) grounding passages per
question; the Qwen3.6 capture carried 1,028 claims against 7,531 context
chunks.

Prose bullets:
- Grounding roughly doubles with the harness on the production backbone and
  more than doubles on Qwen3.6: the loop reads the paragraph it cites. Same
  sign and size on gpt-oss, underpowered on the RAG side (162 empty RAG
  answers leave n = 21).
- The one place the two Qwen backbones separate inside the harness: same
  accuracy, more of the MoE's claims trace to a passage it read (+0.088).
- 0.54 and 0.63 are floors: a sentence-level entailment judge undercounts
  claims supported jointly by two passages.
- The unsupported 37 to 46% is not fabrication: C1 gives 0 confabulated
  local citations on all three backbones (5.3).
- One sentence of provenance honesty: the earlier null (two backbones,
  three runs) was a measurement defect; replication across backbones does
  not protect against a shared capture gap. Expand in §6.1.

---

## 5.3 Does the system know when its corpus lacks the answer?

Both tests on all three backbones (C2b at egress = off; C1 at egress = full,
the harder condition).

### 5.3.1 Fabricated papers (C1)

**Table 5.4 C1, 100 frozen items (80 nonexistent DOIs, 20 by description).**

| Metric | Qwen3.6 | Qwen3.8 | gpt-oss-20b |
|---|---|---|---|
| Correct-refusal rate | 0.980 [0.950, 1.000] | **1.000** (Wilson [0.963, 1.000]) | 0.720 [0.640, 0.810] (floor†) |
| **Confabulated local citations** | **0 / 100** | **0 / 100** | **0 / 100** |
| Verdicts (abstain / possible / ambiguous) | 98 / 2 / 0 | 100 / 0 / 0 | 72 / 4 / 24 |
| Mean tool calls / item | 10.3 | 5.6 | 9.2 |

† 20 of the 24 gpt-oss "ambiguous" items are empty turns (no final message
after a mean of 10 tool calls); the classifier cannot call an empty answer a
refusal, so 0.720 is a floor on correct refusal and 4/100 the ceiling on
confabulation.

Provenance: `2026-09-17_…-qwen3.6-35b-a3b`, `2026-09-14_`,
`2026-09-16_…abstention-c1-fabricated…`. Scored judge-free by corpus
membership of emitted DOIs. The number that holds on all three without
qualification is zero confabulated local citations. The halving of tool
calls on Qwen3.8 is the backbone: Qwen3.6 spends ~10 per item on both
harness generations (10.8 in July, 10.3 now).

### 5.3.2 Paired shadow corpus (C2b)

**Table 5.5 C2b, 50 single-source questions, present vs absent, egress = off.**

| | Qwen3.6 | | Qwen3.8 | | gpt-oss-20b | |
|---|---|---|---|---|---|---|
| | present | absent | present | absent | present | absent |
| Accuracy | 0.640 | 0.040 | **0.540** | **0.040** | 0.400 | 0.040 |
| Abstain rate | 0.300 | 0.860 | 0.420 | 0.920 | 0.340 | 0.800 |
| Accuracy drop on removal | −0.600 | | −0.500 | | −0.360 | |

**Table 5.6 Behaviour on the answerable subset when the source is removed
(multinomial).**

| Outcome | Qwen3.6 (n = 32) | Qwen3.8 (n = 27) | gpt-oss-20b (n = 20) |
|---|---|---|---|
| **Correct abstention** | **0.875 [0.750, 0.969]** | **0.889 [0.777, 1.000]** | **0.800 [0.600, 0.950]** |
| Answered, still correct | 0.031 | 0.074 | 0.100 |
| Answered, now wrong | 0.094 | 0.037 | **0.000** |
| Paired Δ vs Qwen3.8 (50 questions) | −0.014 [−0.154, +0.134], p = 0.85 | | −0.089 [−0.292, +0.092], p = 0.37 |

Provenance: `2026-09-17_…-qwen3.6-35b-a3b`, `2026-09-15_`,
`2026-09-16_…abstention-c2-shadow…`; shadow verified 49/49 present, 0/49 in
shadow each run, both collections (`papers_bge`, `papers_chunks`) shadowed in
all three. Bootstrap conditional on the answerable subset; Wilson alongside
(n = 20–32; Qwen3.6 Wilson [0.719, 0.950]).

Prose bullets:
- The three backbones are indistinguishable on this behaviour (0.875 /
  0.889 / 0.800, both paired deltas null). **Reading changed 2026-09-20**:
  the July Qwen3.6 pair gave 0.667 and the +0.222 (p = 0.015) step to
  Qwen3.8 read as a backbone effect with a harness confound; the same
  checkpoint on the current harness and shadows lands at 0.875, so the step
  was the grounded read stage and the chunk shadow. One sentence in §6.1.
- Qwen3.6 has the largest answerable base (32 of 50 present-arm correct).
- gpt-oss-20b: zero over-confident answers when the source is removed.
- Egress = full is a different experiment: on Qwen3.6, absent-arm accuracy
  0.080 → 0.740 because 17 of 49 withheld sources are re-fetched from the
  web (+0.28 on that set). One sentence, as a web-tier result.

### 5.3.3 Risk-coverage operating points

**Table 5.7 Coverage / selective risk (item-level bootstrap, 2000 resamples).**

| Population | Arm | Desired | Qwen3.6 | Qwen3.8 | gpt-oss-20b | Egress |
|---|---|---|---|---|---|---|
| litqa2-answerable | bare | answer | 0.734 / 0.541 | 0.960 / 0.597 | 0.844 / 0.518 | n/a |
| litqa2-answerable | RAG | answer | 0.251 / 0.500 | 0.502 / 0.580 | 0.126 / 0.200 | n/a |
| litqa2-answerable | **agentic** | answer | **0.935 / 0.070** | **0.925 / 0.054** | **0.628 / 0.104** | full |
| c2-present | agentic | answer | 0.700 / 0.086 | 0.580 / 0.069 | 0.500 / 0.200 | off |
| c2-absent | agentic | abstain | 0.140 / 0.714 | 0.080 / 0.500 | 0.060 / 0.333 | off |
| c1-fabricated | agentic | abstain | 0.020 / (n.m.) | 0.000 / (n.m.) | 0.280‡ / (n.m.) | full |

‡ 28 answered items of which 20 are empty turns; read on coverage only.

Provenance: `2026-09-17_…-qwen3.6-35b-a3b` (all six points from one run on
one commit), `2026-09-15_`, `2026-09-16_…risk-coverage…`.

Prose bullets: on both Qwen backbones the agentic arm keeps coverage
(0.935 / 0.925) at selective risk 0.070 / 0.054, while bare answers more
often (0.734 / 0.960) at ~0.55 risk and RAG collapses coverage. On gpt-oss
the harness cuts selective risk ~5× (0.518 → 0.104) by answering a third
less often (0.844 → 0.628). Abstain-desired populations move toward zero
coverage on all three. The Qwen3.8 and gpt-oss points mix capture dates;
egress differs by population on all three; no single curve.

---

## 5.4 Does the harness hold across backbones?

(This is where the third column pays off.)

**Table 5.8 Harness value and the behaviour it controls, per backbone.**

| | Qwen3.6-35B-A3B | Qwen3.8-27B | gpt-oss-20b |
|---|---|---|---|
| Lab / class | Alibaba, MoE 35B/3B | Alibaba, dense 27B | OpenAI, MoE 21B/3.6B |
| Bare accuracy | 0.337 | 0.387 | 0.407 |
| Agentic accuracy | 0.869 | 0.874 | 0.563 |
| **Harness value** | **+0.533 [0.452, 0.613]** | **+0.487 [0.407, 0.568]** | +0.156 [0.075, 0.241] |
| Corpus-only harness value (egress off − bare) | +0.367 | +0.276 | +0.075 (n.s.) |
| Abstain outside harness (bare / RAG) | 0.226 / 0.749 | 0.040 / 0.498 | 0.030 / 0.060 (+ empty turns) |
| Abstain inside harness | 0.065 | 0.075 | 0.342 |
| Precision of attempted, bare → agentic | 0.459 → 0.930 | 0.403 → 0.946 | 0.482 → 0.896 |
| Faithfulness, agentic | 0.627 | 0.540 | 0.392 |
| Faithfulness Δ agentic − RAG | +0.336 | +0.258 | +0.253 (n = 21) |
| Correct abstention, source withheld (C2b) | 0.875 | 0.889 | 0.800 |
| Correct refusal, fabricated (C1) | 0.980 | 1.000 | ≥ 0.720 |
| Confabulated local citations | 0 | 0 | 0 |
| Comparison to Qwen3.8 | controlled (Table 5.2a) | headline | suggestive (lab, size, sampling) |

Prose bullets:
- Replicates in kind, not in size: ordering, every sign and significance
  hold across two labs; the harness value is a backbone property.
- Inside the Qwen family the harness holds attempted answers to the same
  bar (finding 8b), and now controlled: the newer model guesses more
  outside (abstain 0.226 → 0.040, precision 0.459 → 0.403), abstains the
  same inside (0.065 / 0.075) at the same accuracy (paired p = 0.93) and
  similar precision (0.930 / 0.946), and abstains equally correctly when
  the source is withheld (0.875 / 0.889). A dense 27B and a 3B-active MoE
  from one lab are one point on accuracy; they differ on grounding (+0.088
  for the MoE) and on tool-argument hygiene (5.6).
- On gpt-oss the same harness produces caution instead: it declines a third
  of answerable questions after reading, at 0.896 precision, and never
  over-answers a withheld source; its corpus-only half of the harness value
  is not distinguishable from zero. Where to look for the reason when a
  swap halves the headline: the abstention column, not accuracy.
- Run-to-run and drift floor: the Qwen3.8 recapture, 26 commits newer, is
  −0.005 [−0.050, +0.040], p = 0.89 against the headline; the same Qwen3.6
  checkpoint on the standalone track two months of harness apart is +0.010,
  p = 0.70 (179 of 199 verdicts identical); two identical bare arms a day
  apart differed by 0.035. Sub-3-point deltas are inside the floor.
- The Qwen pair is controlled (bare/RAG bypass the harness; agentic pairs
  against the recapture one day apart). The gpt-oss comparison is
  suggestive (lab, size class, sampling); its within-run deltas are clean.
- One sentence on the routing deploy gate: at 0.647 pass rate on the anchor
  suite, gpt-oss-20b would not ship behind the router as-is (Qwen3.6, two
  months of router changes after it was the production model: 0.816, plus
  an empty-final-message failure on the behavioural probe); a new model
  family costs the harness something before it costs the model anything
  (the parser repair and scorer fix in §6.1).

---

## 5.5 Retrieval on external anchors

Backbone-independent at scoring time; frozen Qwen3.6-era query variants.

**Table 5.9 SciFact (BEIR), nDCG@10.**

| Retriever | nDCG@10 [95% CI] |
|---|---|
| BM25 | 0.652 [0.61, 0.70] |
| SPECTER-dense | 0.479 [0.43, 0.53] |
| RRF [BM25, SPECTER] | 0.621 [0.58, 0.67] |

**Table 5.10 LitSearch, 597 queries, 64,183-paper corpus.**

| Retriever | nDCG@10 [95% CI] | R@10 | R@100 | MRR |
|---|---|---|---|---|
| BM25 | 0.378 [0.345, 0.413] | 0.511 | 0.699 | 0.349 |
| **BGE-dense (production)** | **0.485 [0.453, 0.516]** | 0.637 | 0.829 | 0.451 |
| Citation re-rank 0.7/0.3 | 0.469 [0.437, 0.503] | 0.609 | 0.829 | 0.442 |
| RRF [BM25, BGE] | 0.490 [0.455, 0.526] | 0.628 | 0.830 | 0.462 |

Bullets: BM25 reproduces published BEIR; dense beats BM25 on LitSearch
by +0.107 (p ≈ 0); citation re-rank −0.016 (p = 0.014); RRF n.s.; domain
caveat (ML/NLP). Pre-fix re-rank row → §6.1.

---

## 5.6 Reliability and cost

**Table 5.11 Tool-use reliability and cost, agentic arm of Table 5.1.**

| | Qwen3.6 | Qwen3.8 | gpt-oss-20b |
|---|---|---|---|
| Tool calls, total | 1,458 | 1,380 | 1,742 |
| Calls / query | 7.33 | 6.93 | 8.75 |
| Queries with ≥ 1 tool failure | 84 | 102 | 58 |
| **Recovery rate** | **1.000** | **1.000** | **1.000** |
| Recovery rate, egress off | 1.000 | 1.000 | 0.965 |
| Wall-clock / query, agentic (egress full) | 67.0 s | 157.3 s | 29.3 s |
| Wall-clock / query, agentic (egress off) | 54.1 s | 96.0 s | 21.3 s |
| Wall-clock / query, bare | 15.7 s | 8.0 s | 2.4 s |
| Agentic : bare | 4.3× | ~20× | ~12× |
| Serving | 1 GPU alone, AWQ-4bit, 3B active | TP = 2 with users, dense 27B | 1 GPU alone, MXFP4, 3.6B active |

Bullets:
- Failures absorbed, not propagated, on three backbones at egress = full;
  the one sub-1.000 recovery in the suite is gpt-oss at egress = off (2 of
  57 failing queries).
- Cost is the backbone's per-token cost under its profile, not a harness
  property; not comparable across backbones (Qwen3.8 shared its GPUs with
  users; the other two owned a card). gpt-oss's 29 s/query and Qwen3.6's
  67 s/query on one card are the single-GPU cost points.
- Tool mix differs by lab, not by Qwen generation: gpt-oss stays in the
  corpus search ladder (85% of calls) and left it 17 times in 199
  questions; Qwen3.8 issued 331 web searches, Qwen3.6 362. One sentence.
- Tool-argument hygiene is a backbone property: on the current harness,
  one day apart, `search` errors are 0.231 on Qwen3.8 (a `queries` list or
  an empty query, each followed by a corrected call) and 0.005 on Qwen3.6.
  One sentence.
- Egress-off arms as guard test: every web_search and Semantic Scholar call
  blocked on all three (94 + 51 on Qwen3.8, 171 + 48 on Qwen3.6), zero
  outbound; without the web Qwen3.6 searches the corpus about twice as hard
  (10.5 calls/query vs 7.3).
- Fault-injection sentence stays. Dropped: error/degraded rates, per-tool
  table, parser forensics (→ §6.1 one sentence).

---

## 5.7 Comparison with published baselines

**Table 5.12 LitQA2 accuracy / precision (quoted, not re-measured).**

| System | Accuracy | Precision | Note |
|---|---|---|---|
| PaperQA2 (Skarlinski et al. 2024) | 0.660 ± 0.012 | 0.852 ± 0.011 | trained on LitQA2 splits |
| Human experts (n = 9) | 0.677 ± 0.119 | 0.738 ± 0.096 | |
| Munin, Qwen3.6 | 0.874 [0.824, 0.920] | 0.951 [0.918, 0.978] | standalone, 2026-09-17; ablation arm 0.869 / 0.930 |
| **Munin, Qwen3.8** | **0.884 [0.839, 0.925]** | **0.926 [0.889, 0.963]** | standalone, 2026-09-14; ablation arm 0.874 / 0.946 |
| Munin, gpt-oss-20b | 0.528 [0.462, 0.593] | 0.847 [0.774, 0.911] | standalone, 2026-09-16; ablation arm 0.563 / 0.896; 11 empty answers |

Check the human-expert row against v4 Table 33 (kit says 0.677 / 0.738,
verified 2026-07-25 against arXiv 2409.13740v2; the v4 draft had 0.634 /
0.719).

Bullets: standalone and ablation protocols agree on all three backbones
(Qwen3.8 paired +0.010 [−0.035, +0.055], p = 0.73, 175/199 identical
verdicts; Qwen3.6 +0.005, p = 0.85, 180/199). The two Qwen backbones agree
on the standalone track too (+0.010, p = 0.78). Three caveats in-paragraph:
PaperQA2 trained on LitQA2; in-corpus 199-question subset; ±0.035 noise
floor. The sovereignty number: corpus-only Qwen3.8 (0.663, precision 0.917)
and corpus-only Qwen3.6 (0.704, precision 0.933) both clear PaperQA2's 0.660
with zero outbound traffic; corpus-only gpt-oss (0.482) does not.

---

## §6.1 Why the design looks the way it does (moved out of §5)

One paragraph per lever, each ending in the decision it forced.

- **Retrieval was not the ceiling.** SPECTER → BGE-large: Recall@10
  0.44 → 0.73, accuracy +0.075. Decision: stop optimising retrieval.
- **The bottleneck was the reading path.** Summarise-and-discard deleted the
  answer; oracle ceiling 0.82; pinned full-text `source` agent cleared it at
  0.864. Decision: read full text.
- **Naive RAG is an anchor.** Pointer to 5.1.1. Decision: iterative loop.
- **Prompts do not move behaviour; code does.** Two prompt levers null or
  backfired; the 30-call cap worked. Decision: guardrails are code.
- **The suite catches bugs in the system and in itself.** Three, in one
  paragraph: the LitSearch A/B exposed a score-scale mismatch in citation
  re-ranking (0.117 → 0.469); the bare-arm budget defect (4,096 tokens, 33
  truncated Qwen3.6 answers in July) was believed to have inflated the first
  harness value until the same checkpoint at 16,384 tokens measured it at
  ~0.035 on the bare arm with the harness value unchanged (+0.538 → +0.533);
  and the faithfulness capture gap produced a null that replicated across
  two backbones and three runs before the recapture reversed it (+0.010 →
  +0.258 on Qwen3.8, and +0.336 on Qwen3.6 once that checkpoint was brought
  back). Decision: ship the suite, keep superseded scorecards, and treat
  replication as no protection against a shared measurement defect.
- **The July Qwen3.6 numbers, in one sentence each.** Track D 0.839 / 0.302
  / 0.171 and +0.538 (bare arm at 4,096); C2b correct abstention 0.667 on
  the paper-level shadow only, which the chunk shadow and the grounded read
  stage took to 0.875 on the same backbone (so the 0.667 → 0.889 step to
  Qwen3.8 was harness, not backbone); C1 0.970; faithfulness a capture
  artifact. All superseded by the 2026-09-17 re-run and kept in the
  scorecard folder.
- **A retired checkpoint is not a lost column.** Bringing Qwen3.6 back as
  an eval-only instance beside production cost one driver run (~26 h of
  instance time, 5 h 47 of it the CPU faithfulness judge) and turned every
  "suggestive" Qwen-vs-Qwen comparison into a controlled one. Decision: the
  eval-instance method is how any backbone gets its column.
- **A new model family costs the harness first.** gpt-oss-20b's first pass
  scored 0.467: the serving stack glued channel tokens to 3% of tool names
  and the scorer did not read its `**Answer:** A` habit. Both fixed at the
  parser boundary without touching any Qwen verdict; pre-repair scorecard
  kept. Decision: one gpt-oss-shaped accommodation beside three Qwen-shaped
  ones, and say so.

---

## Dropped from the paper (lives in scorecards / repo)

The 0.688 load run; the 0.56 / 0.86 pre-architecture arm; the 0.356 interim;
the 100-question pilot; the 07-27 Qwen3.6 files (one sentence each in §6.1);
the 07-10 C1/C2b columns (0.200 → 0.667 is one sentence in §6.1);
verdict-count lists; per-tool reliability tables; the
8-question cron re-run; egress-full C2b decomposition table; the
gpt-oss pre-repair run (one sentence in §6.1); the superseded faithfulness
files (one sentence in §5.2, expanded in §6.1); routing anchor pass rates
beyond the one gpt-oss sentence; the certification-gates table (→ §4 as
protocol, baselines regenerated).

## Open cells

None in the results tables; every cell of every table is on the current
protocol. Optional: C2b at egress = full on Qwen3.8 / gpt-oss (only if the
web-tier sentence in 5.3.2 is to be per-backbone); a small Qwen fourth column
if that run still happens.
