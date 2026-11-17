# labkit 0.1.3 and tooling, round 4: plan

Status: PLAN, waiting for "proceed". Parent: `LAB-ANALYSIS-PLAN.md`
(Phase 4 run 3), `LABKIT-011-PLAN.md`. The eval instance stays up.

Goal: every failure left in run 3 should be solvable with the tools the
model has, and the tools should catch the model's mistakes rather than hide
them. Each item below names the run-3 failure it removes.

## What run 3 left, by cause (checked in the bundles and the eval log)

| Failure | Runs | Cause |
|---|---|---|
| Bruker 1D | 2 | Read and processed with labkit, then guessed `Spectrum` fields (`spec.ppmm`, `spec.ppmin`); the AttributeError does not list the real ones. The model then reran the same call, the verbatim-rerun guard blocked it twice and forced the wrap-up. The block message says "same result as call N" but does not repeat the error. In rep 3 a script then wrote invented shifts to results.json (the answer said so). |
| NOESY buildup | 2 | No labkit model for NOE buildup. The run_python hints misfired: `exponential` (points at one_phase_decay) and `relaxation` (points at relaxation_fit) both fired on NOESY scripts. Rep 2's `least_squares` residual returned the model without subtracting the data, so sigma ran to the lower bound. |
| JASCO CD | 2 | MRE computed by hand with unit errors (off by 1000x and 70x); `labkit.cd.mean_residue_ellipticity` exists but nothing points to it once the file is read. |
| HSQC s2#3 | 1 | Parsed the Sparky lists by hand, swapped to (1H, 15N) for labkit, then indexed its own tuples the wrong way round for the saved CSPs. Kd right. |
| Results shape | 8 | The numbers are right, but they were saved without the labels the prompt asked for: relaxation R1/R2 as bare lists (40 values, no residue numbers), Gen5 rates as a list instead of by well, standard curves as `[value, CI]`, NanoDrop nested one level too deep. A user given an R1 list without residue numbers has the same problem the grader has. |
| NIST, hand-written | 3 | Lanczos3 local minimum, Rat42 off, ENSO no results. |
| Competitive Ki CI | 1 | Hand-written fit, CI too wide. |

## Changes

1. **labkit 0.1.3**
   - Field errors that help: `BrukerData`, `Spectrum`, `FitResult` and the
     other result objects raise `AttributeError: Spectrum has no 'ppmm'; did you
     mean 'ppm'? Fields: ppm, real, phase, ref_offset_ppm, steps`.
   - `guide('nmr')`: the 1D example runs end to end, from `read_bruker` to a
     dict of shifts and relative integrals. It currently stops at
     `process_1d`.
   - `labkit.nmr.noe_buildup(t_s, cross, diag0)`: the model in the user's
     1H1H-NOESY code, `A(t) = A_ii(0)/2 (1 - exp(-2 sigma t)) exp(-t/T)` with
     A(0) = 0. It gives sigma and T per pair with CIs, keyed as you pass them.
     Also `labkit.nmr.read_integrals(path)` for TopSpin integral exports
     (`Object / Integral [abs] / ppm` tables). S6 lists NOESY buildup as a
     workflow, so this is in scope, not test tuning.
   - `labkit.nmr.read_sparky(path)`: returns `{residue: (1H, 15N)}` in the
     order `csp` wants, so nobody swaps w1/w2 by hand.
   - Tests for each item, on the fixtures and on a synthetic case with known
     answers.
2. **Hints** (`labkit_hints.py`)
   - NOE buildup (`NOE`, `NOESY`, `buildup`, `cross-relax`, `mixing`) comes
     first. It points at `noe_buildup` and suppresses the relaxation and
     exponential hints.
   - CD: code that computes ellipticity or MRE by hand points at
     `labkit.cd.mean_residue_ellipticity` (MRW = MW/(N-1)).
   - Sparky: code that parses `w1`/`w2` peak lists points at `read_sparky`.
   - Replayed on all run-3 scripts (each new hint must fire on its target
     and on nothing else), as in round 3.
3. **Rerun guard** (`chat_service._repeated_call_skips`): the block message
   quotes the earlier call's error (its last line, for example the
   AttributeError) and says to fix that line. A rerun blocked after an
   error says that, not "same result".
4. **Results echo** (`run_python` result): when a run writes results.json,
   the result shows its outline: each top-level key and what it holds, for
   example `R1: list of 40 numbers (no labels)` or `initial_rate: list of 4
   numbers`. The model sees what it saved, the same way `figure_summary`
   shows it its figure. It is tooling, not a persona line: it states what is
   in the file and does not tell the model what the file should contain.
5. **Generic fits (NIST)**: first check whether `labkit.fit(callable, p0=...)`
   with multi-start solves all 27 NIST problems from both NIST start values.
   If it does, a softer hint for `curve_fit`/`least_squares` on a model
   labkit does not name: "labkit.fit(f, x, y, p0=...) adds multi-start and
   CIs". If it does not, leave NIST alone and report what fails.

## Test, then rerun

- labkit tests in the sandbox image, plus sandbox live and the figure hook.
  Retrieval hermetic tests, plus new tests for the hints, the rerun guard
  and the results echo.
- **Targeted rerun** on the eval instance, 3 reps, of every case family that
  failed in run 3: Bruker 1D, JASCO, NOESY, HSQC, relaxation, Gen5,
  NanoDrop, standard curve, competitive, and the 3 NIST problems. Compared
  with run 3 on the same cases.
- If that holds, run 4 in full (95 x 3 plus fresh 27 x 3) for the
  scorecard.

## Rollout

Sandbox image rebuild (labkit), then the retrieval deploy, then `instance
refresh eval` (you run them). Personas do not change. Production gets
everything.

## Open questions (defaults taken unless you say otherwise)

| # | Question | Default |
|---|---|---|
| Q1 | Results echo (4): on Q3 you declined a persona line about results shape. Is a factual outline of results.json in the tool result acceptable? | Yes. It describes the file, it is not an instruction, and it helps real users the same way. |
| Q2 | `noe_buildup` and `read_integrals` in labkit (built from your 1H1H-NOESY repo)? | Yes. |
| Q3 | Generic-fit hint (5), only if multi-start solves NIST? | Yes, under that condition. |
| Q4 | Analysis bundles that import labkit carry labkit's source, so they run outside Munin? | Separately, after this round. It does not affect any failure. |
