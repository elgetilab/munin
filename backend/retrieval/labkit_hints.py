"""Pointers to `labkit` at the moment the model needs it (LAB-ADOPTION-PLAN.md).

Phase 4 run 1 found the model hand-rolling `scipy.optimize` fits in 199 of
228 S1 runs although the persona named `labkit`; the failures it then made
are conventions `labkit` encodes (one-phase decay Y0 reported as the drop).
A pointer in the tool result, where the model decides its next step, is what
moved `edit_python` adoption; this is the same lever.

Two hints, both narrow:
- `fit_hint(code)`: a run_python script that fits with curve_fit,
  least_squares or lmfit a model `labkit.fit` has (exponential decay or
  association, 3/4/5PL dose response, Michaelis-Menten, Morrison, competitive
  inhibition), or reads a Bruker experiment by hand (nmrglue or raw bytes),
  and does not use `labkit`; also NMR titration Kd and relaxation fits
  (pointing at `labkit.nmr`) and straight-line standard curves (pointing at
  `labkit.fit("linear")` and `labkit.interpolate`). General optimisation is
  left alone: a fit call alone is not enough.
- `upload_hint(filename, data, paths)`: an attached file in a format
  `labkit.read` or `labkit.nmr.read_bruker` knows, sniffed the way
  `labkit.read` sniffs it (backend/sandbox/labkit/labkit/read.py).

Round 4 (LABKIT-013-PLAN.md) adds domain hints (Sparky lists, NOESY
buildup, CD ellipticity, and the NMR Kd and relaxation ones) that fire even
in scripts that import labkit, unless they call the function the hint names,
and a general hint for any curve_fit/least_squares/lmfit model that
labkit.fit does not name (labkit.fit takes a callable with multi-start).

`HintState` keeps each hint to once per conversation; the general fit hints
stop once the conversation's code has used `labkit`.
"""
from __future__ import annotations

import re
from collections import OrderedDict
from typing import Callable, Optional

_FIT_CALL = re.compile(r"\b(curve_fit|least_squares|lmfit|Minimizer)\b")

# (key, what the code looks like, labkit model names). Checked in order; the
# first match names the model in the hint.
_MODELS: list[tuple[str, re.Pattern, str]] = [
    ("morrison", re.compile(r"morrison", re.I), '"morrison" (constants={"Et": ...})'),
    ("competitive", re.compile(r"competitive|\bKi\b.*\bKm\b|\bKm\b.*\bKi\b", re.S),
     '"competitive_inhibition" with x=(S, I), two arrays with one entry per measurement (one fit '
     'over all inhibitor concentrations is the global fit, no fit_global)'),
    ("michaelis_menten", re.compile(r"michaelis|\bvmax\b|\bv_?max\b|\bKm\b", re.I), '"michaelis_menten"'),
    ("dose_response", re.compile(
        r"hill|logec50|log_?ec50|\bec50\b|\bic50\b|\b[345]\s*-?\s*pl\b|logistic", re.I),
     '"3pl", "4pl" or "5pl" (x_scale says what x IS: "linear" for concentrations, '
     '"log" only if x is already log10(conc))'),
    ("exponential", re.compile(r"\bexp\s*\(\s*-|half_?life|plateau", re.I),
     '"one_phase_decay", "two_phase_decay" or "one_phase_association"'),
]
# nmrglue on Bruker data, or any code touching a Bruker experiment folder
# (in Phase 4 run 1 the model read the FID with np.fromfile as int16, 3/3).
_BRUKER = re.compile(r"\bng\.bruker\b|nmrglue\.bruker|\bacqus\b|[/'\"]fid['\"]")
_USES_LABKIT = re.compile(r"\b(import\s+labkit|from\s+labkit\b|labkit\.)")

