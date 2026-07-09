# Scorecard — faithfulness (arm: agentic-live-t1a, T1a excerpts)

- judge `MiniCheck-Flan-T5-Large` cuda:0 | 40 LitQA2 q | claim_mode=extract
- generator: live research chat, research **1.5 (deep-default restored) + T1a
  (paper_search abstract excerpts)** | 2026-07-09
- paired vs `2026-07-09_faithfulness-agentic-live` (extract, deep-default, NO
  excerpts), same 40 questions.

## Finding: T1a is a NULL result for grounding

| metric (same 40 q, both extract + deep-default) | baseline | T1a |
|---|---|---|
| **% claims supported** | 0.356 [0.272, 0.425] | 0.303 [0.229, 0.376] |
| mean faithfulness | 0.404 | 0.351 |
| contexts/answer (mean) | 85.6 | 100.0 |
| retrieval calls/answer (median / mean) | 10.5 / 15.9 | 13.5 / 16.9 |

Excerpts ADDED evidence (contexts 85.6 -> 100.0) but grounding did NOT rise
(flat-to-slightly-down, CIs overlap heavily) and over-tooling did NOT drop
(paired call delta +1.0 mean / 0 median; 18 fewer / 18 more of 40). reps=1,
temp=1.0 - noisy, but no signal of improvement.

## What this means (the useful negative result)

The grounding gap is **NOT an evidence-availability problem** - giving the model
more/better retrieved text (T1a's premise) did not move the number. So the ~35%
is driven by something else:

1. **Model synthesis beyond retrieved text** - the un-grounded claims are
   reasoning / cross-source synthesis / general knowledge that no single
   retrieved passage entails. Confirmed direction: adding evidence didn't help.
2. **MiniCheck literalness** - a claim that FAITHFULLY synthesises two passages
   scores "unsupported" because it is not literally entailed by any one chunk.
   So part of the 65% "unsupported" is faithful-but-not-verbatim synthesis, not
   hallucination. MiniCheck measures literal support, not correctness.

Implication: **T1b / T1c (per-item truncation, structured returns) are ALSO
evidence-availability fixes and are unlikely to move grounding either.** The real
levers are (a) prompt/behaviour (already strongly prompted: "every claim tied to
a source, uncited = failures" - diminishing returns), or (b) a correctness-aware
judge that separates faithful synthesis from hallucination (Track B frontier
cross-check / Track C-D territory), not more retrieval plumbing.

## Recommendation

Keep T1a (harmless; the excerpt still helps the model judge relevance and is a
reasonable result feature). But DO NOT invest in T1b/T1c for grounding - the
measurement says evidence availability is not the bottleneck. Redirect harness
effort to the one clearly-fixable problem (over-tooling -> code-level trajectory
cap) and/or move to Track C/D, which measure grounding/abstention with
correctness awareness the literal MiniCheck score cannot.
