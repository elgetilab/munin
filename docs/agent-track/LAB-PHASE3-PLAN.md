# Lab analysis Phase 3: `labkit`, plan

Status: 3a IMPLEMENTED 2026-11-16 ("proceed", defaults Q1-Q5), frozen at
`6900e5f`, not yet deployed. Tests: labkit 26/26 in the sandbox image
(all 27 NIST StRD problems from both certified starts: parameters to 1e-6,
standard errors to 1e-4, Lanczos1 to 1e-2; a 200-dataset ROUT false-positive
check), sandbox live 54/54, figure hook 20/20, retrieval hermetic 114/114;
an isolated kernel imports it. Fresh set: 27 S1 cases (seeds 6-8) in
`backend/benchmarks/fixtures/lab_fresh/`, generated after the freeze, not
inspected. Checked first: `s5_anova` (unseeded jitter) and `s2_jasco_cd` (a
x100 unit slip) are model-side, not plumbing. Not done from the 3a list: fresh
S2/S6 variants (those generators are not seeded). Parent plan: `LAB-ANALYSIS-PLAN.md`
(Phase 3 spec, Phase 4 paired comparison). Baseline to beat:
`2026-11-16_phase2-baseline.json`, 243/285, mean 0.935.

## What the baseline says `labkit` must fix

All 42 baseline failures are model-side (`LAB-ANALYSIS-PLAN.md`, Phase 2
result). Grouped by what a helper can change:

| Family | Runs | Helper that removes it |
|---|---|---|
| Convention: one-phase decay "Y0" reported as the span | 10 | `fit()` returns Prism-named parameters (`Y0`, `Plateau`, `Span`, `K`, `HalfLife`) with a one-line definition each |
| Interval method: CI off the reference | about 9 | Prism's default: fit every replicate, asymptotic (Wald) CI with t at the residual dof; profile CI only on request |
| Standard curve: replicate unknowns reported as pairs | 2 | `interpolate()` averages replicates by name, returns `None` outside the standards' range |
| Raw Bruker FID misread (SW in ppm as MHz, int32 as int16) | 3 | `read_bruker()` from `acqus` via nmrglue, dtype from `DTYPA`/`BYTORDA`, raises on anything it cannot determine |
| NMR result shapes (R1/R2/hetNOE per residue) | 3 | `relaxation_fit()` returns `{residue: value}` dicts |
| Ambiguous fits, outliers, weighting (S1 traps) | 3 | `fit()` warns on dependency > 0.99 and unconstrained parameters; `rout()` outliers; `weights="1/y^2"` |
| Plate-reader and CD units (`gen5`, `jasco` x100 off) | 2 | readers return tidy tables with units in the metadata; `mean_residue_ellipticity()` with explicit units |
| Unrequested figure edits (S3) | 4 | not a `labkit` problem (prompt rule already exists); left alone |

Checked first, before any code (they may be plumbing, then they go on the bug
list instead): `s5_anova` reproduction mismatch on `groups.png` (2/3 runs:
unseeded jitter, or the bundle's `analysis.py` not being the code that drew
the figure), and `s2_jasco_cd` off by about 100x.

## Scope for this round (3a)

Package `backend/sandbox/labkit/` (Apache-2.0), pure Python on what the image
already has (numpy, scipy, lmfit, statsmodels, pingouin, nmrglue):

1. **`labkit.fit`**: `fit(model, x, y, *, replicates="all"|"mean",
   weights=None|"1/y"|"1/y^2"|array, constraints=..., shared=...)` returning a
   result with `params` (Prism names), `ci` (Wald default, `ci="profile"`
   optional), `dependency`, `warnings`, `bands(x)` (confidence and
   prediction), `to_dict()`. Models: linear, 3PL, 4PL, 5PL (asymmetric),
   Michaelis-Menten, competitive inhibition, Morrison (tight binding),
   one- and two-phase decay, plus any callable. Model comparison: extra
   sum-of-squares F test and AICc. `rout(Q=1%)` outlier detection.
   `interpolate(standard_result, unknowns)` with replicate averaging and
   out-of-range `None`.
