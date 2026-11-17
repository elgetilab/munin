# labkit 0.1.1 and pointers, round 3: plan

Status: IMPLEMENTED 2026-11-17 ("proceed": Q1 raise, Q2 yes, Q3 no), not
yet deployed. labkit 0.1.1: `_check_log_x` in `fit`/`fit_global` (and `rout`
through `fit`); on all 24 S1 dose-response fixtures raw concentrations raise
and their log10 passes with no warning. Hints replayed on every run-2
script: relaxation 3/3 -> relaxation_fit, HSQC 6/6 -> kd_fit, standard
curve 24/24 -> interpolate; first drafts misfired (R2 meaning R-squared read
as relaxation; "standard error" read as a standard curve) and were
tightened. Tests: labkit 28/28 (sandbox CI image, with sandbox live 54/54,
figure hook 20/20), retrieval hermetic 116/116 (`test_labkit_hints` 9). Parent: `LAB-ANALYSIS-PLAN.md`
(Phase 4 run 2), `LAB-ADOPTION-PLAN.md`.

## What run 2 left (checked in the bundles)

| Class | Runs | Cause |
|---|---|---|
| `x_scale` misread | 4PL/5PL: s1_4pl_s1#3, s1_5pl_s4#2, fresh s1_5pl_s8#3, s1_4pl_s7#3 | The model passes raw concentrations with `x_scale="log"`, reading it as "fit on a log axis"; labkit means "x is already log10". The fit then lands on nonsense (Hill -3.1, Bottom -49419) with no error. An API trap in labkit. |
| 5PL logEC50 | s1_5pl_s1#2, s1_5pl_s5#1 | Reported `LogXb` as logEC50. labkit derives `LogEC50` (halfway response), but `print(r)` lists `LogXb` first and the derived value further down. |
| Standard curves | fresh 3/9 lost | None of them used labkit: `np.polyfit` plus hand interpolation, values saved as `[value, CI]` lists. The run 1 hint does not fire on `polyfit`. |
| S6 never uses labkit | 0/15; relaxation 0/3 | The hint fires on the relaxation scripts but names `labkit.fit("one_phase_decay")`, not `labkit.nmr.relaxation_fit`; nothing points at `labkit.nmr.csp` / `kd_fit` for titrations. The persona names the module, not the functions. |
| Not labkit | NIST Lanczos 3 runs | Hand-written fits. Lanczos1 run 1 found the same curve with the exponential terms in a different order (b1 <-> b5); Lanczos3 a local minimum. Left alone. |
| Results shape | Gen5, relaxation | Correct numbers saved in another structure. Left alone (see Q3). |

We also cannot see whether a hint fired: the harness keeps tool names, not
results.

## Changes

1. **labkit 0.1.1, `x_scale` guard** (`fit`, `fit_global`, `rout`). With
   `x_scale="log"`, if x is all positive and is a dilution series (constant
   ratio between successive distinct values, at least 1.5x, and not also
   evenly spaced), raise: "x looks like concentrations (a 3x dilution
   series), but x_scale='log' means x is already log10(concentration). Pass
   x_scale='linear' and labkit takes the log, or pass np.log10(x)." Not a
   dilution series but spanning more than 3 decades with all values
   positive: a warning in `r.warnings`, not an error. Tests: both
   directions on every S1 dose-response fixture, and log10 inputs that are
   all positive (0.1..2) must not raise.
2. **5PL output**: `print(r)` for 5PL lists `LogEC50` right after the fitted
   parameters with "# report THIS as logEC50"; `LogXb`'s definition says
   "not the EC50 (see LogEC50)". `to_dict()` unchanged.
