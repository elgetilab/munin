# Lab analysis: getting the model to use `labkit`, plan

Status: IMPLEMENTED 2026-11-17 ("proceed", Q1-Q3 defaults), not yet
deployed. Changes from the plan, after the user asked whether labkit is
strictly better than scipy (it is not: `labkit.fit` runs
`scipy.optimize.least_squares`; what it adds is Prism's conventions,
multi-start, ambiguity warnings, ROUT and readers that refuse to guess):
- The rule says "labkit for the models and formats it covers, scipy for
  anything else; do not force data into a labkit model", not "labkit
  first".
- The hint is once per conversation and hint kind (not per turn), and stops
  once the conversation's code used labkit.
- The Bruker hint also fires on any code touching `acqus` or a `fid` file:
  in run 1 the model never used nmrglue and read the FID with `np.fromfile`
  as int16 in 3/3 runs.
- Replayed on all Phase 4 run 1 scripts: the hint would have fired in
  127/147 S1 synthetic runs, 40/81 NIST (exponential forms; the hint says to
  keep own code for other models), Bruker 3/3, relaxation 3/3, s5_fit 3/3,
  and 0 of the plotting (S3), ANOVA and HSQC runs.
- Follow-up (not in this change): a bundle whose script imports labkit
  should carry labkit's source or an install note, or "reproducible" means
  "inside Munin" only.

Code: `backend/retrieval/labkit_hints.py` (wired into `run_python` in
`mcp/tools/sandbox.py` and the upload note in `chat_service.py`), the
`LAB DATA` section at the top of the `code.json` fragment, the
`run_python` description. Tests: `tests/test_labkit_hints.py` (7, uses the
model's own run 1 scripts), hermetic 116/116. Parent: `LAB-ANALYSIS-PLAN.md`
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
- **Usage check** on the eval instance: 20 cases, 1 rep each, about 15 min:
  one seed of each S1 family (3pl, 4pl, 5pl, competitive, decay1 x2,
  decay2, mm, morrison, standard), NIST misra1a and rat43, trap_outlier,
  trap_unidentifiable, Bruker 1D, JASCO CD, Gen5, NanoDrop UTF-16,
  relaxation, HSQC titration.
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
