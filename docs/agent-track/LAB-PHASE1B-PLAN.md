# Lab analysis Phase 1b: 31P solid-state lineshapes and MicroCal ITC: plan

Status: PLAN 2026-11-21, not started. Parent: `LAB-DEFERRED.md` section 1,
`LAB-PHASE1-PLAN.md` (case schema, graders, fixture rules), with
`EPR-PLAN.md` as the template for adding a whole suite: generator with
analytic truths, independent check against reference packages, baseline arm
first, a labkit round, then fresh variants made after labkit is frozen.

Phase 1 left both families out. The 31P case needed a lineshape simulator,
and `.itc` needed a verified format sample. Both are now solvable without
labkit: the generators compute the physics themselves (numpy/scipy, like
`s8.py`). The `.itc` layout below was checked against real public files.

## What the research found

**MicroCal `.itc` is plain text** (CRLF line endings), the same family for
VP-ITC and iTC200/Auto-iTC200. Checked against real files in
`choderalab/bayesian-itc` `data/` (VP-ITC, "VPViewer2000 Ver: 1.4.8";
iTC200, "ITC200 Ver: 1.26.1"). Two independent parsers read the fields the
same way: `bayesitc/experiments.py` (GPL-3.0) and MetaboMgITC `read.itc` (R).
- Header lines start with `$`:
  - `$ITC`;
  - `$ N` (number of injections, 50 and 15 in the samples);
  - `$NOT`;
  - target temperature in degrees C;
  - initial delay in s;
  - stirring speed in rpm (VP-ITC about 300, iTC200 1000);
  - reference power in ucal/s;
  - one unexplained integer;
  - `$ADCGainCode: n`;
  - `$False,True,True`;
  - then one line per injection: `$ volume uL , duration s , spacing s ,
    filter period s`.
- A `#` block holds 0 (unexplained), the syringe concentration in mM, the
  cell concentration in mM, the cell volume in mL (1.434 VP-ITC, 0.2002
  iTC200), the temperature, and two values we have not identified.
- Then `?`, a `%` block (firmware string such as `% VPITC06.03.471`,
  calibration constants, software version), and the data.
- Data:
  - `@0` starts the pre-injection baseline;
  - `@i,volume[,duration,time]` starts each injection;
  - rows are `time s, DP ucal/s, T degC` (VP-ITC), with six more columns on
    iTC200 that we leave unexplained.
- DP is the feedback power, so the baseline sits near the reference power
  (9.67 for a reference of 10 ucal/s), not at zero. Exothermic peaks point
  down.
- PEAQ-ITC: no public raw file found. Malvern's notes say PEAQ analysis
  reads `.itc`, but whether PEAQ writes the same layout, and whether it
  stores ucal/s or uW, is unverified (Q1).
- Integrated-heat exports are the other common input:
  - Origin `.DH`: line 3 is `T, cell mM, syringe mM, cell volume mL`, and
    rows from line 6 are `injection uL, heat ucal` (pytc `origin.py`);
  - NITPIC `.dat`;
  - the PEAQ/Origin table with `DH`, `INJV`, `Xt`, `Mt`, `XMt`, `NDH`. NDH
    is already normalised to kcal per mol of injectant.

**ITC references.**
- pytc (`harmslab/pytc`, Unlicense): one-site, binding polynomial,
  competitive and blank models, and the Origin `.DH` reader. It applies the
  overfilled-cell (displaced volume) correction with the product formula of
  Freire et al. 2009 (Meth Enzymol 455, 127). Origin and PEAQ use a
  first-order version (`Xt = X0 (v/V0)(1 - v/2V0)`, and the heat correction
  `dQ_i = Q_i + (dV_i/V0)(Q_i + Q_{i-1})/2 - Q_{i-1}`).
- NITPIC and SEDPHAT are free but closed source and not Python; we do not
  use them.

