# Scorecard — faithfulness (arm: agentic-live, claim-extraction refined)

- judge `MiniCheck-Flan-T5-Large` on `cuda:0` (RAGTruth-QA AUROC 0.95) | seed 42
- generator: live research chat | 40 LitQA2 questions | threshold 0.5 |
  **claim_mode=extract** | 2026-07-09
- supersedes `2026-07-08_faithfulness-agentic-live` (claim_mode=sentences) as the
  Track B dashboard baseline.

## Sharpened faithfulness (real claims, not raw sentences)

`extract_claims` drops process narration ("Let me search...", "here's what I
found:"), questions, headers, and sub-5-word fragments (35% of sentences), so the
denominator is checkable factual claims.

| metric | sentences (07-08) | **extract (07-09)** |
|---|---|---|
| claims / answer | 22.6 | **14.6** |
| **% claims supported** | 0.378 [0.314, 0.442] | **0.356 [0.272, 0.425]** |
| mean faithfulness | 0.413 | 0.404 |

**Finding: grounding is ROBUST to claim extraction (0.378 -> 0.356, CIs overlap
heavily).** Removing narration did NOT raise the score — those sentences were
scored supported at a similar rate — so the ~36% grounding is not an artifact of
counting narration; the un-grounded ~64% is genuine un-retrieved synthesis. This
strengthens the interim finding and gives a principled dashboard baseline for the
harness-iteration grounding work (T1). Caveats from the 07-08 card still hold
(single arm; generous context union; % fully-supported is length math). LLM
atomic-claim decomposition remains the higher-rigor option for a final paper
figure; `extract` is deterministic (reproducible dashboard) by design.
