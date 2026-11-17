# labkit 0.1.3 and tooling, round 4: plan

Status: IMPLEMENTED 2026-11-17 ("proceed", Q1-Q4 defaults), not yet
deployed. Parent: `LAB-ANALYSIS-PLAN.md` (Phase 4 run 3),
`LABKIT-011-PLAN.md`. The eval instance stays up.

What was built, and what turned up on the way:
- labkit 0.1.3: `FieldHelp` attribute errors (`Spectrum`, `BrukerData`,
  `FitResult`); `nmr.read_sparky`, `nmr.read_integrals`, `nmr.noe_buildup`
  (60/60 sigma on the NOESY fixture within the case tolerance; T is bounded
  positive, since unbounded one pair found a lower RSS with T = -0.69 s, a
  growing exponential); the NMR guide runs end to end to shifts and
  relative integrals (on the Bruker fixture: 1.205/3.652/7.261 ppm,
  3.011:2.007:1).
- **A labkit bug found doing that**: `nmr.peaks()` ignored the baseline. On
  the Bruker fixture the baseline sits about 15x the noise above zero, and
  the default threshold returned hundreds of noise peaks, plus the
  truncation ripples beside each strong line. Now baseline + 5x noise
  (robust MAD), a 5x-noise prominence, and 0.5% of the tallest peak
  (`min_rel_height`, as TopSpin's minimum intensity). This, not only the
  attribute typos, is why the Bruker runs struggled to get from a
  processed spectrum to three peaks.
- Divide-by-zero RuntimeWarnings from singular fits are silenced (the
  model saw them as stderr).
- Hints: domain hints (Sparky, NOESY buildup, CD, Kd, relaxation, Bruker)
  fire even when the script imports labkit, unless it calls the labkit
  function itself (run 3's NOESY script defined its own `noe_buildup`; a
  name match would have silenced the hint). The general fit hints keep the
  stop-after-labkit rule; a new `custom_fit` hint covers curve_fit /
  least_squares / lmfit models labkit does not name (the NIST check: labkit
  already passes all 27 problems from both starts, `test_nist`). Sparky
  needs the name, an "N-H" label, a quoted "w1" or an Assignment header:
  bare w1/w2 fired on a relaxation script's warning lists. Upload notes
  name `read_integrals` (TopSpin lists) and `read_sparky` (.list, also in
  a zip).
- Replay on all run-3 scripts: NOESY 3/3 noe_buildup (relaxation and
  exponential no longer fire there), JASCO 3/3 cd, HSQC 5/6 sparky and 1/6
  kd, relaxation 2/3 relaxation, Bruker 2/3; custom_fit on 17 NIST
  problems and 1 competitive run; nothing on S2 readers, S3, S5, traps.
- Rerun guard quotes the exception line of the blocked call.
- `json_outline` in run_python results (`results_outline.py`), for each
  JSON file the run wrote, with a one-line `json_outline_hint` like
  `figure_hint`.
- Tests: labkit 35/35 (sandbox live 54/54, figure hook 20/20); retrieval
  hermetic 117/117 (`test_labkit_hints` 12, `test_repeat_calls` 11, new
  `test_results_outline` 3).

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

## Future of labkit (direction from the user, 2026-11-17)

- **Research agents for fits we do often**: for a recurring analysis, an
  agent researches the method (papers, reference software, conventions),
  writes the labkit function with tests against reference data, and it
  ships after review, the way `noe_buildup` came from the 1H1H-NOESY repo.
- **EPR** is the next domain: CW-EPR (spectral simulation and fitting,
  g and hyperfine tensors, linewidths, multi-component spectra) and DEER /
  PELDOR (background correction, distance distributions by Tikhonov or
  model-based fits, modulation depth, uncertainty). These need special
  fitting and established software as reference (for example EasySpin for
  CW, DeerLab for DEER); plan it as its own round with fixtures and an S6
  extension.

## Result: targeted rerun (2026-11-17, labkit 0.1.3 deployed)

Tags `r4-targeted` and `r4-targeted-fresh` (no scorecard), 3 reps of every
case family that failed in run 3. Original set 60/72 (run 3: 54, baseline:
59 on the same runs); fresh standard curve and competitive 15/18 (run 3:
16). Fixed: JASCO 3/3 (run 3: 1), HSQC 6/6 (5), NanoDrop 9/9 (7), Rat42 and
ENSO 3/3, Bruker 2/3 (1). Hints fired: standard_curve 14, competitive 12,
custom_fit 6, exponential 4, cd 3, relaxation 3, bruker 2, sparky 2, kd 1.
No tracebacks, lock errors or outline failures in the eval log.

**Results shape: the cause is the harness, not the model.** The results
suffix every case gets (`munin_bench/labanalysis/cases.py`,
`RESULTS_SUFFIX`) says "Use plain numbers ..., lists for intervals as
[low, high]". The model follows it against the case prompt: relaxation
"per-residue maps" saved as plain lists of 40 numbers; Gen5 results.v2 was
keyed by well, v3 rewritten as plain lists; standard curves saved
`[value, [low, high]]`. The json_outline showed "(no labels)" and did not
override the explicit instruction. NOESY rep 1 went the other way (exact
sigma values saved as `{sigma, sigma_ci}` objects).

Other failures:
- Competitive (1 original, 2 fresh): the 20-turn budget ran out (one run
  had the numbers in its answer but no results.json).
- Bruker #1: wrote `spec.ppms` in two scripts, then reran the second
  verbatim twice despite the quoted "did you mean 'ppm'?" error; the guard
  forced the wrap-up.
- NOESY #2: read the header line of lipid_groups.txt ("POPC") as a lipid
  group, though the prompt says the first line is the lipid name.
- Lanczos3 2/3: hand-written, local minima (custom_fit/exponential hints
  fired, not followed).

## Result: results-suffix A/B (2026-11-17)

User decision: fix the wording and measure it. Harness `RESULTS_SUFFIXES`
v2 (default since `558bfc4`): a number per key, an object per item
(residue, well, sample, peak, pair), [low, high] only for interval keys; v1
selectable with `--results-suffix v1`, recorded in the scorecard. Same
system (labkit 0.1.3, round-4 retrieval), same 30 cases x 3; v1 arm =
`r4-targeted`, v2 arm = `r4-suffix-v2` / `r4-suffix-v2-fresh`.

| | v2 | v1 |
|---|---|---|
| Original set | 68/72 | 60/72 |
| Fresh (standard, competitive) | 17/18 | 15/18 |
| Paired | 14 fail->pass, 4 pass->fail, exact McNemar p = 0.031 | |

Relaxation 3/3 (v1 0/3), standard curves 24/24 (v1 21/24), Gen5 3/3 (2/3).
So part of every earlier lab scorecard's failures (Phase 2 baseline
included) were harness wording, in both arms alike; run 4 runs on v2 and
its scorecard says so.

Left after v2: Bruker #3 again wrote `spec.ppms` (3/3 Bruker failures in
round 4 used it, and kept it after the "did you mean 'ppm'?" error), so
labkit 0.1.4 adds `Spectrum.ppms` as an alias (labkit tests 36/36);
NOESY #2 sigma off (context budgeted, not looked into); one Lanczos3
local minimum; one ENSO b6/b8 slightly off; one fresh Ki CI.
