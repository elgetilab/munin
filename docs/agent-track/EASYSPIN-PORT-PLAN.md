# EasySpin capabilities labkit.epr lacks: plan

Status: IN PROGRESS 2026-11-23 ("proceed", slice 1, Q1-Q8 as answered). Parents: `EPR-PLAN.md` (S8
49/63 with labkit.epr vs baseline 17/63), `LABKIT-015-PLAN.md` (round 2,
targeted 17/21). Slice 1 is built and tested, not yet deployed; see
"Slice 1: what was built" at the end.

## Where labkit.epr stands (0.2.1)

It reads BES3T, ESP/WinEPR and ASCII files. For CW it does g-values, line
fits, double integrals and spin counts, nitroxide aN, Kivelson tau_c and
power saturation, and it simulates isotropic spectra (`simulate_iso`,
first order, optional second order for a single nucleus). For DEER it does
the one-step Tikhonov fit. Its docstring and `guide('epr')` say that slow
motion and powder spectra are out of scope ("use EasySpin chili, pepper").

Gaps found while reading:
- `EPR-PLAN.md` lists `fit_iso`, but `epr.py` has no fitting of a
  simulation at all. Only single lines (`fit_line`) and the nitroxide triplet
  are fitted.
- `simulate_iso` takes `lw_mT` as the derivative peak-to-peak width.
  EasySpin's `Sys.lw` is the FWHM and `Sys.lwpp` is peak to peak. A model
  that knows EasySpin will pass a FWHM. Any new API names the width by kind
  (`fwhm_mT`, `dBpp_mT`) and has no bare `lw`.
- Hyperfine values are in mT in labkit and in MHz in EasySpin. The new API
  names the unit in every keyword (`A_MHz=`, `A_mT=`) and has no bare `A`.

## Which EasySpin capabilities matter, in priority order

Typical users are biophysics and bioinorganic groups. They have nitroxide
spin labels (room-temperature tumbling and frozen solutions), Cu(II) and
other S = 1/2 metal centres in frozen solution, organic radicals, and
sometimes high-spin Fe(III), Mn(II) or triplets.

| # | Capability | EasySpin | Users | Effort | Priority |
|---|---|---|---|---|---|
| 1 | Least-squares fit of any labkit simulation to a spectrum (multistart, bounds, auto scale and baseline, CIs) | `esfit` | everyone who simulates | small | slice 1 |
| 2 | Powder (frozen solution) spectra for S = 1/2: rhombic g, any number of nuclei with anisotropic A (coaxial, or tilted by Euler angles), g/A strain, natural isotope mixtures | `pepper` (perturbation) | Cu(II), frozen nitroxides, radicals, low-spin Fe, Mo(V), V(IV) | medium | slice 1 |
| 3 | Unit conversions and an isotope table: mT/MHz/cm-1/1e-4 cm-1/G at a given g, g from field and frequency (exists), nuclear spin, gn, abundance | `unitconvert`, `mt2mhz`, `nucdata`, `isotopologues` | everyone | small | slice 1 |
| 4 | Exact isotropic positions (Breit-Rabi) for large couplings and several nuclei with natural abundance | `garlic` | radicals, 31P, isotropic Cu | small | slice 1 |
| 5 | Slow and intermediate motion of a nitroxide: stochastic Liouville equation, isotropic or axial diffusion, then an ordering potential and MOMD | `chili` | spin-label groups (most CW work at room temperature is in this regime) | large | slice 2 |
| 6 | Fast-motion linewidths from tau_c and the tensors (A + B mI + C mI^2 from theory) | `garlic` with `tcorr` | spin labels, the Kivelson regime done right | medium | slice 2 (validated against chili) |
| 7 | Full-matrix powder spectra (S > 1/2, zero-field splitting, triplets, coupled spins) | `pepper` (matrix), `resfields`, `eigfields` | high-spin Fe(III), Mn(II), Gd(III), triplets | large | slice 3, only if users ask |
| 8 | Pulse EPR (ESEEM, ENDOR, relaxation), magnetometry, MD trajectories | `saffron`, `salt`, `curry`, `cardamom` | few | large | out of scope |

