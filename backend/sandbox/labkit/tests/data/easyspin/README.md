# EasySpin reference spectra (pepper, garlic)

Test data only. These files validate labkit's powder and isotropic CW EPR
simulators (`labkit.epr.simulate_powder`, `labkit.epr.simulate_iso`) in
`../../test_easyspin_refs.py`. They are not part of the labkit package and
are not installed into the sandbox image (the Dockerfile installs only the
`labkit` package directory).

## Source

- Repository: https://github.com/StollLab/EasySpin (branch `main`)
- Commit: `4d3446455c53db7e404778dd0ee260a239175bca` (committed 2026-11-04)
- Fetched: 2026-11-23, with `gh api repos/StollLab/EasySpin/contents/...`
- Files: all 41 `tests/data/pepper_*.mat` (31) and `tests/data/garlic_*.mat`
  (10), byte-identical copies, names unchanged. 653,380 bytes in all.
- License: MIT, "Copyright (c) 2006-2024 Stefan Stoll and other
  contributors". The full text is in `LICENSE` here (EasySpin's
  `LICENSE.md` at that commit) and applies to the `.mat` files and to the
  parameter transcriptions in the `.json` files and the MATLAB lines quoted
  in them.

## Per-case JSON

Each `<case>.json` holds the parameters transcribed from EasySpin's
`tests/<case>.m` at the same commit:

- `sys`: S, g principal values (EasySpin's 2-vector `[g_perp g_par]` is
  expanded to xyz), nuclei with isotope name, I, A in MHz (EasySpin's unit,
  principal xyz), A frame, number of equivalent nuclei; line widths both as
  FWHM and as derivative peak-to-peak (EasySpin: scalar = Gaussian, 2-vector
  = [Gaussian, Lorentzian], `lwpp = lw / sqrt(2 ln 2)` Gaussian and
  `lw / sqrt(3)` Lorentzian); g strain FWHM.
- `exp`: microwave frequency (GHz), field range and points (`CenterSweep`
  converted to a range), harmonic, ModAmp (peak-to-peak, mT).
- `opt`: EasySpin's method (pepper default `matrix`, `perturb` =
  second order; garlic default `exact` = Breit-Rabi) and grid size.
- `mat`: what the `.mat` holds (struct `data`, field names, shapes) and,
  for in-slice cases, which variable is the field axis and which the spectrum.
- `comparison.field_offset_steps`: see below.
- `easyspin_check`: EasySpin's own regression check line(s) for the case
  (its normalisation: `y/max(y)` with an absolute tolerance, or a relative
  tolerance on the raw spectrum).
- `matlab_source`: the parameter lines of the `.m`, verbatim.
- `note`: conventions, values the `.m` computes in code (evaluated), and
  independent checks.

Conventions worth knowing before comparing:

- Element-only nucleus names (`Cu`, `N`) are natural mixtures; EasySpin's
  `Sys.A` is for the most abundant magnetic isotope (63Cu, 14N), and the
  other isotopes get A scaled by the gn ratio. `(63,65)Cu` with
  `Sys.Abund = [1 1]` is a 50/50 mixture with A for 63Cu.
- If `Sys.g` is absent, g = gfree = 2.00231930436092.
- **Axis offset in pepper.** pepper's projection step (no strains) returns
  `ref[i] ~ true(x[i] + dx/2)`. An independent converged powder sum matched
  the g-only cases best at `x + 0.5*dx` (residual 4e-4 to 9e-3 of max, versus
  3e-2 to 2.7e-1 at `x`). The g-strain case (summation path) matched at `x`.
  The test evaluates labkit at `x + field_offset_steps * dx`. The hyperfine
  cases use 0.5 by the same code path; that was not checked independently.
- **Grid snapping in garlic.** garlic (outside fast motion) bins each line to
  the nearest grid point (`makespec(..., 'round')`) before convolving. An
  independent exact simulation with that snapping reproduces
  `garlic_simplest`, `garlic_manynuclei` and `garlic_modamp` to 1e-11 of max;
  without it (what an analytic simulator produces) the residuals are 2.6e-2,
  1.4e-1 and 4.4e-3. That is the floor for those tolerances.
- `pepper_photoselection_regression.mat` is a MATLAB v7.3 (HDF5) file:
  `scipy.io.loadmat` cannot read it, `h5py` can. It is outside slice 1.

## Cases

15 of 41 are in slice 1 (S = 1/2 powder with second-order perturbation or
exact diagonalisation; isotropic with Breit-Rabi).

