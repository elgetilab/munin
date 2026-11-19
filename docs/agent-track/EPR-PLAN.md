# EPR analysis (CW-EPR and DEER) in labkit, and portable bundles: plan

Status: IN PROGRESS 2026-11-19 ("proceed", Q1-Q7 defaults). Parent: `LAB-ANALYSIS-PLAN.md`,
`LABKIT-014-PLAN.md`. Direction from the user (2026-11-17): labkit should grow
by running research agents on the fits the group does often, starting with
EPR (CW-EPR, DEER). The eval instance stays up (job extended to 14 days).

Two research agents (2026-11-19) surveyed CW-EPR and DEER: file formats, the
field's conventions and formulas, reference software, typical mistakes,
candidate eval tasks, and an API. Their reports are summarised below; the
items they marked unverified are listed under "Verify before freezing".

## What the research found

**Reference software cannot run in the sandbox as is.**
- **EasySpin**, the CW reference, is MIT but needs MATLAB and does not run
  on Octave. No Python port exists.
- **The Python CW packages** are early-stage, GPL (EPRsim), or heavy (MarS
  needs torch). cwepr (BSD-2) only does processing.
- **DeerLab** (MIT, 1.2.0, maintained by Jeschke and Stoll) is the DEER
  reference. It pulls in `cvxopt` (GPL-3.0, 21 MB wheel), h5py, and pytest
  and setuptools as runtime dependencies: about 50 MB on top of our image.
  Its API also changed heavily at 1.0, and models still write the removed
  0.x calls (`fitsignal`, `fitregmodel`).

**So labkit implements a small, documented set itself** (numpy/scipy only,
like the rest of labkit) and is tested against the references at
development time:
- **CW:** analytic truths; EasySpin's loader logic, read from its source.
- **DEER:** DeerLab, in a throwaway test container, never in the sandbox
  image.

**The conventions that decide right from wrong answers** (these are what
labkit encodes):
- **CW**
  - `g = 71.447735 * nu[GHz] / B[mT]`, with CODATA 2022 constants.
  - The microwave frequency comes from MWFQ (stored in Hz), not a nominal
    9.5 GHz.
  - Peak-to-peak width vs FWHM: Lorentzian FWHM = sqrt(3) * dBpp,
    Gaussian 1.1774 * dBpp.
  - Double integration with baseline correction before and after the first
    integral. The window must be at least 10-20 * dBpp for Lorentzian wings.
  - Normalisation for spin counting:
    - receiver gain as 10^(dB/20);
    - sqrt(P);
    - modulation amplitude linear (exact even when overmodulated);
    - number of scans, unless SctNorm is set;
    - S(S+1) and g against the standard.
  - Nitroxide a_N from the outer lines (second-order shifts move the
    centre line).
  - Kivelson tau_c from line heights, valid only in the fast-motion regime.
  - Power saturation `A = I sqrt(P) [1 + (2^(1/eps) - 1) P/P_half]^(-eps)`.
    The older "b" form's P' is not P_half.