**31P solid-state data arrives as:**
- a Bruker experiment folder:
  - raw `fid` (often a Hahn echo for static samples);
  - or processed `pdata/1/1r` plus `procs`: int32 when DTYPP = 0, scaled by
    2^NC_proc, axis from OFFSET (ppm of the first, leftmost point), SW_p
    (Hz), SF and SI;
- a TopSpin `totxt` ASCII export (`# LEFT = ... ppm. RIGHT = ... ppm.`,
  `# SIZE = ...`, one intensity column; header wording to verify, Q2);
- a bare intensity column with no axis, which is what the user's
  `31P-siml/standard/31P_INP` is (4096 points; the axis comes from
  `settings.txt`: 242.938 MHz, DW 10 us);
- or a two-column CSV from MestReNova, with ppm descending.

The MAS rate is in acqus `MASR` (Hz) when the controller is connected. The
title often gives a rounded nominal rate.

**CSA conventions that decide right from wrong** (Mason 1993, Solid State
NMR 2, 285; Harris et al. IUPAC 2008):
- IUPAC / Herzfeld-Berger:
  - delta11 >= delta22 >= delta33;
  - delta_iso is their mean;
  - span Omega = delta11 - delta33 >= 0;
  - skew kappa = 3 (delta22 - delta_iso) / Omega, in [-1, 1].
- Haeberlen:
  - |delta_zz - delta_iso| >= |delta_xx - delta_iso| >= |delta_yy - delta_iso|;
  - reduced anisotropy delta = delta_zz - delta_iso, which has a sign;
  - eta = (delta_yy - delta_xx) / delta, in [0, 1].
- The "anisotropy" Delta_delta = delta_zz - (delta_xx + delta_yy)/2 is
  1.5 delta. Lipid people report "Delta_sigma" (the parallel minus
  perpendicular edge, about -45 ppm for fluid bilayers), which is the same
  quantity on the shielding scale.
- Shielding sigma and shift delta have opposite signs.
- A hexagonal (HII) phase scales the pattern by -1/2 (31P-siml: `-delta/4`
  vs `delta/2`).
- Herzfeld and Berger (J Chem Phys 73, 6021, 1980) express sideband
  intensities by mu = Omega nu0 / nu_r and rho = (delta11 + delta33 -
  2 delta22) / Omega.
- Only direct-polarisation spectra with a long recycle delay give
  populations; cross-polarisation (CP) intensities are not quantitative.

**31P references.**
- mrsimulator (BSD-3-Clause, maintained, Haeberlen zeta/eta, static and
  MAS). It needs numpy >= 2.2, so it runs only in a throwaway container.
- ssNake (GPL-3.0, static and MAS CSA fits, including Herzfeld-Berger
  style).
- nmrglue (BSD-3, in the sandbox): `bruker.read_pdata` checks our 1r/procs
  writer.
- The closed-form Bloembergen-Rowland static powder pattern (elliptic
  integrals) as a third check.
- The user's 31P-siml (AGPL-3.0, user-owned) for the
  lamellar/hexagonal/isotropic phase model with ellipsoidal vesicle
  deformation.
- None of these goes into the sandbox image.

## Suites and layout

Two suites, so the scorecards stay readable: **S9 ITC** and **S10 31P
solid state** (S7 is the viewer, S8 EPR; Q3). Generators
`fixtures/lab/generate/s9.py` and `s10.py`, wired into `build.py`, with
SHA256SUMS. Neither imports labkit: each is the independent reference a
later labkit round is tested against. Both follow `common.py`:
- seeded rngs;
- values written with fixed precision and read back before truths are
  computed;
- 6 significant digits.

Both copy the `VARIANT` / `_rng` / `_id` / `_p` mechanism from `s8.py` for
fresh sets.

**ITC generator.**
- Per injection: exact mass balance, a quadratic for one site and a scalar
  root solve for two independent sites. The overfilled-cell correction uses
  the exact product formula.
- Raw thermograms: DP(t) = reference power + slow drift + sum of q_i
  convolved with the injection duration and a first-order instrument
  response (tau about 12 s VP-ITC, 7 s iTC200), sampled every filter
  period, plus noise. Exothermic heats are negative peaks.
