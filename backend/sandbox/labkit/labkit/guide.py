"""labkit.guide(topic): the API reference, one topic at a time."""
from __future__ import annotations

TOPICS: dict[str, str] = {}

TOPICS["overview"] = """\
labkit: vetted helpers for lab data analysis. Topics: guide('fit'), guide('read'),
guide('nmr'), guide('stats'), guide('cd'), guide('plot'), guide('epr'), guide('epr-sim'),
guide('deer').
- fit:   Prism-style nonlinear regression with Prism's parameter NAMES and defaults
- read:  instrument files (NanoDrop, Gen5, plate grids, JASCO, any table); refuses binaries
- nmr:   Bruker 1D processing, peaks, integration, Sparky lists, CSP, titration Kd,
         relaxation, NOESY buildup
- stats: two-way ANOVA (Type III), post-hoc on the pooled error, asterisks
- cd:    mean residue ellipticity, HT cutoff
- plot:  save_figure at exact mm size, seeded jitter
- epr:   CW-EPR: Bruker BES3T/ESP/ASCII files, g, linewidths, double integral, spin
         counting, nitroxide aN and tau_c, power saturation, isotropic simulation
- epr-sim: spin systems, powder (frozen solution) and isotropic simulation, fitting a
         simulation to a spectrum, unit conversions (MHz, mT, 1e-4 cm-1), isotopes
- deer:  4-pulse DEER: phase, zero time, one-step fit of P(r), modulation depth,
         background; distance limits of the trace
Every result says what it is: print() it. Warnings in a result are for the user:
report them in the answer, do not drop them.
"""

TOPICS["fit"] = """\
from labkit import fit, fit_global, compare, rout, interpolate, MODELS
r = fit(model, x, y, *, p0=None, replicates="all", weights=None, fixed=None, bounds=None,
        constants=None, x_scale=None, ci="wald", level=0.95)
  model: "linear", "3pl", "4pl", "5pl", "michaelis_menten", "competitive_inhibition",
         "morrison", "one_phase_decay", "one_phase_association", "two_phase_decay",
         or any f(x, a, b, ...) with p0.
  y: 1D, or 2D with ONE COLUMN PER REPLICATE (NaN = missing). replicates="all" fits
     every replicate as a point (Prism's default); "mean" fits the means.
  dose response (3pl/4pl/5pl): x_scale is REQUIRED and says what x IS, not the axis:
     "linear" if you pass the concentrations (labkit takes the log; zero doses raise:
     drop them and say so), "log" only if x is ALREADY log10(conc). A dilution series
     passed with "log" raises.
  5pl: report LogEC50 (derived, halfway between Bottom and Top), not LogXb.
  weights: None | "1/y^2" (relative) | "1/y" | array of SDs.
  fixed={"Bottom": 0}; bounds={"HillSlope": (0, None)}; morrison needs constants={"Et": ...}.
  competitive_inhibition: x=(S, I), two 1D arrays with one entry per measurement, y 1D.
     ONE fit over all inhibitor concentrations IS the global fit (Vmax, Km, Ki shared);
     no fit_global needed. From a long table (one row per substrate/inhibitor pair):
       d = read("inhibition.csv")
       r = fit("competitive_inhibition", (d["substrate_uM"].to_numpy(),
               d["inhibitor_uM"].to_numpy()), d["rate"].to_numpy())
       r.params["Vmax"], r.params["Km"], r.params["Ki"], r.ci["Ki"]   # Ki in I's units
     A wide table (one column per inhibitor concentration): melt it to long first.
r.params  {name: value} with Prism names; derived ones too (EC50, HalfLife, Span, Tau...)
r.ci      {name: (lo, hi)}  Wald = Prism's default asymptotic CI; ci="profile" on request
r.se, r.dependency, r.warnings (ambiguous fit, parameter at a bound, singular fit)
r.to_dict()  flat JSON: name, name_se, name_ci, n, dof, r2, sy_x, warnings
r.predict(x); r.bands(x, kind="confidence"|"prediction")
print(r)  shows each parameter with its DEFINITION. one_phase_decay: Y0 is the VALUE at
          X=0, Span = Y0 - Plateau is the drop. Report what the user asked for by name.
compare(simple_fit, complex_fit) -> extra sum-of-squares F test + AICc
rout(model, x, y, Q=0.01, **fit_kwargs) -> fit after ROUT outlier removal; r.outliers
interpolate(std_fit, {"S1": [0.41, 0.43], "S2": 0.9}) -> {name: X or None}; replicate
  readings are averaged first; a reading outside the standards' X range is None
  (no extrapolation), with the reason in out["warnings"].
fit_global(model, [(x1, y1), (x2, y2)], shared=["Kd"]) -> one shared value, others per set.
"""

