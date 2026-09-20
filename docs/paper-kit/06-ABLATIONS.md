# 06 Ablations

Five ablation families, in one place, including the levers that backfired. The
failed ones are as informative as the successful ones and are reported here
with equal weight.

---

## 1. Harness ablation (Track D): bare vs naive RAG vs agentic

**The design.** Three arms over the same questions:

| Arm | What it is | Implementation |
|---|---|---|
| `bare` | The backbone alone, no tools, no retrieval | Direct vLLM call, persona prompt minus tool instructions |
| `rag` | Retrieve-then-answer, no agent loop | One BGE top-5 retrieval, contexts pasted into the prompt, single completion |
| `agentic` | The production harness | The live `/api/chat/completions` path |

All arms run at concurrency 1 so wall-clock is a usable cost proxy. Every arm
is exposed as a plain `async def solve(question) -> answer`, which is both good
hygiene and the shape a future InspectAI bridge needs.

**Arm matching.** `bare` and `rag` call vLLM directly and bypass the backend,
so anything the backend applies to the agentic arm has to be restated for
them. Since 2026-08-25 all three arms share the model name
(`config.VLLM_MODEL_NAME`), `reasoning_effort=medium` and
`max_tokens=16,384`. Before that the bare arm ran at 4,096 tokens, which
cost the July Qwen3.6 bare arm 33 answers (section 1.4). Sampling is
**not** matched: bare/rag use temperature 0.7, the agentic arm inherits the
research persona's 1.0 / top_p 0.95 / top_k 20 / presence_penalty 1.5. This
predates the swap, was present in every run, and is not controlled for.

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

### 1.2 Clean run, 2026-07-27, n=199, Qwen3.6-35B-A3B (second backbone, July harness)

Full numbers in `05-RESULTS.md` R1. Summary: agentic **0.839** vs bare 0.302 vs
RAG 0.171; harness value **+0.538 [0.457, 0.618], p < 0.001**. The bare arm
in this run was budget-limited (33 of 199 answers truncated at 4,096 tokens
and scored as failures); section 1.2a measures what that was worth.

### 1.2a The same checkpoint on the current harness, 2026-09-17, n=199, Qwen3.6-35B-A3B

The retired checkpoint brought back as an eval-only instance
(`10-REPRODUCE.md` §5a) and run under the protocol of 1.3 and 1.3a: all arms
at 16,384 tokens, 900 s, `egress=full`, concurrency 1, git `556b305`.
Agentic **0.869** vs bare 0.337 vs RAG 0.126; harness value **+0.533 [0.452,
0.613], p < 0.001**; agentic − RAG +0.744; RAG − bare −0.211. Agentic
abstention 0.065, precision of attempted 0.930, 7.3 tool calls and 67 s per
query, 0 unparseable; bare 8 unparseable (6 without a letter, 2 truncated at
16,384). Question-paired against the Qwen3.8 headline arm: −0.005 [−0.050,
+0.040], p = 0.93. This is the Qwen3.6 row to quote; 1.2 is the July harness.

### 1.3 Re-measurement on the production backbone, 2026-08-26, n=199, Qwen3.8-27B (the headline)

Same 199 questions, same arm code, all arms at 16,384 tokens, git `3e0bcfb`.
Agentic **0.874** vs bare 0.387 vs RAG 0.211; harness value **+0.487 [0.407,
0.568], p < 0.001**; agentic − RAG +0.663; RAG − bare **−0.176**.

### 1.3a Third backbone from a different lab, 2026-09-16, n=199, gpt-oss-20b

Same 199 questions, same arm code, all arms at 16,384 tokens, 900 s,
`egress=full`, git `c6c56a7`; run as an eval-only instance beside production
(`10-REPRODUCE.md` §5a). Agentic **0.563** vs bare 0.407 vs RAG 0.101; harness
value **+0.156 [0.075, 0.241], p = 0.004**; agentic − RAG +0.462; RAG − bare
−0.306. Agentic abstention 0.342, precision of attempted 0.896, 8.8 tool calls
and 29 s per query. The bare and RAG arms carry 25 and 162 rows in which the
model ended its turn with **no final message** (see 1.4 and `05-RESULTS.md`
R1); none are truncations.

