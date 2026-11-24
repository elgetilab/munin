# EPR round 2: plain-text EPR files, lineshape and DEER hints, membrane background: plan

Status: IMPLEMENTED and DEPLOYED 2026-11-20 (Q1 changed: the agent asks the
researcher; Q2, Q3 defaults); targeted rerun 17/21 (round 1: 10/21, p = 0.013),
fresh `_f2` 14/21. Parent: `EPR-PLAN.md`
(S8 49/63 with labkit.epr vs baseline 17/63, p = 2e-7; fresh 47/63).
The eval instance stays up.

## What S8 left (checked in the bundles)

| Failure | Runs (orig + fresh) | Cause | Fix by tooling? |
|---|---|---|---|
| Kivelson | 3/6 fail | Plain `.csv`; no run called labkit.epr; hand smoothing lowered the peak heights | Yes: upload note for ASCII EPR, lineshape hint |
| Gaussian FWHM | 5/6 fail | Plain `.txt`; hand fits, 3 saved nothing; got the `exponential` hint (wrong domain) | Yes: same, and `exponential` stays quiet on EPR lines |
| Broad window | 6/6 fail | `epr.double_integral` with defaults passes both (2.31 vs 2.4, 2.59 vs 2.68); the runs that called it ran out of time, the rest fitted by hand | Yes: upload note, `guide('epr')` recipe |
| DEER labeling, concentration | 5/12 fail | Hand fits after the `deer` hint fired (lambda 0.0015, 0.008 vs 0.2; two saved nothing) | Yes: hint with a 3-line example naming `r.lam`, `r.conc_uM` |
| Membrane | 5/6 fail | `fit_deer(t, V)` with the default hom3d background on a liposome sample; 3 fresh runs also hit the model-free limit | Partly: a warning naming `background="strexp"` |
| Power saturation | 2/6 fail | `epr.load`, then fitted by hand; never `power_saturation_fit` | Yes: hint |
| Short trace fresh | 3/3 | Model-free limit (DeerLab agrees) | No |

## Changes

1. **Upload note for ASCII EPR** (`upload_hint`, which already receives
   the bytes): a text file whose header names a field column in G, mT or
   Gauss together with a derivative or EPR signal (`dI/dB`, `deriv`,
   `signal`, `EPR`, `ESR`), or a time column in ns/us with a DEER trace
   word, gets one sentence: `labkit.epr.load(path, x_unit="G")` reads it,
   see `labkit.guide('epr')`. Tests on all S8 ASCII inputs plus negatives
   (run-4 CSVs: plate reader, CD, NMR integrals stay quiet).
2. **Hint `epr_lineshape`**: EPR context plus peak-to-peak work by hand
   (`find_peaks`/`argmax`/`argmin` on a derivative, `dBpp`, `peak.to.peak`,
   `kivelson`, `6.51e-10`, `fwhm`) names `epr.peak_to_peak`,
   `epr.fit_line(shape="gaussian"|"lorentzian")`,
   `epr.nitroxide_hyperfine` and `epr.kivelson_tau`. Fires with labkit
   imported unless the code calls one of them.
3. **Hint `epr_power`**: EPR context plus power or attenuation (`dB`,
   `mW`, `sqrt(P)`, `P_half`, `saturation`) fitted by hand names
   `epr.power_saturation_fit`.
4. **`exponential` stays quiet on EPR lines**: no exponential hint when the
   EPR context matches and the `exp(-` is a Gaussian (`exp(-...**2`).
5. **`deer` hint gets an example** (the round-5 competitive example cut
   tool calls): `r = labkit.epr.fit_deer(t_us, V); r.lam, r.mean_nm,
   r.conc_uM, r.summary()`, and "`background="strexp"` for membranes".
6. **`fit_deer` membrane warning**: with the default `background="hom3d"`,
   also fit `strexp` (r range and alpha reused, about 2 s); if it gains more
   than 20 AIC units with d clearly below 3, warn: "a stretched background
   (d = 2.1) fits better: membrane or crowded sample? Try
   background='strexp'". The default stays hom3d (Q1).
7. **`guide('epr')` recipes**: three short worked lines for an ASCII
   spectrum in G (load with `x_unit`, `fit_line`, `nitroxide_hyperfine` then
   `kivelson_tau`, `double_integral` ratio). labkit 0.2.1.