**Recommended first slice: items 1 to 4.** They cover frozen-solution
work, which is most of what groups bring besides DEER. They are cheap enough
for the sandbox and can be validated without MATLAB (see Validation). Slow
motion (item 5) is the most-requested missing piece for spin labels, but it
is the largest piece of work. It gets its own slice, built on slice 1's
fitter.

## Slice 1 API (labkit 0.3.0)

All functions take field in mT and frequency in GHz, as the rest of
labkit.epr does.

```python
sys = epr.SpinSystem(g=[2.05, 2.05, 2.25],
                     nuclei=[epr.Nucleus("Cu", A_MHz=[50, 50, 550])],   # "Cu": 63Cu/65Cu mixture
                     fwhm_mT=1.0, shape="gaussian", g_strain=None, A_strain_MHz=None)
y = epr.simulate_powder(sys, field_mT, freq_GHz, harmonic=1, modamp_mT=None)
y = epr.simulate_iso(...)      # unchanged signature, plus exact=True (Breit-Rabi)
r = epr.fit_spectrum(field_mT, y, model="powder", start=sys,
                     vary={"g": [0.02, 0.02, 0.05], "A_MHz": [[20, 20, 50]], "fwhm_mT": 0.5},
                     freq_GHz=d.freq_GHz, max_time_s=20)
r.params, r.ci, r.fit, r.residual_rms, r.warnings; print(r)
epr.convert(550, "MHz", "1e-4 cm-1"); epr.convert(1.6, "mT", "MHz", g=2.006)
epr.isotope("63Cu") -> spin, gn, abundance; epr.Nucleus("14N", A_mT=[0.6, 0.6, 3.4])
```

- `simulate_powder`: second-order perturbation for S = 1/2 (Iwasaki), an
  orientation grid over the octant or hemisphere that the symmetry allows,
  intensities with the 1/g factor (Aasa and Vänngård), and broadening by
  histogram plus convolution. Strain broadens each orientation's lines.
  `method="exact"` diagonalises the Hamiltonian and finds the resonance fields
  for small systems (Hilbert dimension up to 64). It is the check that
  `perturb` uses to warn.
- `fit_spectrum`: built on `labkit.fit` and `scipy.optimize.least_squares`.
  The scale and a polynomial baseline are solved linearly at each step. It
  runs a few starts inside the `vary` ranges and stops at `max_time_s`,
  returning the best fit so far with a warning. CIs come from the Jacobian.
  `model` is "iso" or "powder" (and "slow" in slice 2), or a callable.
- Warnings, rather than silent choices:
  - An element name without a mass ("Cu") simulates the natural mixture and
    says so; "63Cu" is one isotope.
  - The sign of A is not determined by a CW spectrum.
  - The perturbation error is estimated (A/(g muB B), and the second-order
    shift against the field step). Above a threshold it says to use
    `method="exact"`.
  - The grid is not converged: the spectrum at half the grid differs by
    more than 1% of its maximum.
  - The linewidth is under 3 field steps.
  - Lines fall outside the sweep.
  - S > 1/2 raises an error that names slice 3, so labkit does not run a
    simulation that is silently wrong.
  - Fit warnings: a parameter at its bound; two parameters correlated above
    0.95 (g_perp with the linewidth, for example); starts that disagree
    (more than one minimum); a residual with structure (runs test).
- `guide("epr-sim")`: three recipes that run as printed: a Cu(II) frozen
  solution (g_par, g_perp, A_par in MHz and 1e-4 cm-1), a frozen nitroxide
  (Azz from the outer extrema, then refined by a fit), and an isotropic
  radical with two kinds of nuclei. `guide('epr')` stops saying "use
  EasySpin" for powder spectra and says it for what remains (slow motion
  until slice 2, S > 1/2).
- Hints (`labkit_hints.py`): EasySpin-style code (`Sys.g`, `Exp.mwFreq`,
  `pepper(`, `esfit(`, `import matlab`, `oct2py`) and hand-written powder
  loops (`np.linspace(0, np.pi` with a g-tensor expression) point to
  `simulate_powder` / `fit_spectrum`. The persona's LAB DATA rule names them.