# Round 3 (LABKIT-011-PLAN.md): in run 2 S6 never used labkit (the relaxation
# hint named labkit.fit, not labkit.nmr) and the lost standard curves fitted
# with linregress/polyfit, which _FIT_CALL did not see.
_ANY_FIT = re.compile(r"\b(curve_fit|least_squares|lmfit|Minimizer|polyfit|linregress|lstsq|minimize)\b")
_KD = re.compile(r"\bK_?d\b|dissociation constant", re.I)
_TITRATION = re.compile(r"\bcsp\b|titrat|chemical.shift|perturbation|\bppm\b", re.I)
# NMR relaxation: "relax"/hetNOE, upper-case T1/T2, or R1 AND R2 together.
# R2 alone is usually R-squared and a plain `t1` variable is not NMR (both
# misfired on run-2 decay and 4PL scripts).
_RELAX_WORD = re.compile(r"(?i:relax|het_?noe)|\bT[12](\b|_)")
_R1 = re.compile(r"\bR1(\b|_)")
_R2 = re.compile(r"\bR2(\b|_)")


def _is_relaxation(code: str) -> bool:
    return bool(_RELAX_WORD.search(code) or (_R1.search(code) and _R2.search(code)))
_STANDARD = re.compile(r"standard(?!\s*(error|deviation|err\b|dev\b))|unknown|interpolat|bradford|calibration|\bBCA\b", re.I)
_LINE_FIT = re.compile(r"\b(polyfit|linregress|lstsq)\b")

_NMR_HINTS: list[tuple[str, str]] = [
    ("kd", "This fits an NMR titration by hand. labkit.nmr has it: csp(free, bound, alpha=..., "
           "scheme=...) for combined shift perturbations, and kd_fit(ligand_conc, csp_by_residue, "
           "protein_conc=...) for a global Kd with ligand depletion and its CI; see "
           "labkit.guide('nmr'). Keep your own code for a binding model kd_fit does not have."),
    ("relaxation", "This fits NMR relaxation by hand. labkit.nmr has it: relaxation_fit(delays_s, "
                   "{residue: intensities}) gives per-residue R with CIs (delays in SECONDS), and "
                   "hetnoe(saturated, reference) the per-residue NOE; see labkit.guide('nmr')."),
]

# Round 4 (LABKIT-013-PLAN.md). Domain hints name one labkit function each
# and fire even when the script already imports labkit, unless it calls that
# function: in run 3 the JASCO and HSQC runs read the file with labkit, then
# computed MRE and CSPs by hand and got them wrong.
# NOESY words; not a bare "NOE" (hetNOE in every relaxation script).
_NOE = re.compile(r"(?i)noesy|build.?up|cross.?relax|cross.?peak|mixing")
_CD = re.compile(r"(?i)ellipticity|\bmre(_\w*)?\b|mean.?residue|\bmrw\b")
# Sparky peak lists: the name, an "N-H" assignment label, a quoted "w1"
# column, or the Assignment header. Bare w1/w2 are too common (run 3's
# relaxation script named its warning lists w1 and w2).
_SPARKY = re.compile(r"(?i)sparky|N-H['\"]|['\"]w1['\"]|assignment[\s\S]{0,200}\bw1\b")
# Round 5 (LABKIT-014-PLAN.md): an HT (detector voltage) limit applied by
# hand. In run 4 a JASCO script used labkit.cd for MRE, then interpolated
# the 600 V crossing between measured points.
_HT_LIMIT = re.compile(r"(?i)\b(ht|hv|high.?tension)\w*\b[^\n]{0,60}?(<=?|>=?)\s*\d{3}\b"
                       r"|\b\d{3}\s*(<=?|>=?)[^\n]{0,10}\b(ht|hv)\w*\b")


def _calls(*funcs: str) -> re.Pattern:
    """The code calls one of labkit's `funcs` (as `x.func(` or imported from
    labkit). A function of the model's own with the same name does not
    count: run 3's NOESY script defined its own `noe_buildup`."""
    alt = "|".join(funcs)
    return re.compile(rf"\.(?:{alt})\s*\(|from\s+labkit[\w.]*\s+import[^\n]*\b(?:{alt})\b")


