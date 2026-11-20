# EPR round 2: plain-text EPR files, lineshape and DEER hints, membrane background: plan

Status: IMPLEMENTED 2026-11-20 (Q1 changed: the agent asks the researcher; Q2, Q3
defaults), not deployed. Parent: `EPR-PLAN.md`
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