## Slice 2 (labkit 0.4.0): slow motion and fast-motion widths

- `epr.simulate_slow(sys, field_mT, freq_GHz, tau_c_s=, diffusion="iso"|
  "axial", potential=None, momd=False)`, for S = 1/2 with up to one nucleus
  of I = 1 or 1/2 (nitroxides 14N and 15N). The stochastic Liouville equation
  is set up in a Wigner-function basis truncated by (L_even max, L_odd max,
  K max, M max), with defaults chosen from tau_c. The spectrum comes from
  Lanczos tridiagonalisation and a continued fraction, on scipy.sparse.
  Field sweeps use the frequency-to-field mapping that the papers give for
  g close to g_e. The function warns when that mapping is poor.
- It warns when the basis is not converged (the spectrum changes by more
  than 1% when each L max is raised by 2), and when tau_c is outside the
  model's range.
- `simulate_iso(..., tau_c_s=)` gets the fast-motion widths from the tensors
  (Redfield). `kivelson_tau` warns when the spectrum is slow-motion and names
  `fit_spectrum(model="slow")`.

## Algorithm sources

| Feature | Sources |
|---|---|
| Powder perturbation, S = 1/2 | Iwasaki, J. Magn. Reson. 16, 417 (1974) (second-order hyperfine); Weil and Bolton, *Electron Paramagnetic Resonance*, 2nd ed., Wiley 2007, ch. 4-5; Stoll and Schweiger, J. Magn. Reson. 178, 42 (2006) (EasySpin's design) |
| Intensities | Aasa and Vänngård, J. Magn. Reson. 19, 308 (1975) (1/g factor) |
| Orientation grids | Ponti, J. Magn. Reson. 138, 288 (1999); Wang and Hanson, J. Magn. Reson. A 117, 1 (1995) (SOPHE); Stoll and Schweiger, J. Magn. Reson. 163, 248 (2003) (interpolation) |
| Exact resonance fields | Belford, Belford and Burkhalter, J. Magn. Reson. 11, 251 (1973) (eigenfields); Stoll and Schweiger, Chem. Phys. Lett. 380, 464 (2003) (adaptive resonance fields) |
| Rhombic powder line shapes (test) | Kneubühl, J. Chem. Phys. 33, 1074 (1960) |
| Strain | Hagen, *Biomolecular EPR Spectroscopy*, CRC 2008 (g-strain); Pilbrow, *Transition Ion EPR*, OUP 1990 |
| Breit-Rabi | Breit and Rabi, Phys. Rev. 38, 2082 (1931); Weil and Bolton, App. C |
| Slow motion | Freed, Bruno and Polnaszek, J. Phys. Chem. 75, 3385 (1971); Meirovitch, Igner, Igner, Moro and Freed, J. Chem. Phys. 77, 3915 (1982); Moro and Freed, J. Chem. Phys. 74, 3757 (1981) (Lanczos); Schneider and Freed, Biol. Magn. Reson. 8, 1 (1989); Budil, Lee, Saxena and Freed, J. Magn. Reson. A 120, 155 (1996) (NLSL) |
| Fast-motion widths | Freed and Fraenkel, J. Chem. Phys. 39, 326 (1963); Atkins and Kivelson, J. Chem. Phys. 44, 169 (1966) |
| Isotope data | Stone, At. Data Nucl. Data Tables 90, 75 (2005) (moments); IUPAC isotopic abundances (2013) |

The paper references are from the agents' knowledge and get checked against
the papers before the code is frozen, as in EPR-PLAN's "Verify before
freezing".

## License: what porting EasySpin permits

- EasySpin is under the **MIT License**, "Copyright (c) 2006-2024 Stefan
  Stoll and other contributors". Sources: `LICENSE.md` in
  https://github.com/StollLab/EasySpin (checked 2026-11-21; GitHub reports
  SPDX `MIT`), and https://easyspin.org/documentation/installation.html
  ("published under the permissive MIT license"). The current release is
  6.0.15. It needs MATLAB R2021b or later and "does not run on Octave".