# EPR (EPR-PLAN.md): Bruker EPR files read by hand, DEER inversions by hand
# (or with DeerLab, which is not installed), spin counting by hand, g-values
# from constants typed in.
_EPR_FILE = re.compile(r"(?i)\.dta\b|\.dsc\b|\.spc\b|\bbes3t\b|\bMWFQ\b|\bXPTS\b|\bBSEQ\b|\bIKKF\b")
_EPR_READ_BY_HAND = re.compile(r"\bfromfile\b|\bfrombuffer\b|struct\.unpack|['\"]>f8['\"]|['\"]>i4['\"]|\bdtype\s*=")
_DEER = re.compile(r"(?i)\bdeer\b|\bpeldor\b|dipolar|\bdeerlab\b|\bdl\.(fit|dipolarmodel|dipolarkernel|deerload|correctphase)")
_DEER_INVERT = re.compile(r"\b(lstsq|pinv|nnls|curve_fit|least_squares|minimize|tikhonov|fresnel)\b|(?i:deerlab)")
_DOUBLE_INT = re.compile(r"\b(cumsum|cumtrapz|cumulative_trapezoid)\b")
_EPR_CONTEXT = re.compile(r"(?i)\bspins?\b|tempol|double.?integ|\bepr\b|\besr\b|\bmodamp\b|\bgauss\b|\bRCAG\b")
_G_BY_HAND = re.compile(r"(?i)\bmu_?b\b|bohr|9\.27\d*e-24|71\.44|0\.7144|714\.4|13\.996")
_EPR_HINT_TAIL = "; see labkit.guide('epr')."
# Round 2 (LABKIT-015-PLAN.md): CW spectra from text files were measured by
# hand in S8 (Kivelson, Gaussian linewidth, power saturation); no hint
# covered peak-to-peak widths, line heights or a saturation curve.
_CW_EPR_WORDS = re.compile(r"(?i)\b(cw.?)?epr\b|\besr\b|dI/dB|\bfield_?(G|mT)\b|field\s*\[\s*(G|mT)\s*\]"
                           r"|\bB_(G|mT)\b|kivelson|nitroxide|tempol|\bdpph\b|\bdBpp\b|\bg[-_ ]?(value|factor|iso)\b"
                           r"|spin.?label|hyperfine|\bmodamp\b|microwave")
# labkit's own names ("from labkit import epr", "epr.load(") are not EPR context
_LABKIT_REFS = re.compile(r"\bepr\.\w+|from\s+labkit[\w.]*\s+import[^\n]*|import\s+labkit[^\n]*|labkit\.\w+")
_DEER_CTX = re.compile(r"(?i)\bdeer\b|\bpeldor\b|dipolar|\bt_us\b|\btime_ns\b|modulation.?depth|fit_deer")
# Round 3 (LABKIT-018-PLAN.md): DEER code the old pattern missed. `\bdeer\b`
# does not match deer_raw.csv, and quad.csv carries no DEER word; the scripts
# that fitted by hand said dipolar, mod_depth or typed the dipolar constant
# (52.04 MHz nm^3, 2 pi times that is 327).
# The constant counts only in a formula (52.1 / r**3, 2*pi*52.04, 52.1 MHz),
# not as a number in a data list.
_DIPOLAR_CONSTANT = r"(?<![\w.])(?:52\.[01]\d*|32[67]\.\d+)(?![\w.])"
_DEER_ISH = re.compile(r"(?i)(?<![a-z])deer|peldor|dipolar|mod_?depth|modulation.?depth"
                       rf"|{_DIPOLAR_CONSTANT}\s*(?:/|\*|MHz)|\*\s*{_DIPOLAR_CONSTANT}")
_GUIDE_REFS = re.compile(r"\bguide\s*\([^)]*\)")


_LABKIT_CHAINS = re.compile(r"\b(?:labkit|epr)(?:\.\w+)+")


def _deer_ish(c: str) -> bool:
    """DEER words outside labkit's own names (epr.fit_deer, guide('deer'));
    code that names fit_deer at all (calls it, reads its help) is past the
    pointer."""
    if re.search(r"\bfit_deer\b", c):
        return False
    c = _LABKIT_CHAINS.sub(" ", _LABKIT_REFS.sub(" ", _GUIDE_REFS.sub(" ", c)))
    return bool(_DEER_ISH.search(c))