TOPICS["read"] = """\
from labkit import read           # sniffs the format
df = read("file")                 # pandas DataFrame
df.attrs["meta"]      header fields (instrument, wavelength, date ...)
df.attrs["units"]     {column: unit} when the file states them
df.attrs["warnings"]  e.g. wells that read OVER (saturated): REPORT them
df.attrs["non_numeric"]  {column: {row: token}} cells that were not numbers (now NaN)
Specific readers: read_table (any delimited text: encoding, delimiter, decimal comma),
read_nanodrop (adds sample, conc_ng_ul, a260_280, a260_230), read_gen5 (time_min,
temperature_C, one column per well; block= for multi-read files), read_plate (96/384
grids from xlsx/text -> well,row,col,value), read_jasco (wavelength_nm, cd_mdeg, ht_v ...).
Binary instrument files (.pcrd, .sda, OLE/unknown) raise labkit.UnsupportedFormat with
what to export: tell the user that, do not decode bytes by guesswork.
Bruker NMR: labkit.nmr.read_bruker.
"""

TOPICS["nmr"] = """\
from labkit import nmr
1D, start to finish (read, process, reference, pick, integrate):
  raw  = nmr.read_bruker("inputs/bruker_1h")       # dir, zip, or a file inside the dir
  spec = nmr.process_1d(raw, autophase=True)       # or phase=(p0, p1); lb_hz=, zero_fill=
  spec = nmr.reference(spec, ppm=0.0)              # tallest peak near 0 -> 0.00 (DSS/TMS)
  pk   = [p for p in nmr.peaks(spec) if abs(p["ppm"]) > 0.1]       # drop the reference
  ints = nmr.integrate(spec, [(p["ppm"] - 0.1, p["ppm"] + 0.1) for p in pk],
                       relative_to=len(pk) - 1)    # relative to the last (highest-ppm) one
  shifts = {k: p["ppm"] for k, p in zip("abc", pk)}
  rel    = {k: i["relative"] for k, i in zip("abc", ints)}
raw.params   SFO1 (MHz), SW_ppm, SW_h (Hz), O1_Hz, O1_ppm, TD, GRPDLY, dtype, PULPROG ...
             SW is in ppm and SW_h in Hz: never use SW as a frequency.
Spectrum fields: spec.ppm (axis array), spec.real (intensities), spec.phase, spec.steps,
             spec.ref_offset_ppm. Nothing else: ppm range is spec.ppm.min(), spec.ppm.max().
nmr.peaks(spec, threshold=None, region=(lo, hi), min_rel_height=0.005)
       -> [{ppm, height, snr}] sorted by ppm; above baseline + 5x noise and 0.5% of the
       tallest peak (drops truncation ripples; min_rel_height=0 keeps weak peaks)
nmr.integrate(spec, [(a, b), ...], relative_to=k) -> [{region, integral, relative}]
Titrations:
nmr.read_sparky("L0.list")  -> {res: (1H ppm, 15N ppm)}, H FIRST, "G2N-H" -> "G2";
       columns told apart by their values; out.warnings counts unassigned peaks
nmr.csp(free, bound, alpha=..., scheme="williamson"|"euclidean")  {res: CSP or None}
       free/bound: {res: (1H ppm, 15N ppm)} as read_sparky gives them (the wrong order
       raises); alpha and scheme are REQUIRED; missing residues stay None
nmr.kd_fit(ligand_conc, {res: [csp...]}, protein_conc=P)  global Kd with ligand depletion
       -> {"Kd", "Kd_ci", "per_residue": {res: {"CSPmax", "CSPmax_ci"}}}
Relaxation:
nmr.relaxation_fit(delays_s, {res: [I...]}, kind="decay"|"inversion_recovery")
       -> {res: {"R", "R_err", "R_ci", "T", "I0"} or None}; delays in SECONDS
nmr.hetnoe(sat, ref, noise_sat=, noise_ref=) -> {res: {"hetNOE", "err"} or None}
NOESY buildup:
nmr.read_integrals("100ms.txt")  TopSpin integral list -> DataFrame (integral, f2_ppm,
       f1_ppm) in file order
nmr.noe_buildup(mixing_s, {pair: [A at each t]}, {pair or diagonal peak: A_ii(0)} or one number)
       A(t) = (A0/2)(1 - exp(-2 sigma t)) exp(-t/T), A(0) = 0 included, grid of starts
       -> {pair: {"sigma", "sigma_ci", "T", "T_ci", "warnings"} or None}; t in SECONDS
Per-residue and per-pair results are dicts keyed by residue or pair: write them that
way (one object per quantity, key -> value), not as lists.
"""