- MIT permits use, copying, modification, merging, publishing,
  distribution, sublicensing and sale. The one condition is that "the above
  copyright notice and this permission notice shall be included in all
  copies or substantial portions of the Software". There is no copyleft and
  no patent clause, and redistribution inside an Apache-2.0 project is
  allowed.
- **Porting algorithms** from papers, or reading EasySpin's code to
  understand it and then writing our own: no obligation. Algorithms and
  physical constants are not covered by copyright. We still credit EasySpin
  in docstrings and the guide (good practice and scientific citation: Stoll
  and Schweiger 2006).
- **Translating code** (a MATLAB function turned line by line into Python,
  or a copied data table such as an isotope list): allowed. Each such file
  then carries EasySpin's copyright and the MIT permission notice. labkit
  gets a `THIRD_PARTY_NOTICES` file with the full MIT text, and `bundle.py`
  must copy it into bundles next to labkit's Apache-2.0 `LICENSE`, because
  bundles vendor labkit's sources.
- **EasySpin's test data** (`tests/data/*.mat`, the stored reference
  spectra) are under the same MIT license. Vendoring them into labkit's
  tests needs the same notice.
- Default: write from the papers; read EasySpin only where a paper
  underspecifies a convention (basis indexing, normalisation, grid symmetry).
  Ship the notice anyway, because the isotope table and any closely
  followed routine make "substantial portions" likely. Plain naming:
  labkit's functions are "following EasySpin's conventions", never "EasySpin
  for Python".
- Other packages: EPRsim is GPLv3. It may be used as a black-box oracle in a
  throwaway container, but its code is not read or ported. MarS and EPRAYA
  are MIT, DeerLab and epyr-tools MIT, cwepr BSD.

## Validation

No MATLAB was found on hugin (`which matlab`, `module avail`). MATLAB is
not required for most checks, because of the first source below.

1. **EasySpin's own regression spectra, without MATLAB.** The EasySpin
   repository has 53 stored reference spectra for these functions in
   `tests/data/`: 31 pepper, 10 garlic, 12 chili, 916 KiB in all. They are
   MATLAB v5 `.mat` files, and `scipy.io.loadmat` reads them (checked on
   `chili_fast_appfield`, `pepper_iso_cu`, `garlic_simplest`). The input
   parameters for each one are in the matching `tests/<name>.m`. Each case's
   parameters get transcribed into a JSON next to the vendored `.mat`. Tests
   compare max-normalised spectra. The tolerance is 1e-3 where our method
   matches EasySpin's, and wider where it differs: EasySpin's pepper
   defaults to full matrix diagonalisation, ours to second-order
   perturbation, and the difference is checked against our `method="exact"`.
   The pepper cases outside slice 1 (non-equilibrium populations, triplets,
   frequency sweeps) are skipped and listed.
2. **Analytic limits.**
   - Isotropic tensors in `simulate_powder` give `simulate_iso` to 1e-6.
   - An axial g with no hyperfine follows the closed-form powder pattern,
     with turning points at h nu/(muB g_par) and h nu/(muB g_perp).
   - A rhombic g follows the elliptic-integral form (Kneubühl).
   - The double integral of a simulated spectrum equals the set area, with
     the 1/g factor stated.
   - Breit-Rabi reduces to first order for small A.
   - Grid convergence.
   - In slice 2: `simulate_slow` reaches `simulate_iso` with Redfield widths
     at tau_c <= 50 ps, and `simulate_powder` at tau_c >= 1 us.
3. **Other Python packages, as oracles in a throwaway container (never in
   the image).** MarS (`mars-epr`, MIT, needs torch, active 2026-11), EPRAYA
   (MIT, needs jax and numba, active 2026-10) and EPRsim (GPLv3, last
   release 2019). They are checked on Cu(II), a nitroxide and a rhombic case.
   No maintained Python package does slow motion, as far as the search
   found, so for slice 2 the only references are EasySpin's 12 chili spectra
   and the limits. NLSL (Freed lab, Fortran) is a possible extra; its
   license is unchecked.