3. **Hints** (`labkit_hints.py`), checked before the generic exponential
   one:
   - NMR relaxation (R1/R2/T1/T2/hetNOE/relax in the code plus a fit) ->
     `labkit.nmr.relaxation_fit` and `labkit.nmr.hetnoe`.
   - Titration Kd (Kd plus CSP, titration or chemical shift, plus a fit) ->
     `labkit.nmr.csp` and `labkit.nmr.kd_fit`.
   - Standard curve (`polyfit` or `linregress` plus standard / unknown /
     interpolate) -> `labkit.fit("linear", ...)` and `labkit.interpolate`
     (returns null outside the standards' range, as such prompts ask).
4. **Persona**: the LAB DATA rule names the NMR functions (`relaxation_fit`,
   `hetnoe`, `csp`, `kd_fit`) and says for dose response:
   "`x_scale="linear"` when you pass concentrations".
5. **Visibility**: retrieval logs one INFO line per hint
   (`labkit_hint kind=<kind> conv=<id>`), so a run can report how often each
   hint fired and whether labkit followed.

## Test, then rerun

- labkit tests in the sandbox image (CI runner) plus the new guard tests;
  retrieval hermetic plus new hint tests, again on the model's own run-2
  scripts (relaxation, HSQC, standard curve).
- **Targeted rerun** on the eval instance, 3 reps: the dose-response
  families (3pl, 4pl, 5pl), standard curve, trap_outlier and all of S6, on
  both the original and the fresh set: 26 + 12 cases, 114 runs, about 70
  min. Compared with run 2 and the baseline on the same cases. Not a paper
  number.
- If it holds, a full run 3 (95 x 3 + fresh 27 x 3) for the scorecard.

## Rollout

Sandbox image rebuild (labkit), retrieval and personas deploy, then
`instance refresh eval` (you run them). Production gets all of it.

## Open questions (defaults taken unless you say otherwise)

| # | Question | Default |
|---|---|---|
| Q1 | Dilution-series case: raise, or only warn? | Raise: a wrong-scale fit gives confident nonsense, and the error says exactly what to pass. Ambiguous inputs only warn. |
| Q2 | Full run 3 after the targeted rerun? | Yes, if the targeted rerun is at least as good as run 2 on those cases. |
| Q3 | A persona line on keeping results in the asked form (a number per name, no extra lists)? | No: it would tune to the grader. Real users read the answer, not results.json. |

## Result: targeted rerun (2026-11-17, labkit 0.1.1 deployed)

Tags `r3-targeted` and `r3-targeted-fresh` (no scorecard), 3 reps, compared
with run 2 on the same cases:

| Set | Round 3 | Run 2 | Baseline | labkit used (run 2) |
|---|---|---|---|---|
| Original (3pl, 4pl, 5pl, standard, trap_outlier, S6) | 68/78 | 64 | 67 | 68 (45) |
| Fresh (3pl, 4pl, 5pl, standard) | 31/36 | 27 | - | 34 (26) |

4PL 15/15 and 9/9 (run 2: 12, 6); standard curve now uses labkit (13/15
and 7/9 runs, run 2: 0); trap_outlier 3/3 with `labkit.rout`; HSQC 6/6 and
relaxation 3/3 use `labkit.nmr`. Hints fired (eval log): standard_curve 19,
dose_response 16, kd 6, relaxation 3, exponential 3.

**Two labkit bugs found, fixed in 0.1.2:**
- **5PL LogEC50 had the wrong sign** (`LogXb + log10(2^(1/S)-1)/H`, should
  be minus). The run-2 5PL failures were NOT the model reporting LogXb: it
  reported labkit's wrong LogEC50 (same values in run 2 and round 3, e.g.
  s1_5pl_s1 0.387001 vs truth -0.535). Present since 0.1.0, so the 5PL
  runs of Phase 4 runs 1 and 2 that used labkit were scored against a
  wrong helper. The 0.1.1 test passed because it checked the formula
  against a copy of itself; the new test checks every derived value
  (5PL for S above and below 1 and both slope signs, 4PL, decay and
  association half-life and span, linear X intercept) against a numerical
  root of the fitted curve, and fails on 0.1.1.
- **`nmr.csp` accepted (15N, 1H) tuples silently.** s6_hsqc_titration_s2
  2/3: the model passed Sparky's w1/w2 order and got CSPs 2-7x too large
  (Kd still right). `csp` now raises when the first values look like 15N
  (median > 50 ppm) and the second like 1H, and says to swap.

Left alone: relaxation saved R1/R2 as bare lists (values match the truth),
standard-curve `[value, CI]` lists (Q3), one 3PL CI.
labkit tests 30/30 in the sandbox image; sandbox live 54/54, figure hook 20/20.