Tests: hint tests on the S8 bundles that failed (each fires once, at the
right step) and false-positive checks on all run-4 and S8 bundles; labkit
tests for the membrane warning (fires on the membrane fixture, quiet on the
other nine DEER fixtures); guide examples run as printed.

## Rerun

Targeted, 3 reps: the seven families above on the original set and on a new
fresh variant `_f2` (`s8.VARIANT = 2`, generated after labkit 0.2.1 is
frozen), because this round is tuned on the `_f1` failures (Q2). About 42 +
42 runs, 2-3 hours. Paired against the S8 labkit runs on the original set.

## Rollout

Sandbox image (labkit 0.2.1) and retrieval (hints, upload note), then
`instance refresh eval`. Production gets all of it.

## Open questions (defaults taken unless you say otherwise)

| # | Question | Default |
|---|---|---|
| Q1 | Make `fit_deer` pick hom3d or strexp itself by AIC? | No (user, 2026-11-20): `fit_deer` does not choose; the agent takes the sample from the question or asks the researcher. |
| Q2 | Measure on a new `_f2` fresh set rather than `_f1`? | Yes: `_f1` failures shaped this round, so it is no longer untuned. |
| Q3 | Treat "saved nothing" (7 runs) in this round? | No: it is the general long-exploration problem (turn budget), not EPR; the hints aim to shorten those runs. |

## Decisions (2026-11-20)

- Q1: no automatic choice. When the background matters, `fit_deer`'s
  warning, the `deer` hint, `guide('deer')` and the persona say: use the
  sample named in the question; if it is not named, ask the user.
- Q2: measure on a new `_f2` set. Q3: the turn budget is a later round.

## What was built

- **`fit_deer` background check** (labkit 0.2.1). An AIC comparison of free
  strexp vs hom3d cannot see it: on the S8 membrane trace they fit equally
  (free d = 2.22 and d = 3 differ by 0.1 AIC units, so a multistart returns
  either). What differs is the answer. So with the default hom3d it also fits
  a membrane background with d fixed at 2 (new `dim=` option) and warns when
  that fits about as well (within 8 AIC units) and moves the mean by more
  than 0.05 nm. On the 20 S8 DEER fixtures (`_f1` included): the two
  membrane traces have dAIC 4.7 and 2.6 and shifts of -0.17 and -0.22 nm;
  every other one has dAIC >= 12.9. It fires on the two membranes only.
  With `dim=2.0` the means are 3.39 (truth 3.40) and 3.20 (truth 3.19): the
  `_f1` membrane that the model-free fit with a free d could not pass. The
  warning recommends `background='strexp', dim=2.0` for membranes
  (spins in a plane), free d for crowded samples, and asking the user when
  the question does not say. Cost 1-4 s; slowest S8 fit 12.9 s at 2 CPUs.
- **`guide('epr')`** has text-file recipes (load a CSV in G, `fit_line`
  linewidth, Kivelson from `nitroxide_hyperfine`, a spin ratio from two
  double integrals), checked on the S8 files: Kivelson 4.235e-10 (truth
  4.231e-10), FWHM 0.8001 (0.8006), spin ratio 2.31 (2.4, within 8%).
  **`guide('deer')`** says the sample decides the background and when to
  ask, and how to get lambda and concentration.