def _deer_context(c: str) -> bool:
    """DEER code, including code that calls fit_deer: the other domain hints
    and the general fit hints do not apply to it (round 3 runs got the
    relaxation hint for T2, Michaelis-Menten for V_max, epr_g for mu_B)."""
    return bool(_DEER_CTX.search(c) or _deer_ish(c))


# Hints that never apply to DEER code.
_NOT_FOR_DEER = {"relaxation", "epr_g", "epr_integral", "kd", "noe_buildup"}


def _cw_epr(c: str, labkit_epr_counts: bool = False) -> bool:
    """CW-EPR code (not DEER). EPR words outside labkit's own names; with
    labkit_epr_counts, data loaded with labkit.epr also counts."""
    if _DEER_CTX.search(c):
        return False
    if labkit_epr_counts and re.search(r"\bepr\.\w+\s*\(", c):
        return True
    return bool(_CW_EPR_WORDS.search(_LABKIT_REFS.sub(" ", c)))


# Measuring a line by hand: locating extrema AND computing a width or height from them.
_EXTREMA = re.compile(r"\b(find_peaks|argmax|argmin|idxmax|idxmin)\b")
_LINESHAPE = re.compile(r"(?i)peak.?to.?peak|\bdBpp\b|\bfwhm\b|\bhwhm\b|line.?width|kivelson|6\.51e-10"
                        r"|\bheights?\b|\bh0\b|\bh_?[pm]1\b")
_POWER = re.compile(r"(?i)p_?half|p1/2|saturat|attenuat|sqrt\(\s*p(ower)?\b")
# Masking a CW spectrum by amplitude, abs(y) > K or abs(y) < K: in round 3
# this removed the lines themselves as "spikes" (449 and 127 points).
_AMPLITUDE_MASK = re.compile(r"\babs\(\s*[\w.\[\]]+\s*\)\s*[<>]=?\s*(?:\d|\.\d|[A-Za-z_])")


def _calls_any(*funcs: str) -> re.Pattern:
    alt = "|".join(funcs)
    # epr.<func>( or imported from labkit.epr: a bare load( is json.load or np.load
    return re.compile(rf"\bepr\.(?:{alt})\s*\(|from\s+labkit\.epr\s+import[^\n]*\b(?:{alt})\b")


# Reading labkit's own source or help() to learn a call: 20.5% of the calls in
# runs that hit the turn budget, 7.6% elsewhere (LABKIT-016-PLAN.md).
_LABKIT_SOURCE = re.compile(
    r"inspect\.(?:getsource|getsourcelines|getfile|signature)\s*\(\s*labkit"
    r"|\blabkit(?:\.\w+)*\.__file__"
    r"|open\([^)\n]*labkit[^)\n]*\.py"
    r"|\bhelp\(\s*labkit")
_GUIDE_CALL = re.compile(r"\bguide\s*\(")