TOPICS["stats"] = """\
from labkit import stats
stats.anova2(df, dv, a, b)   Type III two-way ANOVA table (SS, df, MS, F, p, stars)
stats.posthoc(df, dv, a, b=None, compare="all"|"b_within_a"|"a_within_b",
              method="tukey"|"sidak"|"bonferroni")  on the pooled error term
stats.asterisks(p)  Prism GP style: ns, *, **, ***, ****
"""

TOPICS["cd"] = """\
from labkit import cd
cd.mean_residue_ellipticity(theta_mdeg, conc_mg_ml=, path_cm=, mw_da=, n_residues=)
   deg cm^2 dmol^-1 = mdeg * MRW / (10 * mg/mL * cm), MRW = MW/(N-1)
cd.ht_cutoff(wavelength_nm, ht_v, max_v=600)  shortest trustworthy wavelength: the last
   MEASURED wavelength (scanning down from the long end) before HT first exceeds
   max_v. Not interpolated between points.
"""

TOPICS["plot"] = """\
from labkit.plot import save_figure, jitter
save_figure(fig, "f.png", width_mm=85, height_mm=60, dpi=300)  exact size, no tight bbox
jitter(center, n, seed=0)  reproducible x scatter (an unseeded jitter makes a re-run
                           of the script produce a different figure)
"""