- The integral of each peak equals q_i by construction. The spacing is at
  least 10 tau, so truncation is below 1e-4.
- Files are written in the `.itc` layout above, with the unexplained fields
  copied from the real VP-ITC and iTC200 headers.

**31P generator.**
- Static patterns: numerical powder average (ZCW, about 20k orientations),
  convolved with a Lorentzian/Gaussian line.
- MAS: sideband intensities from the Fourier coefficients of exp(i Phi(t))
  over a rotor period, averaged over the powder (the exact Herzfeld-Berger
  result).
- Lipid phases: the 31P-siml model re-implemented.
- Written as Bruker processed folders (zipped, the same zip normaliser as
  `s2.py`), `totxt` ASCII, a bare column, or CSV.

## Cases

**S9 ITC** (9 cases; prompts ask for K in M^-1, dH and dG in kJ/mol unless
the trap is the unit itself):

| Case | Input | Trap |
|---|---|---|
| `s9_itc_vp_onesite` | VP-ITC `.itc` raw | Integrate the thermogram (baseline near the reference power, not 0). The 2 uL first injection delivered about 60% of nominal: drop it. Report n, K, dH, dG, -TdS. |
| `s9_itc_blank` | sample `.itc` plus ligand-into-buffer `.itc` | Heat of dilution. The titration does not reach a plateau, so the last points are not a blank. Subtract the blank per injection or fit an offset. |
| `s9_itc_temp_units` | iTC200 `.itc` at 15 C | dG and -TdS need T from the header (288.15 K, not 298.15). ucal to kJ/mol uses 4.184 J/cal. |
| `s9_itc_conc_override` | iTC200 `.itc` | The prompt gives an A280-corrected cell concentration that differs from the header by 15%. n and dH scale with it. |
| `s9_itc_displaced_volume` | iTC200, 19 x 2 uL into 200 uL | Ignoring the volume change biases n and dH by several percent. Origin's first-order form and the exact form both pass. |
| `s9_itc_low_c` | `.DH` export, c = 0.5 | The prompt states 1:1, so n is fixed at 1. K is reported with its interval. The answer flags that dH and n are not independently determined. |
| `s9_itc_high_c` | `.itc`, c about 3000 | n and dH are right, but K is only a lower bound: K must be null or flagged, and the answer suggests a displacement titration. |
| `s9_itc_two_site` | `.itc`, K1 1e8, K2 1e5 | A biphasic isotherm. One-site vs two independent sites by AIC/F test; K1, K2, dH1, dH2. |
| `s9_itc_reverse` | `.itc`, protein in the syringe | Reverse titration: the stoichiometry asked for (sites per protein) is 1/n of the fitted value. |

**S10 31P solid state** (10 cases; 31P at 202.4 MHz unless stated, shifts
vs 85% H3PO4):