| case | in_slice1 | what it tests | skip_reason |
|---|---|---|---|
| garlic_dispersion0 | no | dispersion lineshape via Hilbert transform, harmonic 0 | dispersion signal (Exp.mwPhase = pi/2); the slice-1 API has no detection phase |
| garlic_dispersion1 | no | dispersion lineshape via Hilbert transform, harmonic 1 | dispersion signal (Exp.mwPhase = pi/2); the slice-1 API has no detection phase |
| garlic_dispersion2 | no | dispersion lineshape via Hilbert transform, harmonic 2 | dispersion signal (Exp.mwPhase = pi/2); the slice-1 API has no detection phase |
| garlic_fastmotion_multinuc | no | fast-motion regime with 14N + 1H, tcorr 1 ns | fast-motion linewidths from Sys.tcorr (slice 2, not slice 1) |
| garlic_fastmotion_tcorr | no | nitroxide fast-motion regime, tcorr 2 ns | fast-motion linewidths from Sys.tcorr (slice 2, not slice 1) |
| garlic_freqsweep_exact | no | frequency-swept isotropic 1H doublet, A = 200 MHz, Method exact (default) | frequency sweep (Exp.Field fixed, Exp.mwRange swept) |
| garlic_freqsweep_pt2 | no | frequency-swept isotropic 1H doublet, A = 200 MHz, Method perturb2 | frequency sweep (Exp.Field fixed, Exp.mwRange swept) |
| garlic_manynuclei | yes | isotropic, 5 equivalent 1H + 4 equivalent 14N, Lorentzian, Breit-Rabi on equivalent-nucleus F spins |  |
| garlic_modamp | yes | single line, no nuclei, Gaussian, overmodulation (ModAmp 5 mT vs dBpp 0.5 mT) |  |
| garlic_simplest | yes | isotropic S=1/2, one 1H, narrow Lorentzian, Breit-Rabi (exact) positions |  |
| pepper_axiallw | yes | axial g, no nuclei, Gaussian convolution |  |
| pepper_crystal_multiori | no | single crystal, several orientations, Exp.CrystalSymmetry D2h, Sys.gFrame | crystal / orientation selection |
| pepper_crystal_th | no | single crystal with Th site symmetry, Sys.gFrame | crystal / orientation selection |
| pepper_dstraincorrelated | no | S = 1 with correlated D/E strain | S > 1/2, zero-field splitting, D strain |
| pepper_eespins | no | two coupled S = 1/2 electrons (Sys.ee), lwAnisoG | coupled electron spins |
| pepper_freqsweep_autorange | no | frequency sweep with automatic range; also stores the integral | frequency sweep |
| pepper_freqsweep_basic | no | frequency sweep, rhombic g, lwpp in MHz | frequency sweep |
| pepper_freqsweep_gstrain | no | frequency sweep with g strain | frequency sweep |
| pepper_gstrain | yes | rhombic g with g strain only (no convolution width) |  |
| pepper_iso_12c | yes | axial g with a spin-0 nucleus (12C): must equal the bare g spectrum |  |
| pepper_iso_cu | yes | axial g and A, natural-abundance Cu (63Cu + 65Cu), Method matrix |  |
| pepper_iso_customabund | yes | custom 50/50 63Cu/65Cu mixture, perturbation, fine axis |  |
| pepper_iso_twonucs | yes | natural Cu + natural N, perturbation, fine axis |  |
| pepper_modamp | yes | axial g, no nuclei, overmodulation (ModAmp 5 mT) |  |
| pepper_multidstrain | no | two coupled S = 1 spins, two D strains | S > 1/2, zero-field splitting, coupled electron spins |
| pepper_noneqpop_ISC_axialZFS | no | ISC triplet, axial ZFS, user density matrix; field and frequency sweep | S > 1/2, zero-field splitting, non-equilibrium populations, frequency sweep |
| pepper_noneqpop_ISCtriplet | no | ISC triplet, zero-field populations; field and frequency sweep | S > 1/2, zero-field splitting, non-equilibrium populations, frequency sweep |
| pepper_noneqpop_highfield | no | triplet, high-field T0 populations; field and frequency sweep | S > 1/2, zero-field splitting, non-equilibrium populations, frequency sweep |
| pepper_noneqpop_scrp_HF | no | spin-correlated radical pair, with and without 1H | coupled electron spins, non-equilibrium populations |
| pepper_noneqpop_tripletpair | no | triplet pair S = [1 1] vs quintet S = 2 from singlet fission | S > 1/2, zero-field splitting, coupled electron spins, non-equilibrium populations |
| pepper_noneqpop_zerofield | no | triplet, zero-field populations; field and frequency sweep | S > 1/2, zero-field splitting, non-equilibrium populations, frequency sweep |
| pepper_ordering_axial | yes | axial g, no nuclei, absorption (harmonic 0); row 0 only (isotropic powder) |  |
| pepper_photoselection_regression | no | photoselected triplet (Sys.tdm, Exp.lightBeam), parallel and perpendicular light | S > 1/2, zero-field splitting, non-equilibrium populations, photoselection (orientation selection) |
| pepper_pt_donly | no | S = 1, D = 600 MHz, perturbation | S > 1/2, zero-field splitting |
| pepper_pt_ga1 | yes | axial g + one 63Cu, second-order perturbation |  |
| pepper_pt_ga2 | yes | axial g + 63Cu + 1H, second-order perturbation |  |
| pepper_pt_gda | no | S = 1 with D, rhombic g and 1H, perturbation | S > 1/2, zero-field splitting |
| pepper_pt_gdonly | no | S = 3/2 with D, axial g, perturbation | S > 1/2, zero-field splitting |
| pepper_pt_gonly | yes | rhombic g, no nuclei, perturbation |  |
| pepper_rhombiclw | yes | rhombic g, no nuclei, Gaussian convolution |  |
| pepper_temperature | no | S = 1 with D, Boltzmann populations at 20..0.2 K (7 rows) | S > 1/2, zero-field splitting (temperature dependence) |