TOPICS["epr"] = """\
from labkit import epr          # field in mT, frequency in GHz, everywhere
DEER/PELDOR traces (time axis, distances): guide('deer'). This topic is CW.
d = epr.load("inputs/sample.DSC")   # Bruker BES3T (.DSC+.DTA), ESP (.par+.spc), ASCII (x_unit= if
                                    # the header has no unit). Converts G -> mT; print(d) shows it all
d.field_mT, d.y (complex if both channels: use d.real), d.freq_GHz (from MWFQ, the MEASURED
  frequency; never assume 9.5 GHz), d.power_mW, d.modamp_mT, d.gain_dB, d.n_scans, d.sctnorm
2D (power series, scans): d.y[i] is the slice at d.y2[i] (d.y2_unit, e.g. attenuation dB)
One line:   lf = epr.fit_line(d.field_mT, d.real, shape="lorentzian"|"gaussian"|"voigt",
                              freq_GHz=d.freq_GHz)
            lf.g, lf.B0_mT, lf.dBpp_mT (derivative peak-to-peak), lf.fwhm_mT (absorption FWHM:
            Lorentzian sqrt(3)*dBpp, Gaussian 1.1774*dBpp), lf.area
            epr.g_value(B_mT, freq_GHz) = 71.447735 * nu / B; epr.peak_to_peak, epr.zero_crossing
Spikes:     y_clean, idx = epr.despike(d.field_mT, d.real, max_width=3)   # 1-3 point glitches only
            never mask by amplitude (abs(y) > K): in a derivative spectrum the largest points are the line
Calibrate:  field_corr, offset = epr.calibrate_field(d.field_mT, B0_of_DPPH_mT, 2.0036, freq_GHz)
            epr.frequency_correct(field_mT, freq_GHz, target_GHz)   # scales, does not shift
Spins:      sc = epr.spin_count(sample, standard, standard_amount=100, unit="uM",
                                S_sample=0.5, S_standard=0.5)     # both EPRData from epr.load
            normalises each DOUBLE integral by 10^(gain_dB/20), sqrt(power), modulation amplitude
            (linear) and scans (unless SctNorm); print(sc) shows every factor
            epr.double_integral(field_mT, y, wings=0.1) baseline-corrects before AND after the
            first integration; the window must span 20+ linewidths (it warns)
Nitroxide:  nx = epr.nitroxide_hyperfine(d.field_mT, d.real, freq_GHz=d.freq_GHz)
            nx.aN_mT, nx.aN_MHz (half the OUTER-line spacing), nx.g_iso, nx.dBpp_mT[0],
            nx.heights[+1] (LOW field), nx.heights[0], nx.heights[-1] (HIGH field)
            epr.kivelson_tau(nx.dBpp_mT[0], nx.heights[0], nx.heights[1], nx.heights[-1])
Saturation: P = epr.attenuation_to_mW(d.y2, p_max_mW=200)
            A = [epr.peak_to_peak(d.field_mT, row)["Ypp"] for row in d.real]
            r = epr.power_saturation_fit(P, A); r.params["P_half"], r.params["epsilon"]
            (P_half = power at half saturation; not the older b-form P')
Text files (CSV/TXT/DAT export, field in G or mT): d = epr.load("inputs/spec.csv") reads the
            header unit (x_unit="G" if it has none) and gives d.field_mT, d.y, then as above:
  linewidth   epr.fit_line(d.field_mT, d.y, shape="gaussian").fwhm_mT   # not curve_fit by hand
  Kivelson    nx = epr.nitroxide_hyperfine(d.field_mT, d.y)
              k = epr.kivelson_tau(nx.dBpp_mT[0], nx.heights[0], nx.heights[1], nx.heights[-1])
              k["tau_c_s"] (dBpp in mT; heights from the raw derivative, no smoothing)
  spin ratio  epr.double_integral(a.field_mT, a.y).value / epr.double_integral(b.field_mT, b.y).value
              (same settings and S; otherwise spin_count normalises gain, power, modamp, scans)
Simulate:   epr.simulate_iso(field_mT, freq_GHz, g, nuclei=[(1, 1.6, 1)], lw_mT=0.12,
                             linewidth_mI=(A, B, C), second_order=True, modamp_mT=0.1)
            (lw_mT here is the derivative peak-to-peak width)
Powder (frozen solution), fitting a simulation, units, isotopes: guide('epr-sim').
Not in labkit yet: slow-motion spectra (room-temperature spin labels outside the Kivelson
regime, EasySpin chili) and S > 1/2 (high-spin Fe(III), Mn(II), triplets): say so, do not
fit a powder or isotropic model to them.
"""