| Case | Input | Trap |
|---|---|---|
| `s10_p31_lamellar` | Bruker `1r`/`procs` zip, static, fluid bilayer | Report Delta_sigma (edge to edge, about -45 ppm) and the Haeberlen delta = (2/3) Delta_sigma, keyed separately. The scaling is 2^NC_proc and the axis comes from OFFSET/SW_p. |
| `s10_p31_static_tensor` | TopSpin `totxt`, crystalline phosphate, eta 0.5 | Principal values in the IUPAC order, span and skew, and Haeberlen delta and eta. The ppm axis is descending, taken from LEFT/RIGHT. |
| `s10_p31_convention` | CSV, static, delta < 0 | The prompt says "anisotropy and asymmetry" with no convention. The answer must name the one it used (`numeric_convention` grader, below). Shielding vs shift sign. |
| `s10_p31_echo_fid` | Bruker raw `fid`, Hahn echo | Left-shift to the echo top (from D6 and the echo delay) before the FT, plus the digital filter. Without it the first-order phase roll ruins the edges. |
| `s10_p31_mas_hb` | Bruker `1r`, MAS, MASR = 4012 Hz | A Herzfeld-Berger fit of the sideband integrals. The centerband is not the tallest peak. Use MASR, not the title's "4 kHz". |
| `s10_p31_two_rates` | two ASCII spectra, 3.0 and 4.5 kHz, two sites | The centerband is the peak that does not move between rates. Report delta_iso of both sites. |
| `s10_p31_populations` | Bruker `1r`, MAS, direct polarisation, D1 = 5 T1 | Site populations sum the centerband and all its sidebands. Centerbands alone are off by more than 10 points. |
| `s10_p31_cp_quant` | Bruker `1r`, MAS, PULPROG `cp`, D1 3 s | The user asks for populations. The answer reports the ratio but must flag that CP intensities are not quantitative. |
| `s10_p31_fast_sign` | Bruker `1r`, 12 kHz MAS, +-1 sidebands, modest SNR | The sign of delta and eta are poorly determined. Graded: |delta| within tolerance, plus a flag (or interval) on the sign and eta. Kept only if the generator confirms the ambiguity (Q8). |
| `s10_p31_lipid_phases` | static, lamellar + HII + isotropic | HII is reversed in sign and half as wide. Report the three fractions and the lamellar Delta_sigma. |

Optional eleventh (Q4): `s10_p31_userdata`, the real `31P_INP` spectrum
from `varghele/31P-siml`. It is a bare column, so the axis must be built
from SF and DW, and it has ellipsoidal deformation. It has no analytic
truth: the reference is a fit with fresh code and wide tolerances, and it
is reported separately with a `ref:fit` tag.

## Truths and independent checks

Truths are the generating parameters, never a fit, as in S8. The exception
is the user-data case. Each generator also asserts two things for every
case, on 20 noise seeds of the reference analysis (our own fit in the
generator):
- **Truth inside**: the correct analysis lands inside the tolerance in at
  least 19/20 seeds.
- **Trap outside**: the trap analysis lands outside the tolerance in at
  least 19/20 seeds. Trap analyses are things like keeping the first
  injection, no blank, cal instead of J, 298 K, header concentration, no
  volume correction, centerband only, the wrong convention, or no echo
  shift.

So a tolerance can neither fail a right answer nor pass the trap. A case
whose noise breaks this gets less noise, not a wider tolerance after model
runs (the EPR rule).

Checks against references, in a throwaway container (never the sandbox
image):
- **ITC**
  - pytc fits the generated integrated heats (with the first injection
    dropped) and recovers n, K and dH within the tolerances.
  - `bayesitc`'s parser reads every generated `.itc` and returns our header
    values (injection volumes, concentrations, cell volume, temperature).
  - A second integrator, written separately (trapezoid with a linear
    baseline from the 30 s before each peak), returns q_i within 0.5%.
- **31P**
  - mrsimulator simulates the same tensors (static, and MAS at the case's
    rate). The normalised lineshape and sideband intensities agree within
    1% of the maximum.
  - The static axial and eta > 0 patterns also match the
    Bloembergen-Rowland closed form.
  - `nmrglue.bruker.read_pdata` returns our 1r and ppm axis.
  - 31P-siml (user code) reproduces the lipid-phase lineshape.
- **Format check against real files**: our `.itc` writer's output and the
  real `bayesian-itc` files must parse identically with both public
  parsers. Real files are read at development time only and never copied
  in (GPL-3.0 repository).

## Graders and tolerances

Existing graders cover almost all of this: `numeric` (with `"log": true`
for K), `numeric_map` (per-injection heats, per-site values), `ci_contains`,
`flagged`, `is_null`.

One new grader, in `graders.py` with offline tests: **`numeric_convention`**.
It reads `results[convention_key]`, maps it (`haeberlen`, `iupac`/`hb`,
`mehring`, `delta_sigma`) to the truth for that convention, and fails if the
key is missing or unknown. It is used only by `s10_p31_convention`.

