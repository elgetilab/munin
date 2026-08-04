# 06 Ablations

Five ablation families, in one place, including the levers that backfired. The
failed ones are as informative as the successful ones and are reported here
with equal weight.

---

## 1. Harness ablation (Track D): bare vs naive RAG vs agentic

**The design.** Three arms over the same questions:

| Arm | What it is | Implementation |
|---|---|---|
| `bare` | Qwen3.6-35B, no tools, no retrieval | Direct vLLM call, persona prompt minus tool instructions |
| `rag` | Retrieve-then-answer, no agent loop | One BGE top-5 retrieval, contexts pasted into the prompt, single completion |
| `agentic` | The production harness | The live `/api/chat/completions` path |

All arms run at concurrency 1 so wall-clock is a usable cost proxy. Every arm
is exposed as a plain `async def solve(question) -> answer`, which is both good
hygiene and the shape a future InspectAI bridge needs.

### 1.1 Pilot, 2026-07-13, n=100 (superseded, do not cite)

| Arm | Accuracy | Precision | Abstain | Cost |
|---|---|---|---|---|
| RAG | 0.150 | 0.52 | 0.70 | 10 s, 0 tools |
| Bare | 0.320 | 0.48 | 0.25 | 14 s, 0 tools |
| Agentic | 0.560 | 0.86 | 0.31 | 118 s, 16 tools |

Paired deltas (p ≈ 0): agentic − bare +0.240 [0.11, 0.37]; agentic − RAG +0.410
[0.31, 0.51]; RAG − bare −0.170 [−0.26, −0.08].

Kept only for the arm-design rationale and because the naive-RAG-hurts finding
reproduced. Predates the agent-architecture rewrite.

### 1.2 Clean run, 2026-07-27, n=199 (the headline)

Full numbers in `05-RESULTS.md` R1. Summary: agentic **0.839** vs bare 0.302 vs
RAG 0.171; harness value **+0.538 [0.457, 0.618], p < 0.001**.

### 1.3 What the two runs together show

| Quantity | Pilot | Clean run | Change |
|---|---|---|---|
| Harness value (agentic − bare) | +0.240 | **+0.538** | more than doubled |
| Agentic abstention | 0.31 | 0.075 | fell 4x |
| Agentic precision | 0.86 | 0.908 | rose |
| Agentic tool calls / query | 16 | 8.6 | nearly halved |
| Agentic unparseable | n/a | 0 (vs 33 for bare) | accuracy not inflated by lenient parsing |

The arm design did not change between the two runs. The agent architecture and
`source(mode=qa)` full-text reading account for the entire gap. Note the joint
movement: the harness **answers far more and guesses wrong less**, which is not
the usual coverage/precision trade-off, and it does so with **fewer** tool
calls, so the tool-retirement work bought accuracy and cost together.

### 1.4 The load/egress sensitivity companion

`2026-07-26_harness-ablation`, same three arms at higher concurrency with
constrained egress: agentic **0.688** (abstain 0.231, 11.1 calls/query, 183.8 s).
Bare and RAG are **bit-identical** across the two runs, which cleanly isolates
the difference to the agentic arm. This is the ablation of the *measurement
conditions* rather than the system, and it is the reason `10-REPRODUCE.md`
leads with concurrency.

---

## 2. Encoder ablation

Three levels, from cheapest to most expensive, each answering a different
question.

### 2.1 Off-corpus bake-off (SciFact, in-memory)

Apples-to-apples: same `title\n\nabstract` documents, cosine, same metrics.
Git `5f9ef15`, 2026-07-02.

| Encoder | nDCG@10 | Recall@10 | Recall@100 | MRR | Δ nDCG@10 vs SPECTER (p) |
|---|---|---|---|---|---|
| SPECTER-v1 (then production) | 0.479 | 0.637 | 0.840 | 0.441 | n/a |
| SciNCL | 0.564 | 0.723 | 0.908 | 0.530 | +0.085 (~0) |
| E5-large-v2 | 0.722 | 0.844 | 0.963 | 0.692 | +0.243 (~0) |
| **BGE-large-en-v1.5** | **0.746** | **0.873** | 0.948 | 0.716 | **+0.268 (~0)** |

### 2.2 On-corpus bake-off (Munin's own data, shared pool)