4. **EasySpin itself**, if MATLAB is available (Q1): a script in
   `backend/sandbox/labkit/tests/easyspin/` makes reference spectra on our
   own grid of parameters. Its outputs are committed as `.npz` files. This
   matters most for chili, where 12 stored spectra are few.
5. **Real spectra** (Q2): Cu(II) and nitroxide files from the group, as
   held-out fit checks outside git, as Q4 of EPR-PLAN asked.

## New S8 cases

The cases are generated by `fixtures/lab/generate/s8.py` without labkit, as
now. For powder cases the generator gets its own simulator, written
separately: exact diagonalisation (`numpy.linalg.eigh` with root finding of
the resonance fields) on a dense grid. labkit's simulator uses perturbation,
so the two methods are independent. Truths are the generating parameters,
never a fit.

| Case | Input | Asks | Truth and tolerance | Trap |
|---|---|---|---|---|
| `s8_cw_cu_powder` | BES3T, Cu(II) frozen, natural 63/65Cu | g_par, g_perp, A_par in MHz and 1e-4 cm-1 | generating values; g 0.003, A 5% | A units; g_par is not a zero crossing; isotope mixture |
| `s8_cw_nitroxide_rigid` | ASCII, 77 K nitroxide | Azz (mT) | generating Azz, 0.08 mT | half the outer-extrema splitting is only roughly Azz; a fit refines it |
| `s8_cw_rhombic` | BES3T, low-spin S = 1/2 with g-strain | gx, gy, gz | 0.002 | the centre zero crossing is not giso |
| `s8_cw_radical_fit` | BES3T, isotropic, 2 x 14N + 4 x 1H | aN, aH (mT) | 0.005 mT | multiplet counting; a fit, not peak picking |
| `s8_cw_units` | prompt only, plus a spectrum at g = 2.10 | A in MHz from mT | exact conversion, 0.5% | converting with g_e instead of the spectrum's g |
| `s8_cw_slow_motion` (slice 2) | an EasySpin chili reference spectrum with known tau_c plus our noise, or generator output if Q1 gives MATLAB | tau_c, and a flag that Kivelson does not apply | tau_c 25%; keyword rubric | Kivelson outside its range |

Fresh variants (`VARIANT = 3`) are made after labkit 0.3.0 is frozen. The
chili-reference case cannot be redrawn without MATLAB, so it has no fresh
variant unless Q1 gives MATLAB.

Measurement as in EPR-PLAN: a baseline arm on labkit 0.2.1 first (it shows
whether the model writes its own powder loop and where that goes wrong),
then the labkit arm, 3 reps, paired McNemar, v2 results request. The
existing 21 S8 cases are rerun too, as a regression check.

## Performance in the sandbox

`run_python` defaults to a 30 s timeout. The maximum is
`SANDBOX_MAX_TIMEOUT_S` (120 s in the cluster compose file, 60 s in
`lite.yml`), and kernels share `SANDBOX_CPUS` (8). The S8 timings were
measured at 2 CPUs, and so are these. Budgets, to be measured while building:

- `simulate_powder`, perturbation, about 5000 orientations, 2048 points:
  under 0.5 s for Cu(II) and under 2 s for Cu with four 14N
  (324 lines per orientation).
- `method="exact"`: seconds for one nucleus. It is not the default and not
  used inside fits.
- `fit_spectrum` with the powder model: about 100-300 simulations, so under
  20 s with `max_time_s=20` (default) and best-so-far after that.
- `simulate_slow`: basis 200-2000, under 1 s per spectrum without MOMD. MOMD
  costs 10-20 times more. A slow-motion fit is the one task that needs
  `timeout_s` above 30, so the guide says to pass `timeout_s=110` and
  `max_time_s=90`. The fitter checks the clock between evaluations, so it
  never outlives the call.

## Risks

- **A plausible but wrong spectrum.** A mid-size model fits a powder model to
  a slow-motion spectrum (or the reverse) and reports confident numbers. The
  mitigations are the residual-structure warning, the guide's decision rule
  (room-temperature nitroxide: slow model; frozen: powder), and asking the
  user when the prompt does not say the temperature or state.