_DOMAIN: list[tuple[str, Callable[[str], bool], re.Pattern, str]] = [
    ("epr_read", lambda c: bool(_EPR_FILE.search(c) and _EPR_READ_BY_HAND.search(c)),
     _calls_any("load", "load_bes3t", "load_esp", "load_ascii"),
     "This reads a Bruker EPR file by hand. labkit.epr.load(path) reads BES3T (.DSC/.DTA: "
     "byte order, complex interleaving, 2D layout, axis = XMIN + linspace(0, XWID, XPTS)), ESP "
     ".par/.spc and ASCII exports, converts the field to mT (or time to us), and gives "
     "freq_GHz (from MWFQ, in Hz in the file), power_mW, modamp_mT, gain_dB, n_scans"
     + _EPR_HINT_TAIL),
    # Round 3: on the first DEER script that does not call fit_deer, fit call
    # or not (four hand analyses never got it, LABKIT-018-PLAN.md).
    ("deer", lambda c: bool((_DEER.search(c) and _DEER_INVERT.search(c)) or _deer_ish(c)),
     _calls_any("fit_deer"),
     "This works on a DEER trace by hand (DeerLab is not installed). Three lines do it: "
     "r = labkit.epr.fit_deer(t_us, V); print(r); then r.lam (modulation depth), r.mean_nm, "
     "r.conc_uM (frozen solution), r.populations, r.t0_ns, and r.main_nm when the trace is too short "
     "for the long part. It phases a complex trace, finds the zero time, and fits P(r), lambda and the "
     "background in one step, limited to what the trace length supports. The modulation depth is the "
     "fitted lambda, never 1 - V(end)/V(0) (that includes the background); never |V|. Labelling "
     "efficiency: epr.labeling_efficiency(r.lam, inversion=..., scheme='both_equal'). Background: the "
     "sample decides (frozen solution: default hom3d; membrane, liposome or micelle: "
     "background='strexp', dim=2.0); if the question does not say, ask the user. "
     "See labkit.guide('deer')."),
    ("epr_power", lambda c: bool(_cw_epr(c, True) and _POWER.search(c) and _ANY_FIT.search(c)),
     _calls_any("power_saturation_fit"),
     "This fits a power-saturation curve by hand. labkit.epr has it: P = epr.attenuation_to_mW(d.y2, "
     "p_max_mW=200); A = [epr.peak_to_peak(d.field_mT, row)['Ypp'] for row in d.real]; "
     "r = epr.power_saturation_fit(P, A) gives r.params['P_half'] (mW) and r.params['epsilon'] "
     "with CIs, A = I sqrt(P) [1 + (2^(1/eps) - 1) P/P_half]^(-eps)" + _EPR_HINT_TAIL),
    ("epr_lineshape", lambda c: bool(_cw_epr(c, True) and (_EXTREMA.search(c) or _ANY_FIT.search(c))
                                     and _LINESHAPE.search(c) and not _POWER.search(c)),
     _calls_any("peak_to_peak", "fit_line", "nitroxide_hyperfine", "kivelson_tau", "double_integral",
                "spin_count", "power_saturation_fit"),
     "This measures EPR lines by hand. labkit.epr does it for text files too: d = epr.load(path) "
     "(field unit from the header, d.field_mT, d.y); epr.fit_line(d.field_mT, d.y, shape='gaussian' or "
     "'lorentzian') gives dBpp_mT, fwhm_mT and g; nx = epr.nitroxide_hyperfine(d.field_mT, d.y) gives "
     "aN, dBpp and the three line heights from the raw derivative, and "
     "epr.kivelson_tau(nx.dBpp_mT[0], nx.heights[0], nx.heights[1], nx.heights[-1])['tau_c_s']; "
     "a spin ratio is epr.double_integral(...).value of one over the other. Extrema on the grid "
     "(argmax, argmin) are quantised to the field step; use epr.peak_to_peak or fit_line"
     + _EPR_HINT_TAIL),
    ("epr_integral", lambda c: bool(_DOUBLE_INT.search(c) and _EPR_CONTEXT.search(c)),
     _calls_any("double_integral", "spin_count"),
     "This integrates an EPR spectrum by hand. labkit.epr.double_integral(field_mT, y) removes a "
     "baseline before and after the first integration and warns when the window is too narrow; "
     "labkit.epr.spin_count(sample, standard, standard_amount=...) also normalises each double "
     "integral by 10^(gain_dB/20), sqrt(power), modulation amplitude and scans from the files"
     + _EPR_HINT_TAIL),
    ("epr_g", lambda c: bool(_G_BY_HAND.search(c)), _calls_any("g_value", "fit_line", "nitroxide_hyperfine",
                                                               "calibrate_field"),
     "This computes a g-value from constants typed in. labkit.epr.g_value(B_mT, freq_GHz) uses "
     "CODATA 2022 (71.447735 mT/GHz); labkit.epr.fit_line(field_mT, y, freq_GHz=d.freq_GHz) fits "
     "the line and gives g, dBpp and the absorption FWHM (Lorentzian sqrt(3) dBpp, Gaussian "
     "1.1774 dBpp). Use the measured frequency from the file (d.freq_GHz), not a nominal one"
     + _EPR_HINT_TAIL),
    ("epr_mask", lambda c: bool(_cw_epr(c, True) and _AMPLITUDE_MASK.search(c)), _calls_any("despike"),
     "This masks an EPR spectrum by amplitude. In a derivative spectrum the largest points are the "
     "line; a spike is 1 to 3 points wide. Use epr.despike: y_clean, idx = epr.despike(field_mT, y, "
     "max_width=3) replaces only outliers at most 3 points wide and returns their indices"
     + _EPR_HINT_TAIL),
    ("sparky", lambda c: bool(_SPARKY.search(c)), _calls("read_sparky"),
     "This parses Sparky peak lists by hand. labkit.nmr.read_sparky(path) returns {residue: "
     "(1H, 15N)} in the order csp() takes (it tells 1H from 15N by value; 'G2N-H' -> 'G2'), "
     "then csp(free, bound, alpha=..., scheme=...) and kd_fit(...); see labkit.guide('nmr')."),
    ("noe_buildup", lambda c: bool(_NOE.search(c) and _ANY_FIT.search(c)), _calls("noe_buildup"),
     "This fits NOESY buildup curves by hand. labkit.nmr.noe_buildup(mixing_s, {pair: [A(t)]}, "
     "{pair: A_ii(0)}) fits A(t) = (A0/2)(1 - exp(-2 sigma t)) exp(-t/T) with A(0) = 0 from a grid "
     "of starts (the surface has local minima) and returns sigma and T with CIs per pair; "
     "nmr.read_integrals(path) reads TopSpin integral lists. Not relaxation_fit or a decay model; "
     "see labkit.guide('nmr')."),
    ("cd", lambda c: bool(_CD.search(c)), _calls("mean_residue_ellipticity", "mean_residue_weight"),
     "This computes CD mean residue ellipticity by hand. labkit.cd.mean_residue_ellipticity("
     "theta_mdeg, conc_mg_ml=, path_cm=, mw_da=, n_residues=) does it with the units written out "
     "(MRW = MW/(N-1), result in deg cm^2 dmol^-1); see labkit.guide('cd')."),
    ("ht_cutoff", lambda c: bool(_HT_LIMIT.search(c)), _calls("ht_cutoff"),
     "This applies a CD HT (detector voltage) limit by hand. labkit.cd.ht_cutoff(wavelength_nm, "
     "ht_v, max_v=600) returns the last MEASURED wavelength, scanning down from the long end, "
     "before HT first exceeds max_v: a wavelength that was recorded, not one interpolated "
     "between points; see labkit.guide('cd')."),
    ("kd", lambda c: bool(_ANY_FIT.search(c) and _KD.search(c) and _TITRATION.search(c)),
     _calls("kd_fit"), _NMR_HINTS[0][1]),
    ("relaxation", lambda c: bool(_ANY_FIT.search(c) and _is_relaxation(c) and re.search(r"\bexp\s*\(", c)),
     _calls("relaxation_fit"), _NMR_HINTS[1][1]),
    ("labkit_source", lambda c: bool(_LABKIT_SOURCE.search(c)), _GUIDE_CALL,
     "This reads labkit's source or help() to learn a call. labkit.guide('<topic>') is "
     "shorter and has the call signatures and a worked example for each task: topics fit, "
     "read, nmr, stats, cd, plot, epr, deer (labkit.guide() for the overview). help() on a "
     "whole module is long and gets cut in the tool result."),
]