Each LitQA2 source paper ranked among a shared pool of 190 real source papers +
5,000 random corpus papers, same pool for every encoder. Git `2955824`,
2026-07-02.

| Metric | SPECTER-v1 | SciNCL | E5-large-v2 | BGE-large |
|---|---|---|---|---|
| Recall@1 | 0.392 | 0.432 | 0.641 | **0.661** |
| Recall@10 | 0.663 | 0.678 | 0.817 | **0.837** |
| MRR | 0.493 | 0.518 | 0.715 | **0.734** |

Significance vs SPECTER-v1 on Recall@10: BGE +0.173 (p ≈ 0), E5 +0.153 (p ≈ 0),
**SciNCL +0.015 (p = 0.70, not significant)**.

### 2.3 Full-corpus validation and production cutover

See `05-RESULTS.md` R4.4 and R5. Recall@10 0.437 → 0.729; end-to-end answer
accuracy +0.075, p = 0.028.

### 2.4 What the encoder ablation establishes

1. **Retrieval-tuning beats scientific pretraining.** The lever is a general
   SOTA retriever (BGE, E5), **not** the scientific SPECTER successor: SciNCL's
   +0.015 is not significant on Munin's own data. This is a directly
   transferable negative result for anyone assuming a domain-pretrained
   embedder is the right default for a scientific corpus.
2. **The gain grows with corpus size.** The full-corpus gain (+0.29 Recall@10)
   is **larger** than the 5k-pool gain (+0.17): with 68k distractors the weaker
   encoder cannot pick the source out of the noise, so a better encoder helps
   *more* at scale, not less. Small-pool bake-offs therefore **understate** the
   production benefit.
3. **The prediction from recall to accuracy was wrong.** A naive
   "accuracy ≈ recall, so 0.73" projection failed: the actual answer gain was
   +0.075, not +0.29. Reporting this explicitly is worthwhile, because
   recall-implies-accuracy is a common and unstated assumption.

### 2.5 A related encoder finding that was measured but not deployed

Production embeds `title\n\nabstract`. The canonical SPECTER format uses the
tokenizer's `[SEP]` token. On SciFact the canonical format is worth about
+1.5 nDCG@10 (0.479 vs 0.494). Applying it requires a full corpus re-embed, and
it was overtaken by the BGE migration, which is worth far more.

---

## 3. Retriever ablation

Five retrievers on two benchmarks. See `05-RESULTS.md` R4.1 and R4.2 for the
full tables.

| Retriever | SciFact (SPECTER era) | LitSearch (BGE era) |
|---|---|---|
| BM25 | **0.652** (best) | 0.378 |
| Dense | 0.479 (SPECTER) | **0.485** (BGE, best) |
| Citation re-rank 0.7/0.3 | 0.479 (inert, no graph) | 0.117 pre-fix / 0.469 post-fix |
| RRF[BM25, dense] | 0.621 | 0.490 (n.s. vs dense) |

**The encoder migration flipped the lexical/dense verdict.** In the SPECTER
era, BM25 beat the dense retriever on BEIR. On LitSearch with the production
BGE encoder, dense beats BM25 decisively (+0.107 nDCG@10, p ≈ 0). This is a
clean demonstration that "BM25 is a strong baseline that dense retrievers
struggle to beat" is an encoder-dependent claim, not a standing fact.

**RRF adds nothing over dense alone once the encoder is good** (+0.005,
p = 0.72). It was the best Recall@100 option in the SPECTER era.

**Citation re-rank is the interesting case and is treated separately in
`07-FINDINGS.md` §5**, because the ablation surfaced a production bug rather
than a property of the signal.

---

## 4. Harness-iteration ablation (three deploy-measured levers)

Each lever was gated on the routing anchor eval (>= 0.950) plus a Track B
faithfulness arm. **Two nulls or negatives and one win**, reported with equal
weight.

| Lever | Type | Result |
|---|---|---|
| Research fragment: `deep` → `medium` default | Prompt | **Backfired.** Paired over-tooling median rose 10.5 → 20.5 calls. A thinner baseline induces *more* compensatory search. Reverted. |
| "at most 3-4 follow-ups" wording | Prompt | **Did not bite.** Over-tooling flat. Kept because it is harmless. |
| **T1a**: `paper_search` abstract excerpts | Code | **Null for grounding** (0.356 → 0.303, CIs overlap) and null for over-tooling. |
| **Over-tooling code cap** (`CHAT_MAX_TOOL_CALLS=30`) | Code | **Worked.** Tool calls per answer: max 43 → 31, p90 40 → 30, tail above 30 calls 9/40 → 2/40. Grounding held (0.331). Zero failures. |

