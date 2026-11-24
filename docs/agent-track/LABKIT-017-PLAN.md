# Bundles that keep failed runs' files, and a save round that saves: plan

Status: DONE 2026-11-21 ("proceed", Q1-Q3 defaults), deployed (sandbox, retrieval, eval instance), rerun 33/33. Parent: `LABKIT-016-PLAN.md` (Gaussian FWHM
9/9, budget cases 11/12, control 30/33). The eval instance stays up.

## What LABKIT-016 left (checked in the bundles)

| Failure | Runs | Cause |
|---|---|---|
| `reproduces` | `s5_anova#3`, `s5_fit#2` | Run 5 wrote `tukey.csv` (`fit_params.csv`) and then raised. `bundle.py` writes only ok runs into `analysis.py` (`if rec.get("ok")`), so the replay never makes the file: in `s5_anova#3` the next ok run reads it (FileNotFoundError), in `s5_fit#2` it is a selected output that the replay does not remake. The bundle's `code/run_005.py` has the run, so the user can see it, but `analysis.py` does not reproduce what the conversation showed. |
| Save round did not save | `s1_competitive_s8#2` | The save round (round 20) offered only `run_python` with "If the task asks for a file ... write it now"; the model ran one more check of its fit. The wrap-up answer ended "Let me write the results file." with no numbers. 2 of the 3 save rounds in LABKIT-016 did save. |

## Changes

1. **`analysis.py` keeps a failed run that produced files**
   (`backend/sandbox/app/bundle.py`). A failed run that produced at least
   one artifact goes into `analysis.py` too, wrapped so the script goes on
   the way the conversation did:

   ```python
   # --- run 5 (failed), produced: tukey.csv ---
   _munin_seed(5)
   _munin_failed_run(5, '''...the run's code, as a string...''')
   ```

   `_munin_failed_run(n, code)` (written once at the top, like
   `_munin_seed`) runs the code in the script's globals with `exec` and, if
   it raises, prints "run 5 raised <error> in the conversation too;
   continuing" and goes on. The code is embedded with `repr`, so quoting
   and indentation cannot break the script. A failed run that produced
   nothing stays out (as now): its partial state is what the next run
   fixed. Timed-out runs stay out (Q1). The README line "Successful runs,
   in the order they were executed" becomes "Successful runs, and failed
   runs that wrote files, in the order they were executed".
   Tests (sandbox live tests in a standalone container of a test-tagged
   image): a run that writes a CSV and then raises, then an ok run that
   reads it; the bundle's `analysis.py` exits 0 and remakes the CSV byte for
   byte. A failed run with no artifacts is not in `analysis.py`. Replay the
   two LABKIT-016 conversations' code (from their bundles) through the new
   `bundle.py`: both reproduce.
2. **A save round that says what to do** (`chat_service.py`,
   `SAVE_ROUND_MESSAGE`). New text: "This is your last tool call. Do not
   run more checks. If the task asks for a file (for example results.json),
   write it now with run_python from the values you already have; then give
   your answer." (Q2.)
3. **The wrap-up asks for the numbers** (`chat_service.py`, the tools-off
   wrap-up prompt in 5b). After "write your final answer", add: "Give the
   final values you found (each number with its unit); if the task asked for
   a file you did not write, put those values in a JSON block." This is also
   what a user needs when a turn runs out: the numbers, not a promise to
   write them. (Q3.)

## Test, then rerun

Sandbox live tests and labkit tests in a test-tagged image (`docker run`,
not `latest`), retrieval hermetic, a unit test that the save-round and
wrap-up texts carry the new lines.

Targeted rerun, 3 reps, v2 results request: S5 (`s5_anova`, `s5_fit`) and
the competitive families (`s1_competitive_s1`-`s5`, fresh `s6`-`s8`), the
cases where the control failed, 33 runs. Compared with `lk16-control*` and
`r5b-targeted*`. Plus a regrade check: no grader change, so no regrade.

## Rollout

Sandbox image (`bundle.py`) and retrieval (texts), then `instance refresh
eval`. Production gets both: a user's downloaded bundle then remakes every
file the conversation showed, and a turn that runs out still states its
numbers.

## Open questions (defaults taken unless you say otherwise)

| # | Question | Default |
|---|---|---|
| Q1 | Also keep timed-out runs that produced files? | No: a timed-out run can hang the replay for its whole length, and none of the failures was one. |
| Q2 | Make the save round stricter still (refuse a save-round run that does not write a file)? | No: the product cannot tell which file a task wants. Firmer text first; measure. |
| Q3 | Have the wrap-up always ask for the final values, also in non-lab chats? | Only in turns that used the sandbox: a literature answer has no "values", and its wrap-up text stays as it is. |

## What was built

