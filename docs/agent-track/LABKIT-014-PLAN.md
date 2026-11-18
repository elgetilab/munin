# Reproducible randomness, CD cutoff and the (S, I) fit, round 5: plan

Status: PLAN 2026-11-18, waiting for "proceed". Parent: `LAB-ANALYSIS-PLAN.md`
(Phase 4 run 4, 280/285), `LABKIT-013-PLAN.md`. The eval instance stays up.

## What run 4 left (checked in the bundles)

| Failure | Runs | Cause | Fix by tooling? |
|---|---|---|---|
| S5 ANOVA `reproduces` | 3/3 | Strip plot jitter with unseeded `np.random.normal`; `analysis.py` redraws `groups.png` with other points. Run 3 seeded by chance. A real defect: a user's bundle does not reproduce its figure. | Yes: the sandbox seeds the RNGs |
| S2 JASCO CD | 1/3 | `min_wavelength_ht600` interpolated between measured points (194.365 vs 194.5). `labkit.cd.ht_cutoff` does it right; the CD hint names only the MRE function. | Yes: hint and guide |
| Fresh competitive s6#2 | 1/3 | Out of turns: 14 of 20 runs read labkit's source to learn how `fit` takes `(S, I)`. `guide('fit')` has one line on it. | Yes: guide example |
| NIST ENSO | 1/3 | Local minimum in a hand-written 9-parameter fit. | Not this round (see Q3) |

## Changes

1. **Sandbox: seed the RNGs before every run** (`backend/sandbox/app/kernel.py`).
   Before each `execute()`, a silent setup call runs
   `random.seed(n)` and `numpy.random.seed(n)`, with n the run's sequence
   number, so two runs do not draw the same numbers but every run is
   repeatable. `bundle.py` writes a small `_munin_seed(n)` helper at the top
   of `analysis.py` and calls it at the start of each run block with that
   run's original number, so the bundle draws exactly what the sandbox drew.
   Code that seeds itself is unaffected (its own seed comes later).
   Unseeded `np.random.default_rng()` stays random (Q1).
   Tests: sandbox live test that an unseeded jitter plot reproduces from the
   bundle byte for byte; two runs get different draws; the run-4 ANOVA
   bundle's code reproduces under the new `analysis.py`.
2. **CD hint** (`labkit_hints.py`): when CD code also compares HT to a
   voltage by hand (HT/`ht` with 600 or `<=`/`>`), the hint also names
   `labkit.cd.ht_cutoff(wavelength, ht, max_v=600)` (the last measured
   wavelength before HT first exceeds the limit, not interpolated). Fires
   even with labkit imported unless the code calls `ht_cutoff`. `guide('cd')`
   gets the same line. Test on the run-4 JASCO script.
3. **`guide('fit')` example for two-variable models**: a five-line
   `competitive_inhibition` example from a long table (one row per
   substrate/inhibitor pair: `S, I, v` arrays; `labkit.fit("competitive_inhibition",
   (S, I), v)`; `r.params["Ki"]`, `r.ci["Ki"]`), and the parameter names.
   The competitive hint ends with "see labkit.guide('fit')". labkit 0.1.5.
   Test: the guide example runs on the fresh s6 fixture and matches the truth.

## Test, then rerun

labkit tests and sandbox live tests in the sandbox image, retrieval
hermetic. Targeted rerun, 3 reps, v2 suffix: S5 (both cases), S2 JASCO, the
competitive families (original and fresh), 27 runs, about 20 minutes. No
full run 5 unless something else changes.

## Rollout

Sandbox image rebuild (labkit, kernel, bundle) and retrieval deploy (hint),
then `instance refresh eval`. Production gets all of it, including the
seeding: a production user's unseeded randomness becomes repeatable per run.

## Open questions (defaults taken unless you say otherwise)

| # | Question | Default |
|---|---|---|
| Q1 | Also make unseeded `np.random.default_rng()` repeatable (patch it in the sandbox and in `analysis.py`)? | No: patching numpy is invasive and surprising in a downloaded script. The legacy global RNG is what plotting code uses; revisit if a bundle fails on `default_rng`. |
| Q2 | Seed per run (run number) or one constant seed? | Per run: a bootstrap split over two runs should not draw the same numbers twice. |
| Q3 | Multi-start for hand-written NIST fits (ENSO)? | No: the model wrote its own least squares; `labkit.fit` with a callable already does multi-start, and the `custom_fit` hint points there. One run in 81. |
