# Failures left after the full-suite run: plan

Status: IMPLEMENTED 2026-11-22 ("proceed", Q1-Q6 defaults), not deployed. Parent: `LABKIT-017-PLAN.md`
(full-suite regression 532/555, no regression from LABKIT-015 to 017). Root
causes from two read-only investigations of the `full-lk17` bundles (scratch
scripts in the session scratchpad, `lk18_deer/` and `lk18_cw/`), key claims
checked by hand.

## What failed and why

labkit gives the right answer on every failing fixture except one
(`short_trace_f1`). Most failures are the model writing its own analysis while
the hints stay silent or point elsewhere.

| Failure | Runs | Cause |
|---|---|---|
| Retyped data | `s1_nist_misra1d#2`, `s1_nist_thurber#3` | The model copies the rows it was shown (the 8-line upload preview, or `head(10)`) into an array literal and invents the rest. Over 2,019 bundles of 10 full runs: 16 bundles hold a literal that departs from the file, 15 right after the rows the model had seen; the 5 where it reached the final run all failed. Faithful copies (43) all passed. No kernel state was lost (state persists; the model just writes standalone scripts). |
| DEER labeling | 5 runs over the 3 sets | labkit's `fit_deer` gives lambda within 0.006 of truth. The model computed `1 - V(end)/V(0)` (background counted as depth), fitted a single cosine, or guessed. `guide('epr')` is CW only and does not point to `guide('deer')`. The guide line `r.lam / 0.5` hard-codes the eval's inversion efficiency. |
| DEER labeling truth | (fixture) | The prompt asks for "the labelling efficiency of the second site" without saying how the first is labelled. The truth lambda / 0.5 holds only if both sites share the efficiency; with site 1 fully labelled it is lambda / (2p - lambda) = 0.25, not 0.40. |
| `short_trace_f1` | 3/3 at 3.18 nm (truth 3.05, atol 0.12) | labkit bias. The r grid stops at the trace-length limit (4.27 nm); the 35% component at 5.5 nm goes into the background and splits the 3.05 nm peak. The extended fit that `check_long` already runs puts it at 3.06 (f1), 3.00, 3.10; on 16 simulated traces its main-peak error stays at or below 0.020 nm (truncated: up to 0.138), single-peak controls unchanged. |
| `membrane#3` | 1 | labkit bug: `check_long` and `check_background` are skipped when `n_boot` is set (`epr.py:1344`, `1360`), so `fit_deer(..., n_boot=10)` gave no membrane warning and the model reported the hom3d mean. |
| DEER by hand (`zero_time_f2` x2, `phase#1`, `bimodal#1`) | 4 | labkit is right on all (t0 167.8 vs 167.6 ns, phase case exact). The DEER hint did not fire: `\bdeer\b` does not match `deer_raw.csv`, and files named `quad.csv` carry no DEER word; off-topic hints fired instead (relaxation from `T2`, Michaelis-Menten from `V_max`, `epr_g` from `mu_B`). |
| CW amplitude mask | `spin_count#1`, `esp_f1#3` | The model masked `abs(y) > K` as "spikes" and removed the lines themselves (449 and 127 points). labkit gives 37.507 (truth 37.5) and 0.3729 (0.372). In the non-DEER S8 bundles an amplitude mask reached the final run in 4, and 3 failed. |
| CW by hand | `broad_window_f1#3`, `complex_f2#2` | Hand integration with an edge baseline (labkit default passes); grid argmax/argmin quantised to the field step (12% of dBpp; labkit passes). Hints fired and were ignored. |
| No results file, clean end | `overmodulation_f2#3`, `deer_labeling_f1#2` | The prompt asked for results.json; the turn ended normally (8 and 4 calls), so neither the save round nor the wrap-up values line applies. About 1 to 2 runs per full run. |
| NOE buildup | `s6_noesy_buildup#1` | labkit API: `nmr.noe_buildup` looks up `diag0[key]` by pair (`'8.6|CH3'`); the prompt and the model key A_ii(0) by group, so a bare `KeyError` repeats until the repeat guard. With the group lookup, 60/60 sigma within 5%. 11 of 19 bundles that used `noe_buildup` had a failed call. |

## Changes

### Retrieval (`chat_service.py`, `labkit_hints.py`)

1. **File-aware retyping hint.** At upload, keep the numeric columns of each
   text data file (up to about 3 MB) for the conversation. On each
   `run_python`, take bracketed numeric literals of 10 or more numbers and
   line them up against those columns. Fire when 5 or more positions match
   and 3 or more then differ by more than 0.2%, or the literal is longer than
   the matched rows: "This array matches rows 1-10 of `y` in thurber.csv and
   then differs; the file has 37 rows. Read the file instead of typing
   values." Replay over the 2,019 bundles: fires in 15 of the 16 departing
   bundles (misses one rounding-only copy that passed), no false-positive
   bundle, and at run 2 in all 5 failures. A literal that matches no column
   (start values, grids, values typed from the prompt) never fires.
