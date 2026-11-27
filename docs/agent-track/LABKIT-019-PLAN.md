# LABKIT-019: EasySpin slice 1, round 2 (plan)

Status: BUILT 2026-11-24 ("proceed", defaults Q1-Q4), not deployed. MEASURED on a test arm 2026-11-26/27: 13/15 on the five new cases (es1-labkit 5/15, p = 0.021), no S8 regression, fresh 11/15; see "Result (test arm, 2026-11-26)". Parent: `EASYSPIN-PORT-PLAN.md`
(slice 1 deployed 2026-11-23, labkit 0.3.0).

## Result that prompts this round

`es1-labkit` (deployed labkit 0.3.0, the five new S8 cases x 3, `eval`
instance): 5/15, against the `es1-baseline` 3/15 (1/9 on the regenerated
files).

| Case | Pass | What went wrong |
|---|---|---|
| Cu(II) frozen | 2/3 | #2 started A_par at 120 MHz with a +-100 range; the fit ended at its bound (160 MHz) |
| units | 3/3 | |
| rigid nitroxide | 0/3 | #1 and #3: `fit_spectrum` ended Azz at its bound; the model then guessed a "typical" 1.4 or 1.5 mT. #2 never fitted; 2.70 mT is the isotropic-looking splitting |
| rhombic heme | 0/3 | `fit_spectrum` never called; g read from absorption peaks and "typical heme values" |
| pyrazine radical | 0/3 | #2 fitted, aH ended at its bound (0.3); #3 hand-measured; #1 quoted literature values |

Every recipe in `guide('epr-sim')` gives the right answer on these files
when it is run as printed, so the physics holds. The failures are in how the
model reaches it.

1. **The wrong hint fires.** On the rhombic, radical and nitroxide #2 runs,
   the hint that fired is `epr_lineshape` or `epr_g`. Those point to
   `fit_line` and `nitroxide_hyperfine`, the tools for isotropic single
   lines. The new `epr_powder` hint needs a hand-written orientation grid
   (theta, phi), which no run wrote. `epr_iso_fit` lost to `epr_lineshape`,
   which comes earlier in the order.
2. **Fits end at their bounds, and the model then invents a value.** Four
   runs got the "ended at its bound: widen its range" warning and reported
   a bound or a literature number instead of refitting.
3. **The API trips on first use.** Errors seen:
   - `SpinSystem(A_MHz=...)`, where A_MHz belongs on a `Nucleus`;
   - `vary` shapes (one entry per nucleus, half-ranges);
   - `fit_spectrum` without `vary`;
   - `EPRData.field` and `.signal`, 4 runs; the real names are `field_mT`
     and `y`.

## Changes

labkit 0.3.1:
- **Bounds that widen.** When a varied parameter ends within 2% of its
  range edge, `fit_spectrum` itself moves the range (centred on the
  current value, the same width, then twice the width) and refits, up to
  two times within `max_time_s`. The result says so in a note. The
  warning stays only if the parameter is still at an edge after that.
- **`vary` is optional.** Default ranges come from the start values: g
  +-0.05 (axial or rhombic per component), A +-50% or 50 MHz (whichever is
  larger), fwhm +-80%, strains 0 to 0.02. `vary="all"` is the same.
- **Wider `vary` shapes.** It also accepts a flat list for a single nucleus
  (`"A_MHz": [30, 100]`) and a scalar for every component.
- **Clearer errors.** A misplaced keyword says where it belongs:
  `SpinSystem(A_MHz=...)` says "hyperfine goes on a Nucleus:
  nuclei=[epr.Nucleus('63Cu', A_MHz=[...])]".
- **Aliases on `EPRData`**: `field` for `field_mT`, `signal` and `intensity`
  for `y` (read-only; `ppms` in nmr is the precedent).
- **Powder checks in the line tools.**
  - `nitroxide_hyperfine` and `fit_line` check whether the spectrum looks
    like a powder pattern: outer extrema more than 4 mT apart for a
    nitroxide, or more than 3 turning points over more than 20 mT.
  - When it does, they add a warning: "this looks like a frozen/powder
    spectrum: aN here is not Azz and peaks are not g-values; fit it with
    epr.fit_spectrum(model='powder'), see guide('epr-sim')".
- **Start estimates in the guide.** `guide('epr-sim')` says how to estimate
  each start from the spectrum, so the fit starts in the right basin:
  - Cu A_par from the spacing of the low-field parallel lines;
  - nitroxide Azz as half the outer extrema splitting;
  - rhombic g at the three turning points.