_CUSTOM_HINT = (
    "This fits a model by hand with scipy. labkit.fit takes any model too: labkit.fit(f, x, y, "
    "p0=[...]) with f(x, a, b, ...) fits from several starting points (local minima), and gives "
    "Wald CIs, R2 and warnings for ambiguous fits; print(r) shows them; see labkit.guide('fit'). "
    "It passes all 27 NIST StRD problems from both start values.")


def fit_hint(code: str, labkit_in_use: bool = False,
             given: frozenset[str] | set[str] = frozenset()) -> Optional[tuple[str, str]]:
    """(key, hint) when `code` hand-rolls something labkit has, else None.

    `labkit_in_use`: the conversation has used labkit before. Domain hints
    still fire then (unless the code calls the function they name); the
    general fit hints do not.
    `given`: hint keys the conversation already had; the next applicable one
    is returned instead (run 4's JASCO script matched the CD hint, already
    given, and so never got the HT cutoff one).
    """
    if not isinstance(code, str):
        return None
    for found in _candidates(code, labkit_in_use):
        if found[0] in _GENERAL:
            # Only the first general hint counts, as before round 5: a decay
            # script whose hint was given does not fall through to custom_fit.
            return None if found[0] in given else found
        if found[0] not in given:
            return found
    return None


# The general "labkit.fit has this" hints: for one script only the first that
# applies is considered (domain hints fall through to the next one).
_GENERAL = {k for k, _, _ in _MODELS} | {"standard_curve", "custom_fit"}