- `bundle.py`: `_failed_with_files(rec)` (raised, not timed out, produced
  artifacts); such a run goes into `analysis.py` as
  `_munin_failed_run(n, <code repr>)` after its `_munin_seed(n)`, with the
  helper written once at the top. labkit vendoring looks at the replayed
  code itself (a repr'd string would hide `import labkit`). README and the
  module docstring say failed runs that wrote files are included.
- `SAVE_ROUND_MESSAGE`: "This is your last tool call. Do not run more
  checks. ..."; `WRAP_UP_VALUES_LINE` appended to the tools-off wrap-up
  prompt only when the turn ran code.
- Tests: sandbox live tests in `munin-sandbox:r7-test` (standalone, no
  network) 67/67, new: a run writes `tukey.csv` and raises, a failed run
  writes nothing, an ok run reads `tukey.csv`; `analysis.py` holds the first
  and not the second, exits 0, reports the replayed error, and remakes
  `tukey.csv` and `summary.csv` byte for byte. `test_repeat_calls` 15/15
  (the wrap-up carries the values line in a code turn, not in a
  calculate-only turn); retrieval hermetic 118/118.
- Replay: the code of `lk16-control/s5_anova#3` and `s5_fit#2` (every run,
  failed ones included, from their bundles) re-executed in the test image;
  the new bundles' `analysis.py` exit 0 and remake `tukey.csv`,
  `groups.png`, `fit_params.csv` and `fit.png` byte for byte (the
  `plot_00N_1.png` files are inline-figure captures, which no script writes
  and the `reproduces` check does not compare).

## Result

Rerun 2026-11-21 on the deployed eval instance (sage-a1.5, git `6851b4e`), the
same cases as the LABKIT-016 control x 3: `lk17-control` 24/24 and
`lk17-control-fresh` 9/9, so 33/33 (`lk16-control*` 30/33, `r5b-targeted*`
33/33). Paired vs LABKIT-016: 3 fail->pass, 0 pass->fail; exact McNemar
p = 0.25, so the count alone does not show the fix; the bundles do.

- **Bundles.** 3 of the 6 S5 bundles hold a replayed failed run. In
  `s5_anova#3` only the failed run 9 wrote `tukey.csv`, in `s5_fit#3` only
  the failed run 4 wrote `fit_params.csv`: the old `bundle.py` would have
  failed `reproduces` in both, the pattern of both LABKIT-016 failures. All
  6 S5 bundles reproduce. In `s5_anova#1` a later ok run rewrites the file, so
  the replay was not needed there.
- **Save round and wrap-up.** Not exercised: mean calls 5.9 (LABKIT-016 7.2,
  round 5 8.3), at most 12, no run near the 20-round limit, no save round in
  the eval log, no calls-not-run lines. The new texts are covered by the unit
  tests only; the next run that reaches the limit is the field test.
- Mean time per run 35 s (LABKIT-016 41 s, round 5 55 s); 3 auto-continues,
  all finished.

## Full-suite regression (2026-11-22)

The whole lab suite after LABKIT-015 to 017, labkit arm, 3 reps, git
`dea4a58` (no code change since `96dc4fe`): 532/555. Scorecards
`2026-11-22_full-lk17{,-fresh}`.

| Set | Now | Before (paired) | fail->pass / pass->fail | p |
|---|---|---|---|---|
| S1-S7 original | 280/285 | `phase4d-labkit` 280/285 | 5 / 5 | 1 |
| S1-S7 fresh | 81/81 | `phase4d-fresh` 80/81 | 1 / 0 | 1 |
| S8 original | 58/63 | `s8-labkit` 49/63 | 14 / 5 | 0.064 |
| S8 `_f1` | 55/63 | `s8-labkit-fresh` 47/63 | 11 / 3 | 0.057 |
| S8 `_f2` | 58/63 | (round-2 families: `s8-r2-targeted-fresh` 14/21, now 20/21) | 6 / 0 | 0.031 |

No regression from LABKIT-015 to 017. The five S1-S7 pass->fail:

- `s3_bars` x2 (a figure height, an unasked title change) and
  `s6_noesy_buildup#1`: both cases have swung before (`s3_bars` 0/3 to 3/3
  over earlier runs, NOESY 1/3 to 3/3). In the NOESY run the analysis keyed
  the pairs wrongly, the repeat guard blocked two reruns, the save round came
  at round 11, and the wrap-up said what it would have produced: no values
  existed to state.
- `s1_nist_misra1d#2`, `s1_nist_thurber#3` (3/3 in every earlier run): run 1
  read the CSV and printed it, every later run retyped the data as array
  literals from that output, and mistyped it (Thurber's y stops near 142,
  the data go to about 1300). The final run retypes the data in 4 to 11 of
  264 S1/S2/S5 bundles in every full run since `phase4-labkit`, and it failed
  `lanczos2`/`lanczos3` there before. An existing habit, not new; a tooling
  fix is a candidate for the next round (a hint when run_python code holds a
  long numeric literal while an uploaded data file exists: read the file).

Save round, first field test: 21 save rounds. 17 wrote results.json, 3 put
the values in the answer, 1 had no values (the NOESY run above); 14 of the 21
runs passed, the others on wrong values, not a missing file. No run ended
with a promise instead of numbers.

S8 failures left (13 of 189): DEER modulation depth and labeling
efficiency (5 runs, values off by up to 100x or by 2x), `short_trace_f1`
distance 3.18 vs 3.05 nm (3/3, the same value each time, so the method, not
the model), `zero_time_f2` t0 176 vs 168 ns (2), CW linewidth or spin-count
values (4), `overmodulation_f2#3` gave the right values as a list but no
results.json or JSON block.
