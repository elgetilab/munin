# Reproducible randomness, CD cutoff and the (S, I) fit, round 5: plan

Status: IMPLEMENTED and DEPLOYED 2026-11-19 ("proceed", Q1-Q3 defaults);
targeted rerun 33/33 (run 4: 28/33). Parent: `LAB-ANALYSIS-PLAN.md` (Phase 4 run 4, 280/285),
`LABKIT-013-PLAN.md`. The eval instance stays up.

What was built:
- Sandbox: `kernel.seed_code(n)` runs silently before every run (seeds
  `random` and `numpy.random` with the run number, leaves no names in the
  user's namespace); the exec record keeps `seed`; `bundle.py` writes
  `_munin_seed(n)` into `analysis.py` before each seeded run (older,
  unseeded runs get none). Live tests in a standalone container of the new
  image: 58/58 (new: two runs draw differently, code that seeds itself keeps
  its seed, unseeded `jitter.csv` and `jitter.png` reproduce byte for byte);
  the current production image fails the two reproduction checks (56/58), so
  the test catches the defect. Isolation 28/28, figure hook 20/20. The run-4
  ANOVA conversation (`s5_anova#1`, all ten runs incl. the failed ones)
  replayed through the new sandbox: its bundle's `analysis.py` redraws
  `groups.png` byte for byte.
- Hints: new domain hint `ht_cutoff` (an HT/HV limit compared by hand).
  Domain hints now fall through: if the first matching one was already
  given, the next one fires (the run-4 JASCO script matched the CD hint,
  given earlier, so the HT one could never have reached it). General
  "labkit.fit has this" hints keep the old rule (only the first counts, no
  fall-through to `custom_fit`). The competitive hint says `x=(S, I)`, one
  entry per measurement, and that one fit is the global fit.
  `test_labkit_hints` 15/15 (3 new, on the run-4 JASCO script).
- labkit 0.1.5: `guide('fit')` has a worked `competitive_inhibition`
  example from a long table; `guide('cd')` says `ht_cutoff` returns a
  measured wavelength, not an interpolated one. New test runs the guide
  example exactly as printed. labkit 38/38 in the new image (NIST mounted).
- Retrieval hermetic 118/118 (`backend/scripts/ci/run_retrieval_tests.sh`).

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

## Result: targeted rerun (2026-11-19, labkit 0.1.5 and seeding deployed)

Tags `r5b-targeted` and `r5b-targeted-fresh` (no scorecard), 3 reps, v2
results request, compared with run 4 on the same cases: **33/33** (run 4:
28/33).

| Cases | Round 5 | Run 4 |
|---|---|---|
| S5 ANOVA | 3/3 | 0/3 |
| S5 fit | 3/3 | 3/3 |
| S2 JASCO CD | 3/3 | 2/3 |
| S1 competitive s1-s5 | 15/15 | 15/15 |
| Fresh competitive s6-s8 | 9/9 | 8/9 |

- ANOVA: two of the three runs jittered with unseeded `np.random` again and
  reproduced only because of the per-run seeding (`_munin_seed` in
  `analysis.py`); the third seeded itself.
- JASCO: 2/3 runs called `labkit.cd.ht_cutoff` (run 4: 0/3); the
  `ht_cutoff` hint fired once, after the CD hint (fall-through).
- Competitive: mean 8.9 tool calls per run on the original set (one run
  still used all 20 and passed), 6.8 on the fresh set (max 11).
- Hints fired (eval log): competitive 10, cd 3, ht_cutoff 1, custom_fit 1.

A first attempt (`r5-targeted`, 0/33) is void: the eval vLLM job had hit
SLURM's 2-day limit in `standard` before the deploy, so every run ended
"vLLM unreachable". Eval instances now run in `gputraining`, 14 days by
default, with `instance up --time` and `instance extend` (`cb57923`).