Starting tolerances (confirmed by the 19/20 check, tightened where the
seeds allow):

| Quantity | Tolerance |
|---|---|
| log10 K (5 < c < 500) | atol 0.08 |
| log10 K (two-site, low c) | atol 0.15 |
| dH | rtol 5% (two-site 10%) |
| n | atol 0.03 |
| dG | atol 0.5 kJ/mol |
| -TdS | atol 1.0 kJ/mol |
| per-injection heats | rtol 3%, min_frac 0.9, first injection excluded |
| delta_iso | atol 0.3 ppm static, 0.1 ppm MAS |
| span, Delta_sigma, Haeberlen delta | rtol 4% (atol 2 ppm floor) |
| eta, kappa | atol 0.08 |
| phase fractions, populations | atol 0.05, 0.04 |

The traps sit well outside these:
- the 1.5 factor;
- the cal/J factor of 4.184;
- a centerband misidentified by nu_r / nu0 (about 20 ppm);
- 298 K vs 288 K: dG off by about 0.9 kJ/mol;
- the concentration override: 15%.

## What labkit would need (a later round, measured)

Nothing here changes labkit. The baseline arm runs on labkit 0.2.1, which
has:
- `nmr.read_bruker` (raw fid only, no pdata, no echo shift);
- `fit` with Morrison but no ITC model;
- no CSA tools.

A later round (labkit 0.3.0, its own plan after the baseline) would likely
add the following, then be shaped by what the baseline shows (EPR Q2):
- **`labkit.itc`**
  - `read_itc`: VP-ITC, iTC200 and PEAQ once verified; header fields with
    units; injections; thermogram.
  - `read_heats`: `.DH`, NITPIC `.dat`, the NDH table.
  - `integrate`: per-injection baseline, with a drift warning.
  - Models: `one_site` and `two_site` with the displaced-volume correction,
    blank and offset terms, drop-first by default with a note, fraction
    competent vs n.
  - `fit_itc` reporting the c-value with warnings (c < 1: fix n; c > 1000:
    K is a lower bound).
  - `thermo(K, dH, T_from_file)` in kJ/mol, and a reverse-titration flag.
  - `labkit.read` sniffs `$ITC`.
- **`labkit.ssnmr`** (or `nmr.csa`)
  - `tensor(...)` conversions between IUPAC/HB, Haeberlen, Mehring and
    Delta_sigma, sigma vs delta, with the convention always printed.
  - `static_powder`, `mas_sidebands`, `fit_static`, `fit_sidebands`: the
    last warns when +delta and -delta fit within delta chi^2 < 4.
  - `centerband(spectra_at_rates)` and `sideband_positions(masr, sf)`.
  - `lipid_phases` (lamellar, HII, isotropic, optional deformation).
- **`nmr` additions**: `read_bruker_pdata` (NC_proc, OFFSET axis),
  `read_topspin_txt`, `echo_shift`, and a CP-quantitation warning from
  PULPROG.
- Guide topics `itc` and `ssnmr` with examples that run as printed; domain
  hints (hand-written Wiseman fits, a hand-written "anisotropy" with no
  convention, `np.loadtxt` on a bare column with an invented axis); the
  persona's LAB DATA rule.
- Tests against pytc and mrsimulator in throwaway containers, as DeerLab was
  for EPR. pytc could be installed in the sandbox (Unlicense, small), but the
  default is our own code: the conventions go into our signatures, and the
  sandbox stays on numpy 1.26 (Q6).

## Fresh variants

`s9.VARIANT = 1` and `s10.VARIANT = 1` redraw the parameters (K, dH, n, c,
concentrations, delta, eta, rates, fractions) and change the noise seeds,
writing `_f1` cases into `fixtures/lab_fresh`. They are generated only
after the labkit round is frozen and are validated only with the references
and the 19/20 checks. No labkit run on them before the measured run.

## Order of work and cost

1. Generators, the `numeric_convention` grader, offline tests, determinism
   (SHA256SUMS).
