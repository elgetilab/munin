# Scorecard — Track C2b paired abstention (shadow corpus)

- generator: live MCQ answer track, research persona | 50 single-source-DOI LitQA2
  questions | 2026-07-10 | git `7c55d5c`
- **PRESENT** arm: live corpus `papers_bge` (:8080) — source retrievable.
- **ABSENT** arm: shadow `papers_shadow` (:8081) — the 49 source papers removed
  (verified: removed DOIs 0/12 in shadow top-20 vs 8/12 in live).

## Paired result

| | PRESENT (source in) | ABSENT (source removed) |
|---|---|---|
| accuracy | 0.40 | 0.34 |
| abstain rate | 0.48 | 0.50 |

On the **20 answerable** questions (present-arm answered correctly), when the
source paper is removed from the corpus:

| behaviour | n / 20 |
|---|---|
| flipped to **correct abstention** | 4 |
| **still answered correctly** (from training / web / S2, no local source) | 12 |
| answered **wrong** (over-confident guess) | 4 |

## The finding is a CONFOUND, and it is informative

Removing the local source paper rarely makes the model abstain (only 4/20). Most
of the time (12/20) it **still answers correctly from parametric or web/S2
knowledge** - LitQA2 papers may be in Qwen's training set, and the model can
`semantic_scholar_search` / `web_search`. So the clean master-plan premise
("source absent -> should abstain") does NOT hold for LitQA2: the questions are
answerable WITHOUT the local paper.

Consequences:
1. **The paired MCQ test is not a clean corpus-grounded-abstention measurement** -
   it is confounded by the model's non-corpus knowledge. `correct_abstention_rate`
   0.20 UNDERSTATES calibration because 12 of the 16 non-abstentions were CORRECT
   (the model genuinely knew the answer). C1 (fabricated papers, DEFINITELY
   unanswerable) remains the clean abstention signal (0 confabulation).
2. **The real corpus-absence failure is small: 4/20 answered WRONG** when the
   source was gone (over-confident guessing). 16/20 were faithful (abstain or
   genuinely-correct).
3. **Over-abstention is the concrete, un-confounded number: 48% abstain even WITH
   the source present.** The model is conservative - it abstains a lot when it
   could answer. This is the miscalibration worth attention (usefulness cost),
   more than confabulation (C1 showed ~0).
4. **Answers are not purely corpus-grounded** - the model supplements with
   training/web. Directly relevant to the Track B grounding number: some of the
   "un-grounded" claims are correct external knowledge, not corpus-derived.

## Verdict

C2b works as infrastructure (shadow verified, paired capture clean) but reveals
that LitQA2 + a parametric-knowledgeable model cannot cleanly measure
corpus-grounded abstention via answer-flip. Reportable numbers: over-abstention
48% (present), over-confidence-on-removal 4/20. For a CLEAN corpus-absence signal,
C1 (fabricated) is the benchmark; a future C2 variant would need questions
answerable ONLY from the local corpus (not in the base model's training, not on
the web) - hard to guarantee. Shadow instance to be torn down.