def _candidates(code: str, labkit_in_use: bool):
    """Every hint that applies to `code`, most specific first."""
    uses = labkit_in_use or bool(_USES_LABKIT.search(code))
    deer = _deer_context(code)
    if _BRUKER.search(code) and not _calls("read_bruker").search(code):
        yield ("bruker", (
            "This reads Bruker data by hand. labkit.nmr has the "
            "processing chain (read_bruker takes the data type from DTYPA, "
            "process_1d, reference, peaks, integrate); see labkit.guide('nmr'). "
            "Keep your own code for steps labkit.nmr does not cover."))
    for key, applies, covered, hint in _DOMAIN:
        if deer and key in _NOT_FOR_DEER:
            continue
        if applies(code) and not covered.search(code):
            yield (key, hint)
    if uses or deer:
        return    # the general fit hints do not apply to DEER code either
    if _FIT_CALL.search(code):
        epr_code = bool(_cw_epr(code) or _DEER.search(code) or _DEER_CTX.search(code))
        for key, pat, names in _MODELS:
            if key == "exponential" and epr_code:
                continue    # a Gaussian EPR line or a DEER background, not a decay
            if pat.search(code):
                yield (key, (
                    f"This fits by hand a model labkit.fit has: {names}, with "
                    "GraphPad Prism's parameter names and definitions (for example "
                    "Y0 is the value at X=0, not the drop), Wald CIs and warnings "
                    "for ambiguous fits: r = labkit.fit(model, x, y); print(r); "
                    "see labkit.guide('fit'). Keep your own code if the model is "
                    "not one of these."))
                break
    if _LINE_FIT.search(code) and _STANDARD.search(code):
        yield ("standard_curve", (
            "This fits a standard curve by hand. labkit has it: r = labkit.fit(\"linear\", "
            "conc, readings) (every replicate as a point, Prism's default), then "
            "labkit.interpolate(r, {name: readings}) returns each unknown as a number, or "
            "None outside the standards' range (no extrapolation); see labkit.guide('fit')."))
    if _FIT_CALL.search(code):
        yield ("custom_fit", _CUSTOM_HINT)


class HintState:
    """Which hints a conversation has had; bounded so it cannot grow forever."""

    def __init__(self, max_conversations: int = 2000) -> None:
        self._seen: "OrderedDict[str, set[str]]" = OrderedDict()
        self._max = max_conversations

    def _entry(self, conv_id: str) -> set[str]:
        entry = self._seen.pop(conv_id, None) or set()
        self._seen[conv_id] = entry
        while len(self._seen) > self._max:
            self._seen.popitem(last=False)
        return entry

    def hint_for(self, conv_id: str, code: str) -> Optional[tuple[str, str]]:
        """(kind, hint) for this run, or None (already given; general fit
        hints also stop once the conversation has used labkit)."""
        entry = self._entry(conv_id)
        found = fit_hint(code, labkit_in_use="uses_labkit" in entry, given=entry)
        if isinstance(code, str) and _USES_LABKIT.search(code):
            entry.add("uses_labkit")
        if found is None:
            return None
        entry.add(found[0])
        return found


# --- uploads ------------------------------------------------------------------

_BINARY_EXPORTS = {
    ".pcrd": "a Bio-Rad CFX run file",
    ".sda": "a SoftMax Pro document",
}