2. Reference checks in throwaway containers (pytc, bayesitc parser,
   mrsimulator, Bloembergen-Rowland, nmrglue, 31P-siml), with the agreement
   recorded here.
3. A smoke run (1 case per suite, 1 rep), tagged `smoke`, not a result.
4. Baseline arm: 19 (or 20) cases x 3 reps = 57-60 runs on the eval
   instance at concurrency 4. By the LABKIT-015/016 rounds (42 runs, 2 to
   3 hours) that is about 3 to 4 hours, with egress off. Scorecard copied to
   `docs/paper-kit/scorecards/` in the same commit.
5. If the baseline leaves room: labkit 0.3.0 plan, build, labkit arm (same
   cost), fresh set (same cost again), paired McNemar.

The whole path costs about 10 to 12 eval-instance hours. GPU time only, no
Brave or S2 calls.

## Risks

- **`.itc` fields we cannot explain** (one `$` integer, two `#` values,
  most of the `%` block, six iTC200 columns): we copy them verbatim and no
  prompt depends on them. PEAQ-ITC is unverified (Q1); until a real file
  arrives, no case claims to be PEAQ.
- **Powder simulation cost in the sandbox**: a model writing its own powder
  average with a fine grid at 2 CPUs can hit the 30 s `run_python` default.
  The cases need only about 2k orientations for the tolerances; the
  generator records the time its reference fit takes.
- **Real thermograms have artifacts our synthetic ones lack** (spikes,
  baseline steps, bubbles). That is the held-out set's job
  (`LAB-DEFERRED.md` section 2), not this suite's.
- **The sign-ambiguity case may be either trivially solvable or truly
  unsolvable**: the generator measures delta chi^2 between the +delta and
  -delta fits, and the case is replaced if it is not ambiguous.
- **The user-data case has a fit-based truth**: it is reported separately
  and excluded from the paired headline.
- **Licences**: no GPL or AGPL code is copied into fixtures, labkit or the
  image. The bayesian-itc files are read, not committed. 31P-siml data
  enters under D9 only if the user agrees (Q4).

## Open questions (defaults taken unless you say otherwise)

| # | Question | Default |
|---|---|---|
| Q1 | Can the group share a few real `.itc` files (VP-ITC and especially PEAQ-ITC, one titration plus its blank) for a format check, kept outside git under `/opt/munin/data/evals/`? | Ask. Until then the format is checked against the public `bayesian-itc` VP-ITC and iTC200 files, and PEAQ is marked unverified. |
| Q2 | Same for 31P solid state: a Bruker folder (static lipid and one MAS) and a TopSpin `totxt` export? | Ask. Synthetic only until then; the `totxt` header wording stays unverified. |
| Q3 | Suite names S9 (ITC) and S10 (31P solid state), or one S9 with two families? | Two suites. |
| Q4 | Use `31P-siml/standard/31P_INP` (your AGPL repo) as a real-data case under D9, reported separately? | Yes, tagged `ref:fit`, outside the paired headline. |
| Q5 | Baseline arm before any labkit work? | Yes (as EPR Q2): it shows which traps the model actually falls into. |
| Q6 | Later labkit round: own implementation, or install pytc (Unlicense) in the sandbox? | Own code for both families; pytc and mrsimulator are test oracles only (mrsimulator needs numpy 2). |
| Q7 | Raw thermogram integration in scope, or integrated heats only? | Both: 8 cases start from raw `.itc` (one with its blank file), 1 from a `.DH` export. |
| Q8 | Keep the fast-MAS sign-ambiguity case, graded as magnitude plus a flag? | Yes, only if the generator confirms the ambiguity; otherwise replace it with a slow-spinning HB case. |
| Q9 | Volume-correction truth: exact product form (pytc, Freire 2009) or Origin's first-order form? | Exact; the tolerance passes both and fails no correction. |
| Q10 | Run two research agents (ITC, CSA lineshapes) with the EPR brief template before freezing the labkit API? | Yes, after the baseline, so they target the observed failures. |