Retrieval hints:
- **New `epr_sim_context` hint, checked before `epr_lineshape` and
  `epr_g`.** It fires when the code is CW-EPR and any of these appears:
  - words: frozen, 77 K, powder, immobili(s|z)ed, rigid, rhombic,
    principal, g_par/g_perp/g_max/g_mid/g_min, Azz, A_par;
  - two or more nuclei, or a hyperfine pattern with aN and aH.
  It names the fitting recipe for that context. While it applies,
  `epr_lineshape` and `epr_g` do not fire.
- **`epr_powder` no longer needs an orientation grid.**
- **New `epr_bound_guess` hint.** It fires when code writes a number into
  results right after a `fit_spectrum` call whose output had "ended at its
  bound". It says to refit from the estimated start, not to report a
  typical or literature value.
  - Open: is this second one worth it? It needs the previous tool output,
    which the hint code sees for the save round only. Default: build it
    only if the plumbing is already there, otherwise drop it.

Persona: one sentence in the LAB DATA rule. "For frozen, powder or
multi-nucleus spectra use epr.fit_spectrum; never report a typical or
literature value in place of a fitted one."

## Validation

- Unit tests:
  - bound widening on synthetic Cu with A_par started at 120 MHz;
  - nitroxide Azz started at 1.5 mT;
  - the radical with aH started at 0.3 mT.
  Each must recover the truth within the S8 tolerances without the caller
  changing `vary`.