- **Non-unique fits.** g_perp and the linewidth trade off, and so do A_perp
  and the strain. The correlation warning and multistart disagreement show
  it; CIs are reported, not just values.
- **EasySpin conventions misread by the model.** FWHM against peak to peak,
  MHz against mT, the sign of A. Unit-named keywords, and errors for `lw=`
  or `A=`, catch these.
- **Slice 2 size.** The stochastic Liouville basis and the field-sweep
  mapping are where implementations go wrong. With only 12 stored chili
  spectra, slice 2 is weakly validated without MATLAB.
- **License hygiene.** A translated routine without the notice in bundles.
  The `THIRD_PARTY_NOTICES` check goes into the bundle test.
- **Scope creep** into pulse or S > 1/2: refused with a clear message until
  users ask.

## Open questions (defaults taken unless you say otherwise)

| # | Question | Default |
|---|---|---|
| Q1 | Can someone run MATLAB R2021b+ with EasySpin 6.0.15 (university licence, a group member's laptop) to make reference spectra? | Assume no. Validate slice 1 with EasySpin's 53 stored spectra, the analytic limits and MarS/EPRAYA. Ask again before slice 2, where it matters most. |
| Q2 | Can the group share real Xepr files (Cu(II) or another metal centre frozen, a frozen nitroxide, a room-temperature spin label), kept outside git under `/opt/munin/data/evals/`? | Ask; until then synthetic only. |
| Q3 | Scope of the first slice | Items 1-4: `fit_spectrum`, `simulate_powder` (S = 1/2, perturbation plus an exact check), `convert`/isotopes, Breit-Rabi in `simulate_iso`. Slow motion is slice 2, S > 1/2 slice 3. |
| Q4 | Port from papers, or translate EasySpin's MATLAB where convenient? | Papers first, EasySpin read for conventions. Ship EasySpin's MIT notice in labkit and bundles either way. |
| Q5 | Vendor EasySpin's 53 reference `.mat` files (916 KiB) into labkit's tests? | Yes, under `backend/sandbox/labkit/tests/data/easyspin/` with EasySpin's licence. Tests only; not in the sandbox image's package. |
| Q6 | Offer EasySpin-named aliases (`epr.pepper(Sys, Exp)`)? | No: labkit names with unit-named keywords, and a hint that maps EasySpin-style code to them. Aliases would invite EasySpin's units (MHz, FWHM) into labkit's mT conventions and suggest an affiliation. |
| Q7 | Should `fit_spectrum` pick powder or slow motion itself when the prompt does not say? | No (as for the DEER background): the agent takes it from the question (temperature, frozen or liquid) or asks the user. The function warns when the residual suggests the other model. |
| Q8 | Suite placement: new cases in S8, or a new suite? | S8, tagged `sim:powder` / `sim:slow`, so the EPR score stays one number with a sub-score. |

Answers (varghele, 2026-11-22): Q1 no, MATLAB is not available, so
validation is EasySpin's stored reference spectra, analytic limits and the
MIT Python simulators; slice 2 (slow motion) stays weakly validated unless
that changes. Q2 real Xepr files are still to be looked at together; synthetic
only until then. Other questions: defaults stand. Still waiting for
"proceed".

## Slice 1: what was built (2026-11-23, not deployed)

labkit 0.3.0, `labkit/epr_sim.py`, re-exported from `labkit.epr`:

- `isotope`, `known_isotopes`, `convert(value, from_unit, to_unit, g=None)`.
  Natural elements ("Cu") are the isotope mixture. A is given for the most
  abundant magnetic isotope and scaled by gn for the others.
- `Nucleus(name, A_MHz|A_mT, euler_deg, n)` and `SpinSystem(g, nuclei,
  fwhm_mT|dBpp_mT, shape, g_strain, A_strain_MHz)`, S = 1/2 only.
- `simulate_powder(..., method="hybrid"|"perturb"|"exact")`. The default,
  "hybrid", diagonalises each single nucleus with 2I+1 <= 8 exactly with the
  electron, which covers all orders, the nuclear Zeeman term and forbidden
  lines. Equivalent groups (n > 1) and cross terms between nuclei stay in
  second order.
  - Plain second order ("perturb") was 0.1-0.3 mT off for Cu(II) at X band
    (a third-order error, scaling as A^3). On the held-out Cu fixture, its
    chi2 at the true parameters was 12 times the exact value, and the fit
    moved g_par by 0.0046.
  - A warning estimates the hybrid error against full diagonalisation at 17
    orientations. Cu + 14N and Cu + 1H stay 2-3% off, and the warning fires
    for them.
- `fit_spectrum(model="powder"|"iso", vary=..., max_time_s=20)`. It scans and
  starts from several points with cheap second order on a coarse grid, then
  refines with hybrid. Axial parameters take [perp, par] ranges.
- `simulate_iso(exact=True)`: Breit-Rabi.
- `load_ascii` reads the frequency from a text header.
- `guide('epr-sim')` gives the recipes. The `THIRD_PARTY_NOTICES` file
  (EasySpin, MIT) ships in labkit and in every bundle.

Validation:
- Against the independent exact generator `s8_powder.py`, on synthetic
  systems that are not the eval fixtures (max-normalised difference, hybrid
  vs old perturb):
  - Cu axial 2.2e-4 vs 2.9e-1;
  - Cu rhombic 1.1e-3 vs 2.8e-1;
  - VO2+ 2.3e-4 vs 2.4e-1;
  - Cu + 14N 3.2e-2 vs 2.0e-1.
- Synthetic Cu fits recover g within 1e-4 and A_par within 0.05%, in 13-14 s
  at 2 CPUs.
- EasySpin's 41 stored spectra: every case is within its derived tolerance.
  The four spectra that EasySpin itself made by perturbation carry its own
  5-16% error.
- Timings at 2 CPUs, 2048 points:
  - Cu axial 0.05 s;
  - Cu rhombic natural 0.8 s, over the 0.5 s budget (kept for accuracy);
  - Cu + 4x14N 0.8-1.4 s.
- Tests:
  - labkit 100 passed and 3 xfailed in `munin-sandbox:r9-test`;
  - sandbox live 67/67, isolation 28/28, fighook passed;
  - retrieval hermetic 121/121.

Generator fix: `s8_powder.py`'s coarse grid (61 x 31) left 4e-3 of max (one
noise SD) on the Cu fixture. It is now 241 x 61. The Cu, nitroxide and
rhombic files were regenerated (truth unchanged), and the baseline was rerun
on them (`es1-baseline-regen`).