TOPICS["epr-sim"] = """\
from labkit import epr      # field mT, frequency GHz; conventions follow EasySpin (Stoll 2006)
Units are in the keyword names; there is no bare lw= or A= (EasySpin's Sys.lw is a FWHM,
Sys.lwpp peak to peak, Sys.A in MHz). Hyperfine goes on a Nucleus, not on the SpinSystem.
d = epr.load(path): d.field_mT (alias d.field), d.real / d.y (alias d.signal), d.freq_GHz
sys = epr.SpinSystem(g=[gx, gy, gz],                  # or g=2.005, or [g_perp, g_par]
                     nuclei=[epr.Nucleus("63Cu", A_MHz=[Ax, Ay, Az])],   # or A_mT=, n=2 for
                     fwhm_mT=1.0, shape="gaussian",     # equivalent nuclei, euler_deg=(a, b, c)
                     g_strain=None, A_strain_MHz=None)  # strains as FWHM; dBpp_mT= instead of fwhm
"Cu" (no mass) = natural 63Cu/65Cu: A is given for 63Cu (most abundant), 65Cu scaled by gn.
y = epr.simulate_powder(sys, d.field_mT, d.freq_GHz, harmonic=1, modamp_mT=None)
    S = 1/2; default method "hybrid" (each nucleus diagonalised exactly); method="exact"
    diagonalises the whole system (slower).
    It prints warnings (perturbation error, grid, linewidth under 3 field steps, lines outside
    the sweep): report them. S > 1/2 raises an error.
y = epr.simulate_iso(field_mT, freq_GHz, g, nuclei=[epr.Nucleus("14N", A_mT=0.72, n=2)],
                     lw_mT=0.05, exact=True)     # Breit-Rabi positions for large couplings
Fit: r = epr.fit_spectrum(d.field_mT, d.real, model="powder" | "iso", start=sys,
                          freq_GHz=d.freq_GHz, modamp_mT=d.modamp_mT)     # vary optional
     vary left out: g +-0.05, A +-50% (at least 50 MHz), width +-80%, strains 0-0.02.
     Or vary={"g": [0.02, 0.05], "A_MHz": [30, 100], "fwhm_mT": 0.5}: +- half-ranges shaped like
     the start (0 = fixed; one nucleus may be a flat list; one number = every component).
     A parameter that ends at the edge of its range: fit_spectrum moves the range and refits by
     itself (r.notes says so). If r.warnings still says "ended at its bound", the start was far
     off: estimate it from the spectrum (below) and refit. Never report a bound, a typical or a
     literature value in place of a fitted one.
     print(r); r.params, r.ci (95%), r.fit, r.residual_rms, r.warnings, r.notes
     Warnings mean: two parameters correlated (g_perp vs linewidth: report both with CIs), other
     starts disagree (more than one minimum), structure in the residual (wrong model: slow
     motion, a second species, or missing g strain in a frozen metal spectrum).
Start values from the spectrum (estimates to start a fit, not results):
     st = epr.powder_starts(d.field_mT, d.real, d.freq_GHz, kind="cu" | "nitroxide" | "rhombic",
                            modamp_mT=d.modamp_mT)
     print(st); st.values
     "cu": g_perp near the strong perpendicular feature, g_par and A_par from the low-field
           parallel lines (their spacing, and a grid of quick simulations scored on them)
     "nitroxide": Azz = half the outer-extrema splitting (first maximum to last minimum)
     "rhombic": g_max at the first maximum, g_mid at the central zero crossing, g_min at the
           last minimum
     fit_spectrum also reads these off the spectrum itself for an axial Cu or a nitroxide start.
Units: epr.convert(552, "MHz", "1e-4 cm-1"); epr.convert(2.85, "mT", "MHz", g=2.10)
       field <-> energy needs the g of THAT line (never g_e unless g is 2.0023); no g, no answer
epr.isotope("63Cu") -> .spin, .gn, .abundance
Recipes (they run as printed on a BES3T or text file loaded as d = epr.load(...)):
  Cu(II) frozen solution (axial: two values each, [perp, par]; "Cu" = natural 63Cu/65Cu):
      st = epr.powder_starts(d.field_mT, d.real, d.freq_GHz, kind="cu", modamp_mT=d.modamp_mT).values
      sys = epr.SpinSystem(g=[st["g_perp"], st["g_par"]],
                           nuclei=[epr.Nucleus("Cu", A_MHz=[40, st["A_par_MHz"]])],
                           fwhm_mT=1.0, g_strain=[0.004, 0.004, 0.01])   # parallel lines broader
      r = epr.fit_spectrum(d.field_mT, d.real, model="powder", start=sys, freq_GHz=d.freq_GHz,
                           modamp_mT=d.modamp_mT)
      print(r)   # report g_par, g_perp, |A_par| in MHz and in 1e-4 cm-1 (epr.convert), with CIs
    g_par is the centre of the parallel lines, NOT a zero crossing.
  Frozen nitroxide (rigid limit, 77 K): Azz is NOT what nitroxide_hyperfine gives (that is the
    tumbling aN); half the outer-extrema splitting is the start, the fit refines it:
      st = epr.powder_starts(d.field_mT, d.real, d.freq_GHz, kind="nitroxide").values
      sys = epr.SpinSystem(g=[2.0085, 2.0060, 2.0022],
                           nuclei=[epr.Nucleus("14N", A_mT=[0.6, 0.6, st["Azz_mT"]])], fwhm_mT=0.6)
      r = epr.fit_spectrum(d.field_mT, d.real, model="powder", start=sys, freq_GHz=d.freq_GHz,
                           modamp_mT=d.modamp_mT)
      print(r.params["A_mT(14N)_z"])
  Rhombic S = 1/2 (low-spin heme): turning points give the starts, the fit gives the values:
      st = epr.powder_starts(d.field_mT, d.real, d.freq_GHz, kind="rhombic").values
      sys = epr.SpinSystem(g=[st["g_min"], st["g_mid"], st["g_max"]], fwhm_mT=1.0,
                           g_strain=[0.02, 0.01, 0.02])
      r = epr.fit_spectrum(d.field_mT, d.real, model="powder", start=sys, freq_GHz=d.freq_GHz,
                           modamp_mT=d.modamp_mT)
  Isotropic radical, several nuclei: fit_spectrum(model="iso") with one Nucleus per kind
    (n = how many are equivalent); fit the whole spectrum, do not count peaks:
      sys = epr.SpinSystem(g=2.0035, nuclei=[epr.Nucleus("14N", A_mT=0.7, n=2),
                           epr.Nucleus("1H", A_mT=0.3, n=4)], dBpp_mT=0.05, shape="lorentzian")
      r = epr.fit_spectrum(d.field_mT, d.real, model="iso", start=sys, freq_GHz=d.freq_GHz)
"""