def _decode_head(data: bytes) -> str:
    head = data[:8192]
    if head.startswith((b"\xff\xfe", b"\xfe\xff")):
        return head.decode("utf-16", errors="replace")
    if len(head) > 8 and head[1::2].count(0) > 0.4 * (len(head) // 2):
        return head.decode("utf-16-le", errors="replace")
    return head.decode("utf-8", errors="replace").lstrip("﻿")


def upload_hint(filename: str, data: bytes, paths: list[str]) -> str:
    """One sentence naming the labkit reader for a known lab format, or ''."""
    name = (filename or "").lower()
    ext = name[name.rfind("."):] if "." in name else ""
    if any(p.rsplit("/", 1)[-1] in ("acqus", "fid", "ser") for p in paths):
        parts = paths[0].split("/")
        target = "/".join(parts[:2]) if len(parts) > 2 else "inputs"
        return (f"It is Bruker NMR data: labkit.nmr.read_bruker({target!r}) reads it "
                "(see labkit.guide('nmr')).")
    epr_names = [p for p in [name] + [q.lower() for q in paths]
                 if p.endswith((".dsc", ".dta", ".par", ".spc"))]
    if epr_names:
        return ("It is Bruker EPR data (BES3T .DSC/.DTA or ESP .par/.spc): labkit.epr.load(path) reads "
                "it with the field in mT (time in us for pulse data) and the acquisition parameters "
                "(freq_GHz, power_mW, modamp_mT, gain_dB, n_scans); see labkit.guide('epr') and "
                "labkit.guide('deer').")
    if any(p.lower().endswith(".list") for p in paths) and ext == ".zip":
        return ("It holds Sparky peak lists (.list): labkit.nmr.read_sparky(path) reads each as "
                "{residue: (1H, 15N)} in the order labkit.nmr.csp takes (see labkit.guide('nmr')).")
    if ext in _BINARY_EXPORTS:
        return (f"It is {_BINARY_EXPORTS[ext]}, a binary format: labkit.read refuses it "
                "and says which export to ask the user for.")
    if ext in (".xlsx", ".xlsm"):
        return "If it is a plate or instrument export, labkit.read(path) reads it (see labkit.guide('read'))."
    if ext not in (".txt", ".csv", ".tsv", ".dat", ".asc", ".list", ""):
        return ""
    head = _decode_head(data)
    up = head.upper()
    if "XYDATA" in up and ("JASCO" in up or "YUNITS" in up):
        return "It is a JASCO spectrum export: labkit.read_jasco(path) reads it with its units."
    if re.search(r"^Time\t", head, re.M) and ("Reader Type" in head or "Software Version" in head):
        return "It is a BioTek Gen5 export: labkit.read_gen5(path) reads it (times in minutes, OVER wells reported)."
    if re.search(r"^Object\tIntegral", head, re.M):
        return ("It is a TopSpin integral list: labkit.nmr.read_integrals(path) reads it in file "
                "order (integral, f2_ppm, f1_ppm); for NOESY buildups see labkit.nmr.noe_buildup.")
    if re.search(r"^\s*Assignment\s+w1\s+w2", head, re.M):
        return ("It is a Sparky peak list: labkit.nmr.read_sparky(path) reads it as {residue: "
                "(1H, 15N)}, the order labkit.nmr.csp takes.")
    if "A260/A280" in head or "Nucleic Acid" in head:
        return "It is a NanoDrop export: labkit.read_nanodrop(path) reads it in any locale or encoding."
    first = "\n".join(head.splitlines()[:3])
    if _EPR_ASCII_HEAD.search(first):
        return ("It looks like a CW-EPR spectrum exported as text (magnetic field in G or mT): "
                "labkit.epr.load(path) reads it with the field in mT (unit from the header; x_unit='G' "
                "if it has none), then epr.fit_line, epr.nitroxide_hyperfine, epr.double_integral; "
                "see labkit.guide('epr').")
    return ""


# A header naming a magnetic-field column with its unit: "field_G", "Field [mT]",
# "B_G", Xepr's "Field [G]"; "Gauss" spelled out.
_EPR_ASCII_HEAD = re.compile(r"(?i)\bfield[ _]?\[?\(?\s*(G|mT|gauss)\b|\bB_\[?(G|mT)\b|\bB\s*\[(G|mT)\]"
                             r"|magnetic.?field")