Post-deploy routing anchor: **0.963**, no regression.

**Two evidence-backed lessons:**

1. **Over-tooling is not fixable by prompt.** Two independent prompt
   interventions failed, one of them making the problem twice as bad. The code
   cap fixed it immediately, routing into the existing wrap-up synthesis rather
   than truncating the answer. The mechanism behind the backfire is worth
   stating: a thinner default *increases* the model's felt need to compensate
   with more search.
2. **Grounding is not an evidence-availability problem.** T1a supplied more
   evidence directly in the retrieval result and grounding did not move. This
   null, combined with C1's 0/100 confabulations, is what establishes that the
   un-supported claim fraction is faithful cross-source synthesis plus judge
   literalness, and **not** fabrication. See `07-FINDINGS.md` §4.

Scorecards: `2026-07-09_faithfulness-agentic-live-{t1a,cap}`,
`2026-07-09_t2-postdeploy`, `2026-07-10_postcap-t3`.

---

## 5. The reading-path ablation (a diagnostic, then a deployment)

This is the highest-value ablation in the suite and its structure is worth
imitating: a **diagnostic oracle** first, and only then an engineering effort
sized against the ceiling the oracle established.

**Diagnostic.** On the 20 Track D questions where the agentic arm abstained
despite the source paper being retrievable, **16 of 20 had actually read the
paper** and still abstained. The mechanism: `read_paper` was
summarise-and-discard. Full PDF text was extracted, then compressed to a 3-5
sentence summary plus bullets, and the raw text was thrown away. Buried
numerics (a surface area, a fold-change, a table cell) were compressed out
before the model ever saw them.

**Oracle.** Feeding the raw full text flipped **9 of 11** over-abstentions to
correct, establishing a **0.82 ceiling** for the architecture. One remained
incorrect and one still abstained, so full text also *misleads* about 1 time in
11, which is why the quote-or-abstain contract matters.

**Deployment.** `source(mode=qa)` was built to deliver that oracle in
production: read full text in one LLM call rather than summarising and
discarding.

**Result.** Over-abstention collapsed from roughly 42% to 6%; accuracy went
0.497 → 0.864, **clearing the 0.82 oracle ceiling**.

**Why the design deliberately rejected chunk-and-rank.** The obvious cost
optimisation is to chunk the paper and rank chunks against the question. It was
rejected as the default because the oracle proved 0.82 with *full text*, and
chunk-and-rank introduces a retrieval-miss failure mode the oracle never
tested, so it structurally cannot reach the ceiling it is chasing. Chunking
survives only as an overflow fallback and must justify itself against the
no-chunk baseline on cost.

---

## 6. Measurement-condition ablations

Not ablations of the system, but of the harness measuring it. Each one changed
a headline number and each is a reproducibility lesson.

| Condition | Effect |
|---|---|
| Concurrency 1 vs above `--max-num-seqs` | Agentic accuracy 0.839 vs 0.688 |
| Deadline 300 s vs 900 s | Agentic accuracy 0.814 (11 truncations, all counted wrong) vs 0.864 (0 truncations, 0 unparseable) |
| Answer parser, naive vs hardened | Dropped ~17% of BGE answers and ~10% of SPECTER's as unparseable, turning a real +0.075 (p=0.028) into an apparent +0.05 n.s. |
| Egress off vs full (C2 absent arm) | Accuracy 0.080 vs 0.740 |
| Encoder default not matching production | Silently benchmarked the retired 768d corpus and looked like it worked |

**The stochasticity floor, measured rather than assumed.** Comparing the 300 s
and 900 s runs question by question, **34 of 199 verdicts changed**, and only 11
were the recovered truncations. Six flipped correct → incorrect and six
incorrect → correct purely from temperature-0.7 resampling. The net gain is
real (the CI floor rose 0.759 → 0.819), but **any single-question or
sub-3-point delta on this benchmark is inside the noise floor**. Trust
aggregates, not individual runs.
