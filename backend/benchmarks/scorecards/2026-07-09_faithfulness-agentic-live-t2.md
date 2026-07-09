# Scorecard — faithfulness + over-tooling (arm: agentic-live-t2, POST-T2)

- judge `MiniCheck-Flan-T5-Large` on cuda:0 | 40 LitQA2 questions | claim_mode=extract
- generator: live research chat with **research persona 1.4 (T2: medium-default
  deep_research + bounded follow-ups)** | 2026-07-09
- paired against `2026-07-09_faithfulness-agentic-live` (pre-T2, research 1.3,
  deep-default), SAME 40 questions.

## Finding: T2's medium-default BACKFIRED on over-tooling

| metric (same 40 q) | pre-T2 (deep default) | post-T2 (medium default) |
|---|---|---|
| retrieval calls/answer, median | **10.5** | **20.5** |
| retrieval calls/answer, mean | 15.9 | 19.3 |
| total tool_calls/answer, median | (not recorded) | 21.0 |
| contexts/answer, mean | 85.6 | 94.9 |
| **% claims supported** | 0.356 [0.272, 0.425] | **0.284 [0.225, 0.351]** |
| mean faithfulness | 0.404 | 0.335 |

Paired: +3.4 mean / +1.5 median more retrieval calls post-T2; 22/40 answers used
MORE calls, 15/40 fewer.

**Mechanism:** a `depth="medium"` deep_research returns a THINNER baseline (3 sub-
questions x 3 variants vs 5 x 4), so the model fires MORE compensatory follow-up
searches to fill the bigger gaps. The "AT MOST 3-4 follow-ups" prompt rule did
not hold. Net: MORE top-level tool calls, MORE contexts, grounding not improved
(CIs overlap; directionally lower). The deep-default gave a richer single
baseline that SUPPRESSED follow-up searching - better on both axes.

Routing anchor eval was neutral (0.950, no regression), consistent with this: T2
did not break routing, it just shifted retrieval breadth the wrong way.

**Caveat:** single paired capture, reps=1, temp=1.0 - suggestive not definitive.
But the median doubling (10.5 -> 20.5) is a large effect on the same questions.

## Conclusion

T2's `deep -> medium` default is counterproductive for its own over-tooling goal.
Reducing per-call depth induces compensatory follow-up searching. The real fix
for the 28-32-call tail (max here 46 pre / 52 post) is a CODE-level trajectory
cap (per-turn / per-conversation tool-call bound), NOT a prompt depth change.
Recommend reverting the medium-default; keep the bounded-follow-up wording
(harmless); build the code backstop as the actual over-tooling fix.