2. **`labkit.read`**: `read_table()` (encoding/BOM/UTF-16, delimiter and
   decimal-comma detection, returns DataFrame plus metadata), NanoDrop, Gen5,
   BMG, Tecan xlsx, JASCO CD, and `read_bruker()` (1D FID plus parameters).
   Each raises on ambiguity instead of guessing.
3. **`labkit.nmr`**: Bruker 1D chain with explicit steps (digital filter
   removal, FT, phase with given `p0`/`p1` or `autophase=True` named in the
   output, reference to a given ppm), peak picking and integration;
   CSP with `alpha` required and missing residues kept missing; global Kd fit
   with ligand depletion; relaxation fits (R1, R2 from intensity decays,
   hetNOE ratio) returning per-residue dicts with errors.
4. **`labkit.stats`**: two-way ANOVA (Type III), post-hoc on the pooled error
   term, p-value to asterisk mapping. Only what S5 uses this round.
5. **`labkit.guide(topic=None)`**: a compact text reference the model can
   print (a few hundred lines in total, one topic at a time).

Deferred to 3b (no baseline case exercises them): `.pzfx`/`.prism` readers,
mixed models and repeated measures, DOSY, 31P powder lineshapes, structure
helpers, `save_bundle()` (the platform bundle already exists).

## How the model learns about it

- The sandbox image installs it (`COPY labkit`, on `PYTHONPATH`); the compute
  agent's `ALLOWED_IMPORTS` gains `labkit`.
- The `code` persona gets one short paragraph (about 600 characters: what
  `labkit` covers, "prefer it for fits, standard curves, instrument files and
  NMR", "run `labkit.guide('fit')` to see the API"), not the reference
  itself; the prompt is already 16,000 characters.
- `run_python` and `compute` descriptions get one line pointing at it.

## Keeping the Phase 4 delta honest

The cases told us what to build; the code must not be tuned to them.
- `labkit` never imports `fixtures/lab/generate` and is not tested against
  the case `truth.json` files. Its tests use NIST certified values (the 27
  StRD problems, read from the certificate files in `fixtures/lab/nist/raw/`),
  closed-form cases, and Prism's documented definitions. The 27 S1 NIST cases
  are those same problems, so Phase 4 passes them by construction once the
  fitter is right: that is what StRD is for, but they are reported as their
  own line, not folded into the S1 delta.
- Fresh instances: after `labkit` is frozen, generate new seeds of every
  generated family (S1 s6-s10 style, new S2/S6 variants) that neither the
  code nor I have looked at. Phase 4 reports the paired delta on the original
  95 cases AND the score on the fresh set, so a gain that only shows on the
  seen cases is visible.

## Tests

- `backend/sandbox/labkit/tests/`: per function; the fitter against all NIST
  StRD certified parameters and standard errors (lower-difficulty sets to 6
  significant digits, higher to 4, from both certified start points);
  readers against small hand-made files covering each encoding and failure;
  the Bruker chain on a synthetic FID with known shifts.
- Run inside the sandbox CI image (`munin-sandbox:ci-live`, never `:latest`),
  wired into `run_sandbox_live_tests.sh` so CI runs them.
- Retrieval: the persona paragraph and the allowlist entry covered by the
  existing persona/schema tests.

## Rollout and Phase 4

Sandbox deploy (image rebuild, you run it) plus a personas deploy. Then Phase 4
on the eval instance: `--arm labkit`, the same 95 cases x 3 reps plus the
fresh set, `--concurrency 4`. Note: the sandbox is shared, so production users
get `labkit` from the same deploy; it changes nothing for them unless the
model chooses to import it.

## Open questions (defaults taken unless you say otherwise)

| # | Question | Default |
|---|---|---|
| Q1 | Pass thresholds (the parent plan sets them after the baseline) | Per-suite floor = the baseline pass rate (S1 0.89, S2 0.77, S3 0.33, S5 0.67, S6 0.73) as a regression gate; no target set before Phase 4. |
| Q2 | Fresh-instance set for Phase 4 | Yes: about 30 new cases from new seeds, generated after the code freeze, kept in the repo like the others. |
| Q3 | Scope | 3a as above; 3b after Phase 4. |
| Q4 | Autophase in the Bruker chain | Allowed only when asked (`autophase=True`), and the result records the phases used. |
| Q5 | Your other bugs | After 3a is merged and before the Phase 4 run, unless one is urgent. |
