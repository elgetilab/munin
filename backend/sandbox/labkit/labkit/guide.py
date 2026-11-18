"""labkit.guide(topic): the API reference, one topic at a time."""
from __future__ import annotations

TOPICS: dict[str, str] = {}

TOPICS["overview"] = """\
labkit: vetted helpers for lab data analysis. Topics: guide('fit'), guide('read'),
guide('nmr'), guide('stats'), guide('cd'), guide('plot').
- fit:   Prism-style nonlinear regression with Prism's parameter NAMES and defaults
- read:  instrument files (NanoDrop, Gen5, plate grids, JASCO, any table); refuses binaries
- nmr:   Bruker 1D processing, peaks, integration, Sparky lists, CSP, titration Kd,
         relaxation, NOESY buildup
- stats: two-way ANOVA (Type III), post-hoc on the pooled error, asterisks
- cd:    mean residue ellipticity, HT cutoff
- plot:  save_figure at exact mm size, seeded jitter
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
nmr.noe_buildup(mixing_s, {pair: [A at each t]}, {pair: A_ii(0)} or one number)
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


def guide(topic: str = "overview") -> None:
    """Print the labkit reference for one topic (default: the overview)."""
    key = (topic or "overview").lower()
    if key not in TOPICS:
        print(f"unknown topic {topic!r}; topics: {', '.join(TOPICS)}")
        return
    print(TOPICS[key])
