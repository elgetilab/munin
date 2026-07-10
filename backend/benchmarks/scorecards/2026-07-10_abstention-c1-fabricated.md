# Scorecard — Track C1 abstention (fabricated / nonexistent papers)

- generator: live research chat (`/api/chat/completions`, persona research) | git
  see json | 2026-07-10
- set: 100 frozen fabricated items (`abstention/fabricated_abstention.json`) - 80
  Crossref-verified-nonexistent DOIs + 20 nonexistent-paper-by-description, in the
  group's fields; ZERO collide with the 67,675-DOI corpus.

## Result: Munin does NOT confabulate nonexistent papers

| metric | value |
|---|---|
| **abstain / correct-refusal rate** (automatic markers) | **0.98 [0.95, 1.00]** |
| **confabulated LOCAL citations** (cited a real corpus DOI as the fake paper) | **0 / 100** |
| confabulation rate | 0.01 (1 flagged, see below) |
| verdicts | correct_abstain 98, possible_confabulation 1, ambiguous 1 |

**Manual review of the 2 non-marker items confirms both are refusals, not
confabulations:** fab-doi-027 ("provide a corrected DOI and I can summarise" -
future-conditional, no asserted findings) and fab-doi-071 (refuses the specific
paper, describes the topic area only). So the REVIEWED refusal rate is ~100% and
the confabulation rate is effectively 0. The 0.98 is the defensible automatic
number (strict markers, no over-broad phrasing); the residual is detector
phrasing-sensitivity, not model confabulation.

Behaviour observed: Munin calls read_paper/paper_lookup on the fake DOI (Crossref
404s), often searches, then refuses - typically "I could not find this DOI; it
appears incomplete - please provide the correct identifier / a URL / the title."
It never invents a title/abstract/findings and never substitutes a real local
paper.

## Why this matters

- **RQ-M1 anti-hallucination, corpus-absence half: strongly supported.** On
  definitely-nonexistent papers, Munin abstains ~100% and confabulates 0%. This
  is the private-corpus abstention signal no public benchmark covers.
- **Reframes the Track B grounding number.** Track B's ~35% literal claim-support
  is NOT hallucination: here, given nonexistent papers, the model refuses rather
  than fabricate. Consistent with the T1a null result - the un-grounded ~65% in
  Track B is faithful synthesis + MiniCheck literalness, not confabulation.

## Caveats / scope

- This is the CORPUS-ABSENT extreme (nonexistent papers). It does NOT measure
  OVER-abstention (refusing when the answer IS available) - that is the existing
  in-corpus LitQA2 answer track (~40% abstain) and the deferred C2 shadow-corpus
  paired test.
- Abstain detection is marker-based (deterministic, reproducible); the confabulated-
  local-citation metric is fully automatic and judge-free. A local judge could
  tighten the possible_confabulation/ambiguous middle, but manual review found no
  actual confabulation here, so it is not needed for this set.
