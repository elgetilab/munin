# Bundles that keep failed runs' files, and a save round that saves: plan

Status: IMPLEMENTED 2026-11-21 ("proceed", Q1-Q3 defaults), not deployed. Parent: `LABKIT-016-PLAN.md` (Gaussian FWHM
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
