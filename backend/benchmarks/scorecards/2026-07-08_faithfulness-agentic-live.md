# Scorecard — faithfulness (arm: agentic-live)

- judge `MiniCheck-Flan-T5-Large` on `cuda:0` (validated on RAGTruth-QA:
  AUROC 0.95, see `2026-07-08_faithfulness-judge-ragtruth`) | git `3b9b8b9` | seed 42
- generator: live research chat (`/api/chat/completions`, persona research) |
  40 LitQA2 questions | threshold 0.5 | 2026-07-08

## Answer faithfulness (one arm — interim application / B3 smoke)

| metric | value | 95% CI |
|---|---|---|
| **% claims supported** (macro, length-robust — headline) | **0.378** | [0.314, 0.442] |
| mean faithfulness (mean per-claim support) | 0.413 | [0.367, 0.456] |
| % fully supported (every claim grounded) | 0.000 | length-confounded, see below |

answers 40 (39 scored, 1 empty) | mean 22.6 claims/answer | mean 82.5
contexts/answer | per-answer grounding min 0.00 / median 0.39 / max 0.80.

## Read this number with its caveats (it is a first single-arm smoke, not a verdict)

1. **One arm.** This is the live agentic arm alone. Faithfulness is most
   meaningful as a Track D **paired** comparison (bare vs RAG vs agentic on the
   same questions) — the scorer + record shape are built for exactly that
   (each arm = one capture file, same score path).
2. **Every sentence is scored, incl. non-factual ones.** Answers average 22.6
   "claims" because we sentence-split the whole answer; reasoning, transitions,
   and hedges are not groundable by any passage and deflate the score. A real
   claim-extraction step (vs raw sentence split) should raise it and is the
   obvious refinement before the paper number.
3. **Context union is generous (82.5/answer, up to 372).** We capture the union
   of ALL retrieval tool results, so a claim need only be supported by ANY
   retrieved passage — if anything this INFLATES support. That 0.38 survives a
   generous context set means a substantial share of agentic-answer content is
   genuine synthesis / general-knowledge beyond the retrieved text.
4. **`% fully supported` = 0 is length math, not a finding.** Requiring all ~22
   sentences to clear 0.5 is near-impossible; reported for completeness, never
   the headline. Use `% claims supported`.
5. Judge is validated (B2) on RAGTruth **QA** (our task), AUROC 0.95 at this
   0.5 threshold.

**Takeaway:** the pipeline works end-to-end and yields a stable, CI'd
faithfulness number; ~38% claim-grounding (generously scored) says a lot of
agentic-answer content is un-retrieved synthesis — directly relevant to Track C
(abstention) and Track D (what the harness adds). Refine claim extraction before
treating the absolute value as a paper figure.