### 1.4 What the five runs together show

| Quantity | Pilot (Qwen3.6) | Clean run (Qwen3.6, July) | Qwen3.6, current harness | **Headline (Qwen3.8)** | Third backbone (gpt-oss-20b) |
|---|---|---|---|---|---|
| Harness value (agentic − bare) | +0.240 | +0.538 | **+0.533** | **+0.487** | +0.156 |
| agentic − RAG | +0.410 | +0.668 | +0.744 | +0.663 | +0.462 |
| Agentic accuracy | 0.560 | 0.839 | **0.869** | **0.874** | 0.563 |
| Agentic abstention | 0.31 | 0.075 | 0.065 | 0.075 | 0.342 |
| Agentic precision | 0.86 | 0.908 | 0.930 | **0.946** | 0.896 |
| Agentic tool calls / query | 16 | 8.6 | 7.3 | 6.9 | 8.8 |
| Bare accuracy | 0.32 | 0.302 | 0.337 | 0.387 | 0.407 |
| Bare unparseable | n/a | 33 (truncated) | 8 (2 truncated) | 0 | 25 (no final message) |
| Corpus-only harness value (agentic at `egress=off` − bare) | | | +0.367 | +0.276 | +0.075 (n.s.) |

Pilot → clean run: the arm design did not change. The agent architecture and
`source(mode=qa)` full-text reading account for the entire gap. The harness
**answers far more and guesses wrong less**, which is not the usual
coverage/precision trade-off, and it does so with **fewer** tool calls, so the
tool-retirement work bought accuracy and cost together.

Clean run → current harness, same checkpoint: the harness value held
(+0.538 → +0.533) across two months of harness commits and the bare-arm
budget correction. The 4,096-token budget was worth about 0.035 on the bare
arm (0.302 → 0.337, 33 → 8 unparseable), and the agentic arm gained the same
amount (0.839 → 0.869, unpaired: the July per-question array was overwritten),
so the delta did not move. The earlier reading, that the July delta was
inflated by the truncations and +0.487 was the corrected figure, does not
survive: neither figure was inflated.

Current harness → headline, two backbones on one protocol: `agentic` is the
same (0.869 vs 0.874, paired −0.005, p = 0.93; against the 09-16 Qwen3.8
recapture, +0.000), `bare` differs by −0.050 (p = 0.25) and `RAG` by −0.085
(p = 0.002), Qwen3.8 stronger on both. The bare and RAG arms bypass the
harness, so those comparisons are controlled; the agentic one is controlled
against the recapture and one day of harness. So the harness value is larger
on Qwen3.6 (+0.533 vs +0.487) because its floor is lower and its ceiling is
the same. The ordering and the magnitude holding across a dense 27B and a
35B/3B-active MoE is now a controlled finding within the Qwen family, not a
suggestive one; only the July-vs-August comparison (1.2 vs 1.3) keeps the
old caveat, and it is no longer needed for anything.

Headline → third backbone: the lab, the size class (3.6B active vs 27B dense)
and the model's own recommended sampling move at once, so the delta is
attributed to "a different backbone" and nothing finer. What the row
establishes is that the arm ordering, the sign of every paired delta, and the
harness value's significance **replicate outside the Qwen family**, and that
its **size is backbone-dependent**: a third of Qwen3.8's. The mechanism is in
the abstention column. gpt-oss-20b's bare arm matches Qwen3.8's, so the
model is not weaker at the questions; inside the harness it abstains on a
third of them, keeping attempted-answer precision at 0.896. The harness makes
this backbone careful rather than correct. Its bare and RAG arms also show a
failure the Qwen runs never did: without tools the model reasons "we need to
search" and ends the turn with no final message on 25 bare and 162 RAG
prompts, scored as wrong by the frozen protocol. Precision of attempted (0.48
bare, 0.80 RAG) is the fairer read of what it knows; RAG's 0.101 is mostly
refusal by silence rather than the anchoring failure of section 3 in
`07-FINDINGS.md`.