2. **Preview says how much it shows.** The upload preview line becomes "the
   first 7 of 37 data rows", and a file that fits in the 1500-character
   preview is shown whole (thurber 703 B, misra1d 227 B).
3. **Named-file nudge.** When the latest user message asks to save, write or
   export a named file (a verb plus a filename with an extension) and the turn
   ends without an artifact of that name, run one save round
   (`SAVE_ROUND_TOOLS`): "You were asked to save 'results.json'; it was not
   written. Write it now from the values you have, then answer." The answer
   already written is kept. This is what a real user who named a file wants;
   it fires once per turn.
4. **DEER hints.**
   - Detection: `deer` without the trailing word boundary, plus `peldor`,
     `dipolar`, `mod_depth`/`modulation depth`, and hand dipolar constants
     (52.04 MHz nm^3 and the like); fire on the first such script that does
     not call `fit_deer`, fit call or not.
   - In DEER context, skip the other domain hints (relaxation, Michaelis-Menten,
     `epr_g`, custom fit), as the CW hints already skip DEER.
   - Hint text adds: "modulation depth is the fitted lambda, never
     1 - V(end)/V(0) (that includes the background); never |V|".
5. **CW hints.** A hint for EPR code that masks by `abs(y) > K` or `< K`:
   "in a derivative spectrum the largest points are the line; a spike is 1 to
   3 points wide; use epr.despike". The lineshape hint adds "extrema on the
   grid are quantised to the field step; use epr.peak_to_peak or fit_line".

### labkit 0.2.2 (sandbox image)

6. `fit_deer`: run `check_long` and `check_background` on the point fit also
   when `n_boot` is set (the bootstrap fits keep them off).
7. `fit_deer`: when `check_long` fires, report the populations below the
   limit and a new `main_nm` from the extended fit, keep the long part flagged
   unreliable, and print "main distance (reliable part)" in `summary()`.
8. `epr.labeling_efficiency(lam, inversion, scheme="both_equal" | "one_full")`,
   which prints the formula it used; the guide recipe uses it with the
   inversion efficiency as a parameter, not `r.lam / 0.5`. `guide('epr')`
   gains one line: "DEER/PELDOR traces: guide('deer')".
9. `epr.despike(B, y, max_width=3)`: local-median outliers at most 3 points
   wide, returns the cleaned trace and the indices it changed.
10. `nmr.noe_buildup`: look up `diag0[key]`, then the parts of the key (a
    tuple, or a string split on `|`, `:`, `-`, `/`), and use a part if exactly
    one matches; otherwise raise "diag0 has no entry for pair '8.6|CH3': key it
    by pair, by the diagonal peak's name, or pass one number".

### Fixture

11. `s8_deer_labeling` (all three sets): the prompt says "both sites are
    labelled with the same efficiency"; the truth stays lambda / 0.5. Only
    the prompt text changes, so the data and earlier runs stay comparable
    except for this wording (noted in the scorecard).

## Test, then rerun

- Unit tests: the retyping detector (thurber and misra1d code fire; a
  faithful copy, p0 arrays, grids and prompt values stay silent) plus its
  replay script over all full-run bundles; the named-file nudge (fires once,
  not on "read results.json", not when the file was written); DEER and CW hint
  replays over the lk17 S8 bundles and a negative control over S1-S7.
- labkit tests: `fit_deer` with `n_boot` warns on the membrane fixture;
  `main_nm` on the three short-trace fixtures and the 16-trace simulation
  sweep, single-peak controls unchanged; `labeling_efficiency` both schemes;
  `despike` keeps a line and removes a 2-point spike; `noe_buildup` with group
  keys, pair keys, an ambiguous key.
- Sandbox live tests in a test-tagged image (`docker run`, not `latest`);
  retrieval hermetic.
- Targeted rerun, 3 reps: the failing families above (NIST thurber, misra1d,
  lanczos2/3, enso, kirby2, mgh17; NOESY; DEER labeling, short trace, zero
  time, phase, bimodal, membrane; CW spin count, ESP, broad window, complex,
  overmodulation) on the original and both fresh sets, plus the round-5
  control. Then a fresh S8 set `_f3` (`s8.VARIANT = 3`, generated after
  labkit 0.2.2 is frozen) for the DEER and CW families, as in LABKIT-015.

## Rollout

Sandbox image (labkit 0.2.2), retrieval, then `instance refresh eval`.
Production gets all of it: none of it is eval-specific.

## Open questions (defaults taken unless you say otherwise)