- **DEER**
  - One-step fit of P(r), modulation depth, background and zero time
    together (the 2021 community white paper, Schiemann et al., JACS 143,
    17875).
  - Phase the complex trace and never use |V|.
  - The zero time is not the first point.
  - The background divides; it is not subtracted.
  - Non-negative Tikhonov with alpha chosen by AIC (DeerLab's default).
  - nu_dd = 52.04 MHz nm^3.
  - Background k [1/us] = 9.974e-4 * lambda * c [uM] (homogeneous 3D);
    stretched (d about 2) for membranes.
  - Distance limits from trace length:
    - mean distance reliable to 3.73 * tmax^(1/3) nm;
    - width and shape to 2.96 * tmax^(1/3) nm.
- **Files:**
  - Bruker BES3T (`.DSC` plus `.DTA`; big-endian by default; complex values
    interleaved; X varies fastest; axis = XMIN + linspace(0, XWID, XPTS))
    for both CW and DEER.
  - Bruker ESP/WinEPR `.par`/`.spc` (int32 big-endian, or float32
    little-endian if a DOS key is present).
  - ASCII exports with the unit only in the header, or nowhere.
  - DeerLab's `deerload` divides every axis by 1000 ("ns to us"), which is
    wrong for CW field axes.

## Part A: labkit.epr (labkit 0.2.0)

Internal units: field in **mT**, time in **us**, distance in **nm**, P(r)
in nm^-1 with integral 1. Every loader converts from the file's unit and
says which unit it read. Results are objects with `FieldHelp` attribute
errors, a `warnings` list, and `print()` showing each value with its
definition, like `labkit.fit`.

1. **Reading** (`labkit.epr.load(path)`): BES3T (1D and 2D, real and
   complex, IGD axes), ESP/WinEPR par/spc, and ASCII.
   - ASCII: unit from the header or from the magnitude (about 3400 means G
     at X band); it raises if ambiguous, and says what to pass.
   - Returns `EPRData`:
     - `field_mT` or `t_us`, `y` (complex if stored complex), `y2_axis`;
     - `freq_GHz`, `power_mW`, `modamp_mT`, `gain_dB`, `n_scans`,
       `conv_time_ms`, `temperature_K`, `sctnorm`;
     - `params` (raw).
   - `labkit.read` sniffs these formats too, and the upload hint names the
     reader.
2. **CW**
   - Field and g: `g_value`, `field_for_g`, `calibrate_field` (against
     DPPH, BDPA or a given g), `frequency_correct` (scales the field;
     does not shift it).
   - Lines: `zero_crossing`, `peak_to_peak` (`dBpp_mT`, `Ypp`),
     `fwhm_from_pp(shape=)`.
   - Integration: `baseline_correct`, then
     `double_integral(field, y, wings=, order=)`, which warns when the
     window is shorter than 10 * dBpp.
   - Counting: `normalize_intensity`, then `spin_count(sample, standard,
     ...)`, which shows every factor.
   - Nitroxides: `nitroxide_hyperfine` (a_N in mT and MHz, g_iso, per-line
     heights and widths), `kivelson_tau` (warns outside 5e-11 to 1e-9 s).
   - Power saturation: `power_saturation_fit` (P_half, epsilon) and
     `attenuation_to_mW`.
   - Simulation and fitting: `simulate_iso` / `fit_iso`, an isotropic
     fast-motion model:
     - any number of nuclei;
     - Lorentzian, Gaussian or Voigt lines;
     - widths A + B mI + C mI^2;
     - optional second order and the exact modulation kernel.
   - Slow motion and powder patterns are out of scope, and the guide says
     so and points to EasySpin.
3. **DEER**
   - Preprocessing: `correct_phase` (returns the phase and the rms of the
     imaginary part as a noise estimate), `zero_time`, `average_scans`.
   - Model parts: `dipolar_kernel` (Fresnel form; scipy's `fresnel`
     returns (S, C), a known trap), `bg_hom3d`, `bg_strexp`,
     `bg_rate_to_conc`, `distance_limits(tmax_us)`.
   - Fitting: `fit_deer(t_us, V, method="tikhonov"|"gauss", n_gauss=,
     background="hom3d"|"strexp", fit_t0=True, n_boot=)`, a one-step
     separable fit:
     - P by non-negative least squares on [K; alpha L], alpha by AIC;
     - lambda, k and t0 by `least_squares`;
     - residual bootstrap for 95% bands.
   - Result:
     - `r_nm`, `P`, `P_ci95`;
     - mean, modal and std distances with CIs;
     - `lam`, `k_per_us`, `conc_uM`, `t0_us`, `alpha` and its criterion,
       SNR;
     - warnings: distance above the trace limit, an imaginary part that is
       not white, lambda > 0.6 (more than two spins);
     - `summary()`: the white paper's reporting block.
   - Generator: `simulate_deer(...)` for the evals and for users' own
     sanity checks.
4. **Guide and hints**
   - `labkit.guide("epr")` and `labkit.guide("deer")`, each with an
     end-to-end example that runs as printed (tested, as for NMR).
   - Domain hints for EPR code done by hand:
     - g from a nominal frequency;
     - a single integral reported as a spin count;
     - `np.fromfile` on a `.DTA` file;
     - unregularised `lstsq`/`pinv` on a dipolar kernel;
     - DeerLab 0.x calls.
   - The persona's LAB DATA rule names `labkit.epr`.
5. **Tests**
   - Every formula checked against an independent computation.
   - BES3T and par/spc round trips against files written by the generator.
   - `fit_deer` against DeerLab 1.2.0 on the DEER generator cases, in a
     throwaway container with `pip install deerlab`.
   - Agreement on mean and width within the eval tolerances, recorded in
     the plan.

## Part B: the eval suite S8 (EPR)

**Fixtures.** Generated with known truths: about 12 CW and 10 DEER tasks,
from the agents' proposals, trimmed:
- CW
  - g from BES3T;
  - DPPH calibration;
  - complex little-endian BES3T and FWHM;
  - Gaussian FWHM;
  - spin count against TEMPOL with different gain, power, modulation and
    scans, plus baseline drift;
  - a broad-Lorentzian window;
  - nitroxide a_N and g_iso;
  - Kivelson tau_c;
  - a 2D power sweep (P_half, epsilon);
  - overmodulation;
  - ESP/WinEPR files;
  - frequency correction.
- DEER
  - ns-to-us axis;
  - modal distance in Angstrom;
  - phasing;
  - zero time;
  - bimodal;
  - a too-short trace (the answer must flag the long component);
  - concentration from the background;
  - labelling efficiency from lambda;
  - 2D complex BES3T;
  - membrane (stretched) background.
- Fresh seeds for a tuning check, as in S1.

**Truths.** Computed analytically from the generating parameters, never
from a fit. Formula tasks (Kivelson) take the formula's value as the truth.
Tikhonov widths get a wider tolerance (they are biased broad).

**Grading.** "Flag" tasks (too-short trace, overmodulation) are graded on a
keyword rubric plus a number.

**Measure as in the lab suite.**
1. Baseline arm first, with the current system and no `labkit.epr`. This
   shows what the model gets wrong.
2. Then the labkit arm, after Part A.
3. 3 reps each, paired McNemar.
4. The v2 results request.

## Part C: portable bundles (deferred Q4 of LABKIT-013)

A bundle whose code imports labkit runs only where labkit is installed.
When any run included in `analysis.py` imports labkit, `bundle.py` copies
the installed labkit package (`.py` files only, about 150 KB) into
`labkit/` next to `analysis.py`, with labkit's license and version.
`python analysis.py` then imports that exact copy with no install. The
README says so, and the copy is listed in MANIFEST/SHA256SUMS. Test: a
labkit-using bundle reproduces in a plain `python:3.11-slim` container with
only numpy/scipy/pandas/matplotlib installed.

## Verify before freezing (marked unsure by the agents)

- **CW constants**
  - The Kivelson constant 6.51e-10 s/G (it depends on the tensors).
  - The Goldman-Bruno-Freed constants.
  - The exact g correction in relative spin counting.
  - The Xenon gain convention: 10^(dB/20) or 20 * 10^(dB/20).
  - Checked against the primary papers and EasySpin's source; where
    conventions differ, labkit names the one it uses and the other in its
    warnings and docs.
- **DEER / DeerLab**
  - cvxopt's license.
  - Whether DeerLab's default solver uses cvxopt.
  - The DeerLab 0.x names the hints catch.
  - `dd_gauss` std vs FWHM in older versions.
- **BES3T:** the details are checked against EasySpin's `eprload` source,
  and against real Xepr files if the group can share a few (Q4).

## Rollout

1. labkit 0.2.0 and bundle.py go out in a sandbox image rebuild.
2. Hints and persona go out in a retrieval and personas deploy, then
   `instance refresh eval`. You run these.
3. The baseline arm runs before step 1, on the current deploy.
4. Production gets all of it.

## Open questions (defaults taken unless you say otherwise)

| # | Question | Default |
|---|---|---|
| Q1 | DEER: own implementation in labkit, or install DeerLab in the sandbox? | Own: no GPL cvxopt in the image, no 50 MB, conventions in our signatures. DeerLab is the test oracle. If a user asks for DeerLab by name, the model says it is not installed and does the same fit with labkit. |
| Q2 | Run the S8 baseline arm before building labkit.epr? | Yes: it is the comparison the paper needs, and it shows which traps the model actually falls into, so hints target real failures. |
| Q3 | CW scope: isotropic fast-motion simulation only? | Yes. Slow motion and powder spectra need EasySpin's chili/pepper; labkit says so instead of half-doing them. Revisit after S8. |
| Q4 | Real data: can the group share a few real Xepr files (CW nitroxide, DEER) as held-out checks, kept outside git under `/opt/munin/data/evals/`? | Ask; until then synthetic only. Real files catch format details no generator has. |
| Q5 | Portable bundles (Part C) in this round? | Yes: small, no eval runs needed, and EPR analyses will import labkit too. |
| Q6 | Make "research agents per domain" a repeatable step (a brief template in `docs/agent-track/`, then plan, baseline, build, rerun)? | Yes: the next domains (e.g. ITC, SPR, fluorescence anisotropy) follow the same path. |
| Q7 | Suite name S8? | Yes (S7 is the structure-viewer suite planned in Phase 5). |

## Progress (2026-11-19)

- **S8 fixtures** (`f5dad35`): 11 CW and 10 DEER cases, generated by
  `fixtures/lab/generate/s8.py` without labkit.
  - DEER cases checked with DeerLab 1.2.0 in a throwaway container: its
    Gaussian fits recover every truth (e.g. phase case 3.200/0.250/0.350),
    and its BES3T reader agrees with ours.
  - CW cases checked with an independent reference analysis.
  - Fixes on the way:
    - the DEER std tolerance is 0.12 nm (Tikhonov widths are biased broad);
    - the membrane case grades the mean only;
    - the short-trace weights are 65/35, so the main distance is
      unambiguous;
    - the broad-window and overmodulation windows were widened;
    - noise was lowered where the double integral was noise-limited
      (broad-window and spin-count; see below).
- **Baseline arm** (`s8-pre-epr`, current system with labkit 0.1.5, no EPR
  support) running on the eval instance.
- **Portable bundles, Part C** (`596f82c`): a bundle whose code imports
  labkit carries labkit's sources and license. It reproduced in a plain
  container without labkit (numpy 2) byte for byte. Sandbox live 62/62.
- **labkit 0.2.0** (`15815f8`): `labkit.epr` solves 21/21 S8 cases in the
  sandbox image at 2 CPUs; the slowest DEER fit takes 13.5 s (run_python
  default 30 s). Found while building:
  - lambda must use sum(q) * dr (the kernel carries the dr weight).
  - AIC needs the full influence-matrix trace; counting only non-zero NNLS
    coefficients drove alpha to 4e-4 and split P(r) into noise peaks.
  - With r allowed beyond the distance the trace supports, a stretched
    background (d) and a long-r tail in P(r) trade off. The data cannot
    tell them apart: RSS was lower at d = 3 plus a tail than at the true
    d = 2. So the default r range now ends at 3.73 tmax^(1/3) nm, the
    fractal dimension is bounded to 1-3, and a second fit over 1.5x that
    range warns when it gains more than 20 AIC units and 3% residual. On
    the fixtures that warning fires on the too-short trace only (gain 52 vs
    at most 4.5 elsewhere).
  - labkit tests 60/60 (22 EPR checks), retrieval hermetic 118/118.
  - The EPR hints (`test_labkit_hints` 16/16) give no false positives on
    run 4's 366 lab bundles.
- **Fresh S8 set** (`fixtures/lab_fresh`, `_f1` cases): parameters redrawn
  by `s8.VARIANT = 1` after labkit 0.2.0 was frozen.
  - Validated with DeerLab and the reference CW check only. The exception:
    labkit's `spin_count` and `double_integral` were run on variants 0-7 of
    two cases to measure how noise-limited they were. That changed the
    fixtures' noise, not labkit.
  - Those two cases (broad-window, spin-count) get lower noise and a less
    narrow reference line, and broad-window a tolerance of 8%. Their 6
    baseline runs are rerun on the corrected files.
- **Baseline arm** (`s8-pre-epr`, labkit 0.1.5, no `labkit.epr`), 21 x 3:
  **17/63** (0.270), mean check score 0.335. CW 17/33: DPPH calibration,
  Kivelson and overmodulation 3/3; g (BES3T), complex, power saturation
  2/3; Gaussian FWHM, nitroxide 1/3; spin count, broad window, ESP 0/3.
  DEER 0/30. The spin-count and broad-window runs were rerun on the
  corrected files (still 0/3: spin ratios 0.43-0.69 vs 2.4, concentrations
  25-179 uM vs 37.5). Scorecard `2026-11-19_s8-pre-epr`.
- **Fresh set, labkit check** (a fixed script calling `labkit.epr`, run
  after the fresh set was made; labkit unchanged): 19/21 numeric. The two
  misses are fixture properties, not labkit bugs. DeerLab 1.2.0's
  model-free fit gives the same answers as labkit on both:
  - `s8_deer_short_trace_f1`: model-free modal 3.18 nm (labkit 3.18), truth
    3.05, atol 0.12.
  - `s8_deer_membrane_f1`: model-free mean 3.39 nm (labkit 3.41), truth
    3.19, atol 0.12.

  DeerLab's Gaussian fit recovers both (3.051, 3.193), so with this draw only
  a parametric fit (`fit_deer(method="gauss")`) passes. The fixtures are
  left as generated, not widened after seeing the result; the fresh score
  is reported with and without these two cases.