- Replay the 10 failing `es1-labkit` bundles' fit calls against 0.3.1:
  - the Cu #2, nitroxide #1/#3 and radical #2 calls must now land on the
    truth;
  - hints are checked on the stored code with `fit_hint` (rhombic, radical,
    nitroxide #2 must get `epr_sim_context`).
- Test image `munin-sandbox:r10-test`: labkit, sandbox live, isolation,
  fighook. Retrieval hermetic tier.
- After deploy:
  - `es1-labkit` cases x 3 (tag `es2-labkit`), paired against `es1-labkit`;
  - the 21 older S8 cases x 3 as a regression check;
  - fresh `_f1` variants of the five new cases, generated after the freeze.

## Open questions (defaults taken unless you say otherwise)

| # | Question | Default |
|---|---|---|
| Q1 | Should widening be automatic, or only a stronger warning? | Automatic, with a note; the model ignored the warning in 4 of 4 runs |
| Q2 | `vary` defaults when omitted | Yes, as above |
| Q3 | `EPRData` aliases (`field`, `signal`, `intensity`) | Yes |
| Q4 | The `epr_bound_guess` hint | Only if the plumbing exists |

## What was built (2026-11-24)

The other worktree is changing the harness at the same time, so a rerun
after both deploys measures the two together. To separate them, deploy this
round first and run `es2-labkit` before the harness change lands, or say
which order you want.

labkit 0.3.1:
- **Ranges that move.** A parameter within 2% of an edge gets a new range
  and is refit, at most twice. Plan change: the new range reaches past the
  edge (half the old width behind the value, twice the old full width
  ahead, four times in round 2). "Centred, same width" stopped Cu #2 at 520
  MHz.
- **Spectrum-based starts.** New `epr.powder_starts(field_mT, y, freq_GHz,
  kind='cu'|'nitroxide'|'rhombic', modamp_mT=)`.
  - Cu: g_perp is scanned near the strong feature; g_par and A_par come
    from a grid of quick simulations scored on the parallel region. It
    never raises, and `.how` says when the parallel lines were not
    resolved.
  - `fit_spectrum` adds this estimate as a start for axial Cu and 14N
    nitroxide starts. Moving ranges alone left Cu in a wrong minimum
    inside the range.
- **`vary` optional**, with more shapes accepted. Misplaced keywords say
  where they belong.
- **Aliases:** `EPRData.field`, `.signal`, `.intensity`.
- **Powder checks** in `nitroxide_hyperfine` and `fit_line`. All 72 S8 CW
  inputs were checked: no isotropic one triggers; Cu, rigid nitroxide and
  rhombic do.
- **Faster isotropic fits:** 24-point modulation quadrature when the
  modulation is under half the linewidth.
- **Fix:** the 0.3.0 cheap-stage deadline was ignored.

Retrieval:
- **`epr_sim_context` hint.** It fires when the code is CW-EPR, the user's
  question says frozen, powder, rhombic, principal g, Cu(II), or names
  14N and 1H, and the code measures lines by hand or with the isotropic
  tools.
  - Plan change: the question, not the code, decides. Code words are the
    model's assumptions; an ESP single-line run wrote "Cu(II)".
  - The user text is read from the conversation (last 4 user messages,
    2 s timeout), and only for CW-EPR code.
  - Zero-crossing and peak-to-peak questions, DEER and fluid nitroxides
    are excluded.
  - While it applies, `epr_lineshape`, `epr_g`, `epr_powder` and
    `epr_iso_fit` stand down.
- **`epr_bound` hint**, on the run's output. When `fit_spectrum` prints
  "ended at its bound", the run gets a hint once per conversation: refit,
  never report the bound or a typical value.
- **Persona:** one sentence in LAB DATA (fit frozen, powder and
  multi-nucleus spectra with `fit_spectrum`; never a typical value).

Checks:
- **Hint replay** over stored bundles, with the case prompts as context:
  `epr_sim_context` fires on all 12 runs of the four failing case types
  and on none of the stored runs of the 21 older S8 cases (full-lk17,
  lk18, fresh).
- **Replayed fit calls** from the failing es1 bundles: nitroxide #1, #3
  (twice) and radical #2 land on the truth; Cu #2 (63Cu only, no strain)
  does not. The residual warning now says to add g strain, and the hint
  says to use 'Cu' and g_strain.
- **Guide recipes as printed, held-out eval files, 2 CPUs:**
  - Cu g_par 2.2519, g_perp 2.054, A_par 551.7 MHz (12.7 s);
  - nitroxide Azz 3.519 (8.1 s);
  - rhombic 2.418 / 2.253 / 1.914 (8.8 s);
  - radical 0.721 / 0.264 (2.8 s).
- **20 Cu draws** (eval family and wider): 20/20, worst g 0.0002, A_par
  0.06%. 8 of the 20 hit `max_time_s` in refinement (values already
  right, with a "stopped at max_time_s" warning).
- **Tests:**
  - `munin-sandbox:r10-test`: labkit 111 passed, 3 xfailed; sandbox live
    67/67; isolation 28/28; fighook 20 passed.
  - Retrieval hermetic 122/122. Hint tests 23/23. Benchmarks 261 passed.

## Result (test arm, 2026-11-26)

These numbers come from a test arm, not the deployed instance.

- Sandbox `munin-sandbox-lk019` (image `munin-sandbox:r11-test`, labkit 0.3.1).
- Retrieval `munin-retrieval-lk019` (image `munin-retrieval:lk019-test`, the merged tree at 7defc57), env cloned from the `eval` instance.
- Source ledger and tool-result cutter OFF (`MUNIN_SOURCE_LEDGER=off`, `MUNIN_TOOL_RESULT_CUT=drop`), so the comparison with `es1-labkit` (run 2026-11-23, before the ledger existed) changes only this round.
- Same vLLM and model (`sage-a1.5`), concurrency 4, 3 reps.

Production now has the ledger and the cutter on by default (4413992). A rerun on the deployed instance may differ.

Pairing is by rep number (#1 with #1). Reps are independent runs, so this is a convention. The test is the exact McNemar on discordant pairs (`mcnemar_p` in `munin_bench/userfail/run.py`), as in earlier lab scorecards.

### Numbers

| Run | Cases | Pass | Paired against | Gained / lost | p |
|---|---|---|---|---|---|
| `es2-labkit` | five new S8 cases x 3 | 13/15 | `es1-labkit` 5/15 | 9 / 1 | 0.021 |
| `es2-labkit` | same | 13/15 | `es1-baseline(-regen)` 4/15 | 10 / 1 | 0.012 |
| `es2-regress` | 21 older S8 cases x 3 | 61/63 | latest deployed run per case 62/63 | 1 / 2 | 1.0 |
| `es2-regress` | same | 61/63 | `full-lk17` 58/63 | 5 / 2 | 0.45 |
| `es2-fresh` | `_f1` of the five new cases x 3 | 11/15 | `es2-labkit` 13/15 | 2 / 4 | 0.69 |
| `es2-fresh` | same | 11/15 | `es1-labkit` 5/15 | 8 / 2 | 0.11 |

"Latest deployed run per case" is `lk18-targeted` for its 11 cases and `full-lk17` for the other 10.

Per case on the new cases:

| Case | es1-labkit | es2-labkit | es2-fresh (`_f1`) |
|---|---|---|---|
| Cu(II) frozen | 2/3 | 3/3 | 3/3 |
| rigid nitroxide | 0/3 | 3/3 | 3/3 |
| rhombic heme | 0/3 | 3/3 | 3/3 |
| pyrazine radical | 0/3 | 2/3 | 1/3 |
| units | 3/3 | 2/3 | 1/3 |

Regression: no older case dropped by 2 or more of 3. `broad_window` and `complex` went from 3/3 to 2/3, and `deer_labeling` went from 2/3 to 3/3.

The three powder cases are solved on both the original and the fresh files (18/18). The radical fit and the units conversion are not (6/12 over both files). Units was 3/3 in `es1-labkit` and 3/3 in `es1-baseline`. Neither its code path nor its hints changed in this round. It is now 3/6 over both files, which looks like model variance on the g_e trap, but it is worth watching.

### The `epr_sim_context` hint mostly did not fire

The retrieval log (`labkit_hint kind=...` lines) and the hint text in the recorded tool results agree:

- `epr_sim_context` fired on the 6 rigid nitroxide runs only.
- On the Cu, rhombic and radical runs, the old line hints (`epr_lineshape`, `epr_g`, `epr_integral`, `epr_iso_fit`) fired instead, or no hint at all (es2-labkit Cu#2, Cu#3, rhombic#3).
- A replay of the stored code with the question as context (`fit_hint` with `context=` the recorded prompt) gives `epr_sim_context` on 14 of those 18 runs (not on es2-labkit Cu#2, Cu#3, rhombic#3 or `_f1` Cu#1) and 0 times in production.

So the hint code is right on the question text, but in production it did not see the question when the upload was a BES3T pair (`.DSC` + `.DTA`). The single `.txt` upload (nitroxide) worked.

- The cause is not known. The eval deletes its chats, so the stored user message cannot be read back.
- Candidates: what `_user_text` gets back from `chat_store` for a message with two data-file blocks, or the `content` type of that row.
- No "hint context skipped" line was logged, so it was not a timeout or an exception.

The powder cases passed anyway. The gain therefore comes from labkit 0.3.1 (`powder_starts`, ranges that move, the aliases, the powder warnings in the line tools) and from the persona sentence, not from this hint. `epr_bound` fired once (Cu `_f1`#2, which passed).

### Failing runs

| Run | Result vs truth | Hints that fired (log) | `fit_spectrum` | Ended at bound | `powder_starts` | Typical/literature value | API errors | max_time_s / timeout | What went wrong |
|---|---|---|---|---|---|---|---|---|---|
| es2-labkit radical#3 | aN 0.47 / aH 0.28 vs 0.721 / 0.264 | labkit_source, epr_lineshape | no | n/a | no | yes ("consistent with literature values") | none | none | Peaks with `find_peaks`, results.json saved, then one `simulate_iso` check. `epr_sim_context` should have fired on run 6 |
| es2-labkit units#3 | 79.87 vs 83.77 MHz | epr_lineshape | no | n/a | no | no | none | none | Converted with g_e (28.025 MHz/mT), the case's trap. It computed g from the doublet centre with the wrong constant (g = 1.05) and did not notice |
| es2-regress complex#1 | fwhm 13.95 vs 0.1455 mT (dBpp passed) | epr_lineshape | no | n/a | no | no | 3 failed runs (attribute probing) | none | Hand Lorentzian-derivative `least_squares` fit with no amplitude, which diverged. results.json took that value, while the answer text said 0.149 mT (which would pass). No hint on the fit run: the lineshape hint was already given, and general fit hints stop once labkit is used |
| es2-regress broad_window#2 | ratio 103.3 vs 2.4 | epr_lineshape, epr_integral, epr_mask | no | n/a | no | no | 2 failed runs | none (771 s) | Took the narrow reference line (+-774) for a spike and zeroed it by amplitude, even after `epr_mask` fired and `despike` removed 0 points |
| es2-fresh radical_f1#1 | aN 1.343 / aH 0.339 vs 0.687 / 0.298 | labkit_source, epr_lineshape, epr_integral | yes (4 calls, 2 failed on `g=[2.0]` / `A_mT=[1.25]` shape errors) | no | n/a (no iso kind) | no | 2 shape errors | stopped at max_time_s=20 s (2 of 8 starts); a 60 s retry timed out; used all 20 calls | Started aN at 1.25 mT from peak spacings. The fit settled near 2x the true aN. The host replay of the 60 s call ends in yet another minimum (0.30 / 0.38) with a "local minimum" warning. It reported the 20 s fit and its warnings |
| es2-fresh radical_f1#2 | aN 0.37 / aH 0.17 vs 0.687 / 0.298 | epr_lineshape, labkit_source | no | n/a | no | yes ("well-established literature values") | 2 failed runs | none | Hand scipy `minimize` around `simulate_iso`. No hint covers that: `epr_iso_fit` stands down once `simulate_iso` is called. It then saved literature values and cited a DOI pair with an unverified tag |
| es2-fresh units_f1#1 | 100.2 vs 98.04 MHz | epr_lineshape, epr_g | no | n/a | no | no | 1 failed run + 4 malformed `run_python` calls (no `code`) | none (578 s) | g from the midpoint of the sweep window (about 2.15), not from the spectrum (2.1036). `epr_g` fired and named `g_value`, but the model did not use it |
| es2-fresh units_f1#2 | 93.32 vs 98.04 MHz | epr_lineshape, epr_g | no | n/a | no | no | 1 failed run (`find_minima` does not exist) | none | Computed g from the window midpoint, then chose g_e and "verified" the factor against the 21-cm line. This is the g_e trap |

Summary of the eight failures:
- 3 are units conversions: g_e twice, the sweep midpoint once.
- 3 are radical fits: no fit twice, a wrong basin once.
- 2 are older CW cases: a hand fit once, a masked line once.
- No failure ended at a bound.
- `powder_starts` played no part: it was used in every passing powder run and in none of the failures, and it has no kind for isotropic radicals.
- The Cu `max_time_s` caveat (8 of 20 draws in the build check) did not surface in any Cu answer. All 6 Cu runs passed, and each `_f1` Cu run had one timed-out call before a passing fit.

## Next round (proposal, not built)

What is left is the radical fit, the units conversion, and the hint plumbing. All three can be addressed with tooling.

1. **Fix the context read for `epr_sim_context`** (retrieval). Reproduce with a hermetic test: a stored user message made of the prompt plus two `_data_file_block`s (BES3T pair), then `_user_text`. Fix what drops it. Log `hint context len=N` once per conversation so an eval can see it. This is the precondition for measuring the hint at all.
2. **Isotropic start estimates** (labkit 0.3.2). Add `epr.powder_starts(..., kind='iso', nuclei=[('14N', 2), ('1H', 4)])`, or an `iso_starts`: a coarse grid of cheap stick simulations over the couplings, scored on the spectrum. `fit_spectrum(model='iso')` should add it as a start, as it does for Cu and nitroxide. The 20 s budget finished 2 of 8 starts on 2N + 4H, so the fit should also start from the grid winner first, and say in a warning when the best fit has about twice a coupling of another start.
3. **Hint for hand fits around labkit simulators** (retrieval). When code calls `simulate_iso` or `simulate_powder` inside `scipy.optimize` (`minimize`, `least_squares`, `curve_fit`), point to `fit_spectrum` with the same system. Today no hint fires there.
4. **Units hint** (retrieval). It fires when code converts a splitting with g_e or a fixed 28.0 MHz/mT (`28.02`, `2.8025`, `g_e * mu_B / h`), or takes g from `(field.min() + field.max()) / 2`. It says: A(MHz) depends on the g of this spectrum. Use `g = epr.g_value(B_centre, d.freq_GHz)` at the centre of the pattern, then `epr.convert(a, 'mT', 'MHz', g=g)`. No units run ever called `convert`; the passing runs used `g_value`.
5. **results.json vs answer check** (harness, small). `complex#1` saved 13.9 mT and said 0.149 mT. The save round could compare the numbers in results.json with the numbers in the answer and ask once when they disagree by more than 10%.

Open questions (defaults in brackets):

| # | Question | Default |
|---|---|---|
| N1 | Fix item 1 before anything else and rerun `es2-labkit` on the arm to measure the hint alone? | Yes, fix first; rerun the radical and units cases only (6 x 3) |
| N2 | Iso starts as a new `kind='iso'` of `powder_starts` or a separate `iso_starts`? | `kind='iso'`: one function to learn |
| N3 | Raise the `fit_spectrum` default `max_time_s` for iso fits with many nuclei? | No; better starts first, then measure |
| N4 | The units hint: fire on g_e alone, or only when the question names a metal or a g far from 2? | On g_e or 28.0 in a mT-to-MHz product, whatever the question |
| N5 | Deploy LABKIT-019 as built, before this round? | Yes, after a rerun on the deployed instance with ledger and cutter on, since the test arm had them off |
