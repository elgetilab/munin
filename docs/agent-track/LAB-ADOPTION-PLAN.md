# Lab analysis: getting the model to use `labkit`, plan

Status: PLAN 2026-11-17, waiting for "proceed". Parent: `LAB-ANALYSIS-PLAN.md`
(Phase 4), `LAB-PHASE3-PLAN.md`.

## Why

Phase 4 run 1 was inconclusive: the model imported `labkit` in 16 of 285 runs
(6 of 81 on the fresh set). In the 228 S1 runs it reached for
`scipy.optimize` 199 times (`curve_fit` 125, `least_squares` 110, `lmfit` 25)
and `labkit` 12 times. When it did use `labkit` it passed 15 of 16. The
remaining failures are conventions `labkit` already handles: one-phase decay
reports the span as Y0 (10 runs), Bruker 1D 0/3, S6 relaxation 0/3.

The pointers exist but are weak:
- The `LAB DATA` paragraph in `code.json` sits 60% of the way down a 17k-char
  prompt, phrased as "prefer it".
- The `run_python` description lists `labkit` last of 26 packages.
- Nothing points to it at the moment the model is writing the fit.

## Changes

1. **Persona (`code.json`)**: move `LAB DATA` up next to the core rules and
   make it a rule, not a preference: "For curve fits, standard curves,
   instrument files, NMR and group statistics, use `labkit` first; write your
   own fitting code only for a model `labkit` does not have, and say so." Add
   one 3-line example (`r = labkit.fit("one_phase_decay", df.t, df.signal)`,
   `print(r)` for the parameter table, `r.params["Y0"]`, `r.ci["K"]`), since a concrete call is copied far more often than a
   description.
2. **`run_python` description**: move `labkit` to the front of the package
   sentence, with the same one-line rule.
3. **Hint in the tool result**: when a `run_python` call fits with
   `curve_fit`, `least_squares` or `lmfit` and has not imported `labkit`, the
   result gets one line: "`labkit.fit` has <model> with Prism's parameter
   names and CIs; see `labkit.guide('fit')`." Once per turn, only when the
   code matches a model name or a model formula `labkit` knows (decay,
   association, Hill/4PL/5PL, Michaelis-Menten, Morrison), so general
   optimisation is left alone. The same hint for `nmrglue.bruker` without
   `labkit.nmr`.
4. **Upload note**: when an uploaded file is a format `labkit.read` knows
   (Bruker folder, NanoDrop, Gen5, Jasco, plate exports), the upload note
   names the reader.

## Test, then rerun

- Hermetic: the hint fires on a `curve_fit` decay script, not on a generic
  `minimize` script, at most once per turn (retrieval tests).
- **Usage check** on the eval instance: 20 cases, 1 rep each (the S1
  families, 2 NIST, Bruker, two S2 readers, relaxation, HSQC), about 15 min.
  Go on only if `labkit` is imported in at least 70% of them and the pass
  rate is not below the same runs in the baseline.
- **Run 2**: the full 95 x 3 plus the fresh 27 x 3 (about 3.5 h), scorecards
  `phase4b-labkit` and `phase4b-fresh`, paired against Phase 2 as before,
  with the import rate reported next to the delta.

What the comparison then measures is the system change (`labkit` plus the
nudges), not `labkit` on its own; the scorecard says so.

## Rollout

Personas and retrieval deploy (you run it). The eval instance picks it up on
its restart; production gets the same change, which also helps real users.

## Open questions (defaults taken unless you say otherwise)

| # | Question | Default |
|---|---|---|
| Q1 | Do the tool-result hint (3), or prompts only? | Do it: prompt wording alone is the weakest lever, and the hint fires only on fits `labkit` covers. |
| Q2 | Usage-check threshold | 70% import rate on the 20 cases. Below that, revise once, then report and ask. |
| Q3 | Also the upload note (4)? | Yes, it is small and covers S2 and Bruker. |