A first pass at this run, before a tool-name repair (vLLM's harmony parser
glued channel tokens to 3% of tool names) and a scorer fix (`**Answer:** A`
was not parsed; changes none of the 398 stored Qwen3.8 verdicts), scored
agentic 0.467 and is kept as `2026-09-16_gpt-oss-20b-prerepair.json`. That
0.10 is what a new model family cost the harness before it cost the model
anything, and it is reported as such.

### 1.5 The load/egress sensitivity companion

`2026-07-26_harness-ablation`, same three arms at higher concurrency with the
web tier degraded (egress on, Brave unfunded; not `egress=off`): agentic
**0.688** (abstain 0.231, 11.1 calls/query, 183.8 s).
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

Each lever was gated on the routing anchor eval (a written `>= 0.950` bar,
read off the scorecard by hand; it is a tool-trajectory pass rate, see R7)
plus a Track B faithfulness arm. **Two nulls or negatives and one win**, reported with equal
weight.

| Lever | Type | Result |
|---|---|---|
| Research fragment: `deep` → `medium` default | Prompt | **Backfired.** Paired over-tooling median rose 10.5 → 20.5 calls. A thinner baseline induces *more* compensatory search. Reverted. |
| "at most 3-4 follow-ups" wording | Prompt | **Did not bite.** Over-tooling flat. Kept because it is harmless. |
| **T1a**: `paper_search` abstract excerpts | Code | **Null for grounding** (0.356 → 0.303, CIs overlap) and null for over-tooling. |
| **Over-tooling code cap** (`CHAT_MAX_TOOL_CALLS=30`) | Code | **Worked.** Tool calls per answer: max 43 → 31, p90 40 → 30, tail above 30 calls 9/40 → 2/40. Grounding held (0.331). Zero failures. |

Post-deploy routing anchor (2026-07-10, after the cap): **0.963** tool-trajectory
pass rate, no item regressed. The later tool consolidation (2026-07-25) took
it to 0.835 on a 17-item set, called "within noise" at the time; there has
been no anchor run since the KNN set changed on 2026-08-27.

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
| Concurrency 1 vs above `--max-num-seqs` | Agentic accuracy 0.839 vs 0.688 (Qwen3.6) |
| Bare arm `max_tokens` 4,096 vs 16,384 | 33 of 199 bare answers truncated and scored wrong vs 8 on the same checkpoint (Qwen3.6, 07-27 vs 09-17); bare accuracy 0.302 vs 0.337. Worth about 0.035 on the bare arm; `agentic − bare` unchanged (+0.538 vs +0.533) because the agentic arm moved by the same amount. |
| Backbone Qwen3.6-35B-A3B vs Qwen3.8-27B, same protocol | Same ordering; agentic equal (0.869 vs 0.874, paired p = 0.93), bare −0.050 (p = 0.25), RAG −0.085 (p = 0.002); harness value +0.533 vs +0.487. Controlled for the bare/RAG arms (they bypass the harness) and against the 09-16 recapture for the agentic arm. |
| Same checkpoint, harness two months apart (Qwen3.6, 07-24/07-27 vs 09-17) | Standalone track 0.864 vs 0.874, paired +0.010 [−0.035, +0.055], p = 0.70, 179 of 199 identical; ablation agentic 0.839 vs 0.869 (unpaired). The harness-drift floor. |
| Run-to-run resampling, same configuration | Two Qwen3.8 bare arms one day apart: 0.422 vs 0.387, i.e. ~0.035 on a 199-question arm at temperature 0.7 |
| Agentic arm `egress=off` vs `full` (three backbones) | Qwen3.8 0.663 vs 0.874, paired +0.211 [0.151, 0.276], corpus-only loop +0.276 over bare; Qwen3.6 0.704 vs 0.869, +0.166 [0.101, 0.226] (same day, egress alone), corpus-only +0.367; gpt-oss-20b 0.482 vs 0.563, +0.080 [0.015, 0.146], corpus-only **+0.075 [−0.010, +0.161], p = 0.10**. A system ablation as much as a measurement one: it is what the harness is worth without the web, and on the third backbone that is not distinguishable from zero on accuracy (`05-RESULTS.md` R1). |
| Deadline 300 s vs 900 s | Agentic accuracy 0.814 (11 truncations, all counted wrong) vs 0.864 (0 truncations, 0 unparseable), Qwen3.6 |
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
