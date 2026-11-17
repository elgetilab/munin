"""labkit.guide(topic): the API reference, one topic at a time."""
from __future__ import annotations

TOPICS: dict[str, str] = {}

TOPICS["overview"] = """\
labkit: vetted helpers for lab data analysis. Topics: guide('fit'), guide('read'),
guide('nmr'), guide('stats'), guide('cd'), guide('plot').
- fit:   Prism-style nonlinear regression with Prism's parameter NAMES and defaults
- read:  instrument files (NanoDrop, Gen5, plate grids, JASCO, any table); refuses binaries
- nmr:   Bruker 1D processing, integration, CSP, titration Kd, relaxation
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
  competitive_inhibition: x=(S, I) two arrays, y 1D.
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
raw = nmr.read_bruker("bruker.zip" or "expdir")   # dtype/byte order from acqus (DTYPA, BYTORDA)
raw.params   SFO1 (MHz), SW_ppm, SW_h (Hz), O1_Hz, O1_ppm, TD, GRPDLY, dtype, PULPROG ...
             SW is in ppm and SW_h in Hz: never use SW as a frequency.
spec = nmr.process_1d(raw, phase=(p0, p1))   # or autophase=True; lb_hz=, zero_fill=
       digital filter removal, FT, phase, ppm axis from acqus; spec.steps lists all
spec = nmr.reference(spec, ppm=0.0)          # tallest peak near 0 -> 0.00 (DSS/TMS);
       near=... to search elsewhere, window= in ppm
nmr.peaks(spec, threshold=None, region=(lo, hi))  -> [{ppm, height, snr}]
nmr.integrate(spec, [(a, b), ...], relative_to=k) -> [{region, integral, relative}]
nmr.csp(free, bound, alpha=..., scheme="williamson"|"euclidean")  {res: CSP or None}
       free/bound: {res: (1H ppm, 15N ppm)}, H FIRST (Sparky lists give 15N first: swap them;
       the wrong order raises); alpha and scheme are REQUIRED, missing residues stay None
nmr.kd_fit(ligand_conc, {res: [csp...]}, protein_conc=P)  global Kd with ligand depletion
       -> {"Kd", "Kd_ci", "per_residue": {res: {"CSPmax", "CSPmax_ci"}}}
nmr.relaxation_fit(delays_s, {res: [I...]}, kind="decay"|"inversion_recovery")
       -> {res: {"R", "R_err", "R_ci", "T", "I0"} or None}; delays in SECONDS
nmr.hetnoe(sat, ref, noise_sat=, noise_ref=) -> {res: {"hetNOE", "err"} or None}
Per-residue results are dicts keyed by residue: write them that way (one object per
quantity, residue -> value), not as lists.
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
cd.ht_cutoff(wavelength_nm, ht_v, max_v=600)  shortest trustworthy wavelength
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