Each recipe in the guide, run by hand on the eval files, recovers the
answer:
- Cu g_par 2.2519 (truth 2.252), g_perp 2.054, A_par 551.7 MHz (552);
- nitroxide Azz 3.519 mT (3.52);
- rhombic g 2.418 / 2.253 / 1.914 (exact);
- pyrazine radical aN 0.7212, aH 0.2634;
- units 83.77.

Also new: retrieval hints `epr_easyspin` (EasySpin-style code mapped to
labkit names), `epr_powder` and `epr_iso_fit`. The persona's LAB DATA rule
names the new functions.

Baseline (deployed labkit 0.2.2):
- `es1-baseline`: 3/15 (units 3/3; Cu, nitroxide, rhombic and radical 0/3).
- `es1-baseline-regen`, the three regenerated cases x 3: 1/9 (nitroxide 1/3, Cu 0/3, rhombic 0/3), against 0/9 on the same cases before regeneration; failures are of the same kind (Cu A_par 280-1300 MHz, nitroxide Azz 2.70 mT, rhombic g off by up to 0.4).

Deployed 2026-11-23 (main 68b3f58). `es1-labkit`, the five cases x 3:
5/15 (Cu 2/3, units 3/3, nitroxide 0/3, rhombic 0/3, radical 0/3), paired
vs the baselines 2/1, p = 1.0. The recipes are right; the model does not
reach them (wrong hints fire, fits end at their bounds and the model
reports a typical value, API slips). Round 2 is `LABKIT-019-PLAN.md`.

Originally next: the user deploys the sandbox image and retrieval. Then the labkit-arm
run on the 5 cases x 3, an S8 regression rerun of the 21 older cases, and
fresh variants generated after this freeze.