TOPICS["deer"] = """\
from labkit import epr          # time in us, distance in nm, P in 1/nm
d = epr.load("inputs/deer.DSC")          # or a CSV/ASCII trace (x_unit="ns" if not in the header)
V = epr.average_scans(d.y) if d.y.ndim == 2 else d.y     # 2D BES3T: one row per scan
r = epr.fit_deer(d.t_us, V)              # phases complex V, finds the zero time, fits P(r),
print(r)                                 # lambda, background (and t0) in ONE step
r.mean_nm, r.std_nm (std, not FWHM), r.modal_nm, r.populations (modes with fractions),
r.lam (= r.mod_depth), r.k_per_us, r.conc_uM (3D background), r.t0_ns, r.phase_deg, r.warnings
r.main_nm: the main distance of the reliable part (when the trace is too short for a long
         component, from a fit allowed past the limit; summary() says "main distance (reliable
         part)"): report it as the main distance
r.r_nm, r.P: the distribution; r.summary(): the block a report needs (white paper 2021)
Background: the sample decides, not the fit (both often fit equally well):
         frozen solution: background="hom3d" (default; gives r.conc_uM)
         membrane, liposome, micelle, bicelle: background="strexp", dim=2.0 (spins in a plane)
         crowded or unknown geometry: background="strexp" (d fitted, 1-3)
         If the question does not say, ask the user; fit_deer warns when the choice matters.
Options: method="gauss", n_gauss=2 (parametric) when the trace is short or a model-free P(r)
         is too broad;
         n_boot=100 for 95% CIs (r.ci, r.P_ci95)
Never take |V|; never subtract the background (fit_deer divides, in one step).
The trace length limits what it can measure: epr.distance_limits(tmax_us) -> mean distance up
to 3.73*tmax^(1/3) nm, width/shape up to 2.96*tmax^(1/3) nm. fit_deer limits P(r) to that
range and WARNS when the trace seems to hold longer distances: report that as unreliable.
Labelling efficiency: f = epr.labeling_efficiency(r.lam, inversion=p, scheme="both_equal")
         p = pump inversion efficiency (from the question or the spectrometer, not assumed);
         "both_equal": both sites labelled alike, f = lambda / p;
         "one_full": site 1 fully labelled, f = lambda / (2p - lambda). Prints the formula.
         lambda is r.lam from fit_deer, never 1 - V(end)/V(0) (that includes the background)
Concentration (frozen solution): r.conc_uM from the hom3d rate, no hand fit of the decay.
epr.simulate_deer(t_us, r_nm, P, lam, conc_uM=..., phase_deg=..., noise=...) for checks.
"""


def guide(topic: str = "overview") -> None:
    """Print the labkit reference for one topic (default: the overview)."""
    key = (topic or "overview").lower().replace("_", "-")
    if key not in TOPICS:
        print(f"unknown topic {topic!r}; topics: {', '.join(TOPICS)}")
        return
    print(TOPICS[key])