- **Hints** (`labkit_hints.py`):
  - upload note for text spectra whose header names a field column with a
    unit (`field_G`, `Field [mT]`, `B_G`, Xepr's `Field [G]`);
  - `epr_lineshape`: CW-EPR code (EPR words outside labkit's own names, or
    data loaded with `labkit.epr`; not DEER) that locates extrema or fits
    and computes a width or line height; names `fit_line`,
    `nitroxide_hyperfine`, `kivelson_tau`, `double_integral`;
  - `epr_power`: a saturation or attenuation fit by hand; names
    `power_saturation_fit`;
  - the `exponential` hint stays quiet on EPR and DEER code (a Gaussian
    line or a DEER background is not a decay);
  - the `deer` hint has a three-line example (`r.lam`, `r.mean_nm`,
    `r.conc_uM`) and the background rule.
  Replayed over run 4, round 5 and both S8 runs: run 4 and round 5 get
  exactly the hints they got before; in S8, `epr_lineshape` fires in all 6
  Kivelson runs and the Gaussian and broad-window runs, `epr_power` in all 6
  power-saturation runs.
- **Persona**: `epr.load` reads text exports too; a DEER question that does
  not name the sample gets a question back.
- Tests: labkit 64/64 (new: the background warning on a simulated membrane
  trace and not on a frozen solution, `dim` bounds, the text-file recipe),
  `test_labkit_hints` 17/17, retrieval hermetic 118/118.

## Fresh `_f2` set (generated after 6e64286)

21 cases, `s8.VARIANT = 2`. Checked without labkit: the independent CW
analysis recovers all 11 CW truths; DeerLab 1.2.0's model-free fit passes 9
of 10 DEER cases. The exception is `s8_deer_membrane_f2`: DeerLab with a
free stretched background gives mean 3.35 nm (truth 3.17, atol 0.12), its
Gaussian fit fails too. It needs the sample-based choice this round adds
(d fixed at 2 for a membrane); left as generated.

## Result: targeted rerun (2026-11-20, labkit 0.2.1 deployed)

Tags `s8-r2-targeted` and `s8-r2-targeted-fresh`, 3 reps, v2 results request,
`eval` instance (labkit 0.2.1 checked in `munin-sandbox`). Original set
**17/21** vs round 1 (`s8-labkit`) 10/21 on the same runs: 12 fail->pass,
2 pass->fail, exact McNemar p = 0.013 (vs the baseline `s8-pre-epr`: 12/1).
Fresh `_f2` **14/21** (round 1's `_f1` on the same families: 9/21; other
parameters, so not paired).

| Family | Orig r2 | Orig r1 | `_f2` r2 | `_f1` r1 |
|---|---|---|---|---|
| Kivelson | 3/3 | 0/3 | 3/3 | 3/3 |
| Gaussian FWHM | 0/3 | 1/3 | 0/3 | 0/3 |
| Broad window | 3/3 | 0/3 | 3/3 | 0/3 |
| Power saturation | 3/3 | 3/3 | 2/3 | 2/3 |
| DEER labeling | 3/3 | 1/3 | 1/3 | 2/3 |
| DEER concentration | 2/3 | 1/3 | 2/3 | 2/3 |
| DEER membrane | 3/3 | 1/3 | 3/3 | 0/3 |

- Membrane 6/6: every run passed `dim=2` (the questions name a liposome), so
  the sample-based rule worked without the user being asked; `_f2`, which
  DeerLab with a free stretched background fails, passes 3/3.
- Kivelson 6/6 and broad window 6/6: all called `nitroxide_hyperfine` +
  `kivelson_tau` or `double_integral`, most after `guide('epr')`.
- Hints fired (eval log): deer 12, epr_lineshape 6, epr_power 2,
  custom_fit 2, epr_integral 1, relaxation 1. The upload note is not logged;
  it returns the CW-EPR text note for both `defect.txt` files and `label.csv`.

What is left (7 failing runs of 42):

| Failure | Runs | Cause |
|---|---|---|
| Gaussian FWHM | 6/6 | No run called `fit_line`. Hand peak picking: FWHM = dBpp x 1.665 (2 sqrt(ln 2); right is sqrt(2 ln 2) = 1.177), or the right factor on extrema read by eye from noisy data (1.013 vs 0.983); two runs used all 20 tool calls (`help(labkit.epr)`, rereading the file, `print('hello')`, web search); one answered 0.81 (right) without code and saved nothing. `epr_lineshape` fired in one of six: the others' code had no width word or EPR word |
| Saved nothing | 4 (incl. 2 above) | Tool budget or answer without results.json (Q3) |
| DEER hand fits | 2 | No `fit_deer` (concentration#3; concentration_f2#2 after the deer hint) |
| Labelling formula | 1 | `fit_deer` used, lambda right, then divided by p(1-p) = 0.25 instead of the stated 0.50 (did not call `guide('deer')`) |

Later finding (LABKIT-016-PLAN): in the Gaussian runs the model saw no tool
result at all, because the inlined `defect.txt` filled the context and every
result was elided.

None of these is a missing tool. The Gaussian family is the one place a
hint could still help (it fires on width or EPR words that this code lacks);
the rest is the turn-budget round (Q3).