| # | Question | Default |
|---|---|---|
| Q1 | Labeling fixture: reword the prompt (both sites equal, truth unchanged) or change the truth to "site 1 fully labelled" (0.25)? | Reword: the equal-labelling case is the common one, and the data stay the same. |
| Q2 | Retyping hint: advisory only, or also refuse to run code whose literal departs from an uploaded file? | Advisory: a refusal could block a legitimate edited copy, and the hint alone fires at the first invented run. |
| Q3 | Named-file nudge also for files a user names in earlier messages of the conversation? | No, only the latest user message: an older request may have been dropped on purpose. |
| Q4 | `short_trace`: report `main_nm` from the extended fit by default when `check_long` fires, or only offer it? | Report it, and say so in the summary: on the simulation it is the better estimate and leaves single-peak traces unchanged. |
| Q5 | Add `epr.despike`, or only the hint? | Add it: real spectra do have spikes, and the model needs a correct tool to reach for. |
| Q6 | Fresh `_f3` set for the DEER and CW families after the freeze? | Yes, so the S8 number is not tuned on the sets it was fixed on. |

## What was built

Retrieval:
- `retype_check.py` (new, pure) and its state in `mcp/tools/sandbox.py`
  (`RetypeState`: per conversation, in process, files up to 3 MB, at most 256
  conversations and 4M numbers, least recently used dropped; after a restart
  the hint does not fire). Uploads store their numeric columns (also text files
  inside a .zip); `run_python` results carry a separate `data_hint`, so it
  never displaces a `labkit_hint`; once per (file, column) per user message.
  Replay over 2,019 bundles (14,246 runs): fires in 15 of the 16 departing
  bundles (the miss is a rounding-only copy that passed), 0 false-positive
  bundles, at run 2 in all 5 failures.
- Preview: a file that fits in 1500 characters is shown whole ("37 data rows,
  shown whole"); otherwise "the first 8 lines (the first 7 of 151 data rows)".
- Named-file nudge (`NAMED_FILE_MESSAGE`, `unsaved_named_file`): a present-tense
  save/write/export verb and a file name in the latest user message, the turn
  used the sandbox, no file of that name in this turn's tool results, not the
  last round. The first answer stays the answer: the save round's text is not
  streamed (it becomes the step narration if it calls a tool, otherwise it is
  dropped), no wrap-up follows. The answer is re-sent as a token after the
  save round's tools, because the client moves the shown answer into that
  step's narration; until a reload the work log also shows it under that step
  (a small frontend change would avoid this). Offline over the lk17 bundles
  the requested file is missing in 5 of 549 one-turn runs, including
  `overmodulation_f2#3` and `deer_labeling_f1#2`.
- DEER hints: `_deer_ish` (deer not after a letter, peldor, dipolar,
  mod_depth, modulation depth, 52.0x/326 in a formula); silenced by any
  `fit_deer`; in DEER code the relaxation, `epr_g`, `epr_integral`, Kd, NOE,
  standard-curve and general fit hints are skipped; text adds the lambda
  sentence, `r.main_nm` and `epr.labeling_efficiency`. CW: new `epr_mask` hint
  (amplitude masks, unless `epr.despike` is called), the lineshape hint notes
  grid quantisation. Replay over 189 S8 bundles: 13 of the failing runs now
  get the DEER or mask hint, most at run 1 or 2; hint sets changed in 76
  (only DEER code losing off-topic hints and gaining `deer`, 4 real masks);
  S1-S7 negative control (732 bundles of lk17 and phase4d): 0 changed.

labkit 0.2.2:
- `fit_deer` runs `check_long` and `check_background` also with `n_boot`
  (membrane with `n_boot=3` now warns on all three sets).
- `DeerResult.main_nm`, `reliable_populations`, `long_fraction`: when
  `check_long` fires, a wider fit with its own AIC alpha gives the short part
  ("main distance (reliable part)" in `summary()`); otherwise `main_nm` is the
  largest population. Short-trace fixtures: 3.000 (truth 3.000), f1 3.180 to
  3.060 (3.053), f2 3.120 to 3.100 (3.095). Sweep of 16 traces: largest
  long-trace error 0.138 to 0.071 nm (the 0.071 trace does not trigger
  `check_long`, AIC gain 15.4 under the gate of 20, left as is); single-peak
  controls identical. 27 of 30 S8 DEER fixtures identical in every field.
- `epr.labeling_efficiency(lam, inversion, scheme)` (both_equal f = lam/p,
  one_full f = lam/(2p - lam)), refuses p outside (0, 1].
- `epr.despike(B, y, max_width=3)`: never touched a line over 225 test lines.
- `nmr.noe_buildup`: diag0 by pair, by text, or by one part of the key;
  otherwise `PairKeyError` naming the accepted keying.
- Guide: `guide('epr')` points to `guide('deer')`, despike line; DEER recipe
  uses `main_nm` and `labeling_efficiency` with p as a parameter.

Fixture: `s8_deer_labeling` (and `_f1`, `_f2`) prompt now says both sites are
labelled with the same efficiency; data and truth byte-identical, checksums
updated. Earlier labeling scores were on the ambiguous wording.

Tests: retrieval hermetic 121/121 (new `test_retype_check.py`; hints 21,
repeat calls 20, plumbing 21); labkit 70/70 and sandbox live 67/67, fighook
20/20, isolation 28/28 in `munin-sandbox:r8-test` (host labkit 69/70: nmrglue
is not installed on the host). Fixture checks and replays are scratch scripts
(`lk18_impl/`).
