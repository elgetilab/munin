"""S1 fit correctness: Prism-family synthetic fits and trap items.

Each fit case grades the model's estimates against the least-squares optimum
of the data as written (any correct optimizer lands within the tolerance) and
its confidence interval against the reference Wald interval. The generating
parameters are stored too ("generating"), for CI coverage across seeds.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from common import csv_bytes, fit, read_back, write_case

SEEDS = (1, 2, 3, 4, 5)
CI_TOL = 0.25        # interval bounds within 25% of the reference width
FMT_PROMPT_CI = "95% confidence interval"


def _num_checks(ref: dict, keys: dict) -> list[dict]:
    """keys: name -> (rtol, atol)."""
    return [{"type": "numeric", "key": k, "truth": f"truth:ref.{k}", "rtol": r, "atol": a}
            for k, (r, a) in keys.items()]


def _ci_check(key: str) -> dict:
    return {"type": "ci_close", "key": f"{key}_ci", "truth": f"truth:ref.{key}_ci", "tol": CI_TOL}


# -- models ------------------------------------------------------------------

def f4pl(logx, bottom, top, logec50, hill):
    return bottom + (top - bottom) / (1 + 10 ** ((logec50 - logx) * hill))


def f3pl(logx, bottom, top, logec50):
    return f4pl(logx, bottom, top, logec50, 1.0)


def f5pl(logx, bottom, top, logxb, hill, s):
    return bottom + (top - bottom) / (1 + 10 ** ((logxb - logx) * hill)) ** s


def fmm(s, vmax, km):
    return vmax * s / (km + s)


def fdecay1(t, y0, plateau, k):
    return (y0 - plateau) * np.exp(-k * t) + plateau


def fdecay2(t, plateau, span_fast, k_fast, span_slow, k_slow):
    return plateau + span_fast * np.exp(-k_fast * t) + span_slow * np.exp(-k_slow * t)


def fmorrison(i, v0, kiapp, e=5.0):
    b = e + i + kiapp
    return v0 * (1 - (b - np.sqrt(b * b - 4 * e * i)) / (2 * e))


# -- dose response -----------------------------------------------------------

DOSES = 10 ** np.arange(-3, 2.01, 0.5)    # 11 doses, 1 nM to 100 uM


def _dose_rows(rng, f, params, sd, reps=3):
    rows = []
    for d in DOSES:
        for _ in range(reps):
            rows.append([float(d), float(f(np.log10(d), *params) + rng.normal(0, sd))])
    return rows


def _write_dose(rows):
    data = csv_bytes(["conc_uM", "response"], rows, ["{:.6g}", "{:.3f}"])
    back = np.array([[float(a), float(b)] for a, b in read_back(data)])
    return data, np.log10(back[:, 0]), back[:, 1]


def case_4pl(root: Path, seed: int):
    rng = np.random.default_rng(1000 + seed)
    gen = [5.0 + rng.normal(0, 2), 100 + rng.normal(0, 5), rng.uniform(-1.5, 0.5), rng.uniform(0.8, 1.6)]
    data, lx, y = _write_dose(_dose_rows(rng, f4pl, gen, 4.0))
    r = fit(f4pl, lx, y, [0, 100, -0.5, 1])
    names = ["bottom", "top", "logEC50", "hill"]
    ref = {n: float(v) for n, v in zip(names, r["p"])}
    ref["logEC50_ci"] = list(r["ci"][2])
    ref["hill_ci"] = list(r["ci"][3])
    fname = f"dose_response_{seed}.csv"
    prompt = (f"{fname} is a dose-response experiment: concentration in µM and response, three "
              "replicates per concentration. Fit a four-parameter logistic (variable slope) model of "
              "response against log10(concentration), using every replicate point. Report bottom, top, "
              f"logEC50 (log10 µM) and the Hill slope, with the {FMT_PROMPT_CI} of logEC50 and of the Hill slope.")
    checks = _num_checks(ref, {"bottom": (0.02, 0.5), "top": (0.02, 0.5), "logEC50": (0, 0.02), "hill": (0.03, 0)})
    checks += [_ci_check("logEC50"), _ci_check("hill")]
    write_case(root, f"s1_4pl_s{seed}", "S1", [{"prompt": prompt, "checks": checks}],
               files={fname: data}, results_keys={k: "" for k in [*names, "logEC50_ci", "hill_ci"]},
               truth={"ref": ref, "generating": dict(zip(names, gen))}, tags=["dose-response"],
               provenance=f"synthetic, s1.case_4pl seed {seed}")


def case_3pl(root: Path, seed: int):
    rng = np.random.default_rng(2000 + seed)
    gen = [2.0 + rng.normal(0, 1), 80 + rng.normal(0, 5), rng.uniform(-1.5, 0.5)]
    data, lx, y = _write_dose(_dose_rows(rng, f3pl, gen, 3.0))
    r = fit(f3pl, lx, y, [0, 80, -0.5])
    names = ["bottom", "top", "logEC50"]
    ref = {n: float(v) for n, v in zip(names, r["p"])}
    ref["logEC50_ci"] = list(r["ci"][2])
    fname = f"agonist_{seed}.csv"
    prompt = (f"{fname} holds an agonist concentration-response curve (µM, triplicates). Fit the "
              "standard-slope model (Hill slope fixed at 1.0) of response vs log10(concentration) to all "
              f"replicate points. Report bottom, top and logEC50 with the {FMT_PROMPT_CI} of logEC50.")
    checks = _num_checks(ref, {"bottom": (0.02, 0.5), "top": (0.02, 0.5), "logEC50": (0, 0.02)})
    checks.append(_ci_check("logEC50"))
    write_case(root, f"s1_3pl_s{seed}", "S1", [{"prompt": prompt, "checks": checks}],
               files={fname: data}, results_keys={k: "" for k in [*names, "logEC50_ci"]},
               truth={"ref": ref, "generating": dict(zip(names, gen))}, tags=["dose-response"],
               provenance=f"synthetic, s1.case_3pl seed {seed}")


def case_5pl(root: Path, seed: int):
    rng = np.random.default_rng(3000 + seed)
    gen = [3.0, 95 + rng.normal(0, 3), rng.uniform(-1.0, 0.0), rng.uniform(0.9, 1.3), rng.uniform(0.4, 0.6)]
    rows = []
    doses = 10 ** np.arange(-3, 2.01, 0.25)
    for d in doses:
        for _ in range(2):
            rows.append([float(d), float(f5pl(np.log10(d), *gen) + rng.normal(0, 1.5))])
    data, lx, y = _write_dose(rows)
    r = fit(f5pl, lx, y, [0, 100, -0.5, 1, 0.5], bounds=([-50, 0, -5, 0.1, 0.05], [50, 300, 5, 10, 20]),
            starts=[[0, 100, 0, 1, 1], [5, 90, -1, 1.5, 0.3]])
    b, t, lxb, h, s = r["p"]
    # x at half-maximal response: (1 + 10^((lxb-x)h))^s = 2
    logec50 = lxb - np.log10(2 ** (1 / s) - 1) / h
    ref = {"bottom": b, "top": t, "hill": h, "asymmetry": s, "logEC50": float(logec50)}
    fname = f"asymmetric_{seed}.csv"
    prompt = (f"{fname} is an asymmetric concentration-response curve (µM, duplicates). Fit a "
              "five-parameter logistic, Y = Bottom + (Top-Bottom)/(1+10^((LogXb-X)*HillSlope))^S with "
              "X = log10(concentration), to all points. Report bottom, top, the Hill slope, the "
              "asymmetry factor S, and logEC50 defined as the log10 concentration giving a response "
              "halfway between bottom and top.")
    checks = _num_checks(ref, {"bottom": (0.05, 1.0), "top": (0.02, 1.0), "logEC50": (0, 0.03),
                               "hill": (0.05, 0), "asymmetry": (0.08, 0)})
    write_case(root, f"s1_5pl_s{seed}", "S1", [{"prompt": prompt, "checks": checks}],
               files={fname: data}, results_keys={k: "" for k in ["bottom", "top", "hill", "asymmetry", "logEC50"]},
               truth={"ref": ref, "generating": dict(zip(["bottom", "top", "logXb", "hill", "S"], gen))},
               tags=["dose-response"], provenance=f"synthetic, s1.case_5pl seed {seed}")


# -- enzyme kinetics ---------------------------------------------------------

def case_mm(root: Path, seed: int):
    rng = np.random.default_rng(4000 + seed)
    vmax, km = rng.uniform(50, 150), rng.uniform(2, 20)
    subs = km * np.array([0.125, 0.25, 0.5, 1, 2, 4, 8, 16])
    rows = [[float(s), float(fmm(s, vmax, km) * (1 + rng.normal(0, 0.04)))] for s in subs for _ in range(2)]
    data = csv_bytes(["substrate_uM", "rate_nM_per_s"], rows, ["{:.4g}", "{:.3f}"])
    back = np.array([[float(a), float(b)] for a, b in read_back(data)])
    r = fit(fmm, back[:, 0], back[:, 1], [back[:, 1].max(), np.median(back[:, 0])])
    ref = {"Vmax": r["p"][0], "Km": r["p"][1], "Km_ci": list(r["ci"][1]), "Vmax_ci": list(r["ci"][0])}
    fname = f"kinetics_{seed}.csv"
    prompt = (f"{fname} has initial rates at eight substrate concentrations, in duplicate. Fit the "
              "Michaelis-Menten equation by nonlinear regression to all points (no linearisation). "
              f"Report Vmax and Km with their {FMT_PROMPT_CI}s.")
    checks = _num_checks(ref, {"Vmax": (0.01, 0), "Km": (0.02, 0)}) + [_ci_check("Km"), _ci_check("Vmax")]
    write_case(root, f"s1_mm_s{seed}", "S1", [{"prompt": prompt, "checks": checks}],
               files={fname: data}, results_keys={k: "" for k in ["Vmax", "Km", "Vmax_ci", "Km_ci"]},
               truth={"ref": ref, "generating": {"Vmax": vmax, "Km": km}}, tags=["enzyme"],
               provenance=f"synthetic, s1.case_mm seed {seed}")


def case_competitive(root: Path, seed: int):
    rng = np.random.default_rng(5000 + seed)
    vmax, km, ki = rng.uniform(80, 120), rng.uniform(5, 15), rng.uniform(1, 10)
    subs = km * np.array([0.2, 0.4, 0.8, 1.6, 3.2, 6.4, 12.8])
    inh = [0.0, ki, 3 * ki, 9 * ki]
    rows = []
    for i in inh:
        for s in subs:
            v = vmax * s / (km * (1 + i / ki) + s)
            rows.append([float(s), float(i), float(v * (1 + rng.normal(0, 0.03)))])
    data = csv_bytes(["substrate_uM", "inhibitor_uM", "rate"], rows, ["{:.4g}", "{:.4g}", "{:.3f}"])
    back = np.array([[float(c) for c in row] for row in read_back(data)])

    def g(x, vm, k, kk):
        s, i = x
        return vm * s / (k * (1 + i / kk) + s)
    r = fit(g, back[:, :2].T, back[:, 2], [100, 10, 5])
    ref = {"Vmax": r["p"][0], "Km": r["p"][1], "Ki": r["p"][2], "Ki_ci": list(r["ci"][2])}
    fname = f"inhibition_{seed}.csv"
    prompt = (f"{fname} has rates at seven substrate and four inhibitor concentrations. Fit a "
              "competitive inhibition model globally, with Vmax, Km and Ki shared across all inhibitor "
              f"concentrations. Report Vmax, Km and Ki, and the {FMT_PROMPT_CI} of Ki.")
    checks = _num_checks(ref, {"Vmax": (0.01, 0), "Km": (0.03, 0), "Ki": (0.03, 0)}) + [_ci_check("Ki")]
    write_case(root, f"s1_competitive_s{seed}", "S1", [{"prompt": prompt, "checks": checks}],
               files={fname: data}, results_keys={k: "" for k in ["Vmax", "Km", "Ki", "Ki_ci"]},
               truth={"ref": ref, "generating": {"Vmax": vmax, "Km": km, "Ki": ki}}, tags=["enzyme", "global"],
               provenance=f"synthetic, s1.case_competitive seed {seed}")


def case_morrison(root: Path, seed: int):
    rng = np.random.default_rng(6000 + seed)
    v0, kiapp = rng.uniform(80, 120), rng.uniform(0.5, 3.0)
    inh = np.array([0, 0.5, 1, 2, 3, 4, 6, 8, 12, 16, 24, 32], float)
    rows = [[float(i), float(fmorrison(i, v0, kiapp) + rng.normal(0, 1.5))] for i in inh for _ in range(2)]
    data = csv_bytes(["inhibitor_nM", "rate"], rows, ["{:.4g}", "{:.3f}"])
    back = np.array([[float(a), float(b)] for a, b in read_back(data)])
    r = fit(fmorrison, back[:, 0], back[:, 1], [100, 1], bounds=([0, 1e-4], [1e4, 1e3]))
    ref = {"v0": r["p"][0], "Ki_app": r["p"][1], "Ki_app_ci": list(r["ci"][1])}
    fname = f"tight_binding_{seed}.csv"
    prompt = (f"{fname} is a tight-binding inhibitor titration: rate vs inhibitor concentration (nM). "
              "The enzyme concentration in the assay is 5 nM, comparable to the inhibitor, so ligand "
              "depletion matters. Fit the Morrison equation with [E]t fixed at 5 nM and report the "
              f"uninhibited rate v0 and the apparent Ki (nM) with its {FMT_PROMPT_CI}.")
    checks = _num_checks(ref, {"v0": (0.01, 0), "Ki_app": (0.04, 0)}) + [_ci_check("Ki_app")]
    write_case(root, f"s1_morrison_s{seed}", "S1", [{"prompt": prompt, "checks": checks}],
               files={fname: data}, results_keys={k: "" for k in ["v0", "Ki_app", "Ki_app_ci"]},
               truth={"ref": ref, "generating": {"v0": v0, "Ki_app": kiapp, "Et_nM": 5.0}},
               tags=["enzyme", "tight-binding"], provenance=f"synthetic, s1.case_morrison seed {seed}")


# -- exponentials ------------------------------------------------------------

def case_decay1(root: Path, seed: int):
    rng = np.random.default_rng(7000 + seed)
    y0, plateau, k = rng.uniform(800, 1200), rng.uniform(50, 150), rng.uniform(0.05, 0.3)
    t = np.linspace(0, 6 / k, 16)
    rows = [[float(ti), float(fdecay1(ti, y0, plateau, k) + rng.normal(0, 15))] for ti in t]
    data = csv_bytes(["time_min", "signal"], rows, ["{:.3f}", "{:.2f}"])
    back = np.array([[float(a), float(b)] for a, b in read_back(data)])
    r = fit(fdecay1, back[:, 0], back[:, 1], [back[0, 1], back[-1, 1], 1 / back[-1, 0] * 3])
    ref = {"y0": r["p"][0], "plateau": r["p"][1], "k": r["p"][2], "half_life": float(np.log(2) / r["p"][2]),
           "k_ci": list(r["ci"][2])}
    fname = f"decay_{seed}.csv"
    prompt = (f"{fname} is a single-exponential decay to a non-zero plateau. Fit a one-phase decay "
              f"model and report Y0, plateau, the rate constant k (per minute) with its {FMT_PROMPT_CI}, "
              "and the half-life in minutes.")
    checks = _num_checks(ref, {"y0": (0.01, 0), "plateau": (0.03, 2), "k": (0.02, 0), "half_life": (0.02, 0)})
    checks.append(_ci_check("k"))
    write_case(root, f"s1_decay1_s{seed}", "S1", [{"prompt": prompt, "checks": checks}],
               files={fname: data}, results_keys={k: "" for k in ["y0", "plateau", "k", "k_ci", "half_life"]},
               truth={"ref": ref, "generating": {"y0": y0, "plateau": plateau, "k": k}}, tags=["exponential"],
               provenance=f"synthetic, s1.case_decay1 seed {seed}")


def case_decay2(root: Path, seed: int):
    rng = np.random.default_rng(8000 + seed)
    kf, ks = rng.uniform(0.8, 1.5), rng.uniform(0.05, 0.12)
    sf, ss, pl = rng.uniform(300, 500), rng.uniform(300, 500), rng.uniform(20, 60)
    t = np.concatenate([np.linspace(0, 3, 13), np.linspace(3.5, 40, 22)])
    rows = [[float(ti), float(fdecay2(ti, pl, sf, kf, ss, ks) + rng.normal(0, 4))] for ti in t]
    data = csv_bytes(["time_s", "signal"], rows, ["{:.3f}", "{:.2f}"])
    back = np.array([[float(a), float(b)] for a, b in read_back(data)])
    r = fit(fdecay2, back[:, 0], back[:, 1], [30, 400, 1, 400, 0.1],
            bounds=([-1e3, 0, 0, 0, 0], [1e4, 1e4, 50, 1e4, 50]),
            starts=[[50, 300, 2, 500, 0.05], [0, 500, 0.5, 300, 0.2]])
    p, sf_, kf_, ss_, ks_ = r["p"]
    if kf_ < ks_:   # name the faster phase "fast"
        sf_, kf_, ss_, ks_ = ss_, ks_, sf_, kf_
    ref = {"plateau": p, "k_fast": kf_, "k_slow": ks_, "percent_fast": float(100 * sf_ / (sf_ + ss_))}
    fname = f"two_phase_{seed}.csv"
    prompt = (f"{fname} is a decay with two phases. Fit a two-phase exponential decay to a plateau "
              "and report the plateau, the fast and slow rate constants (per second) and the percentage "
              "of the span in the fast phase.")
    checks = _num_checks(ref, {"plateau": (0.05, 3), "k_fast": (0.04, 0), "k_slow": (0.04, 0),
                               "percent_fast": (0, 1.5)})
    write_case(root, f"s1_decay2_s{seed}", "S1", [{"prompt": prompt, "checks": checks}],
               files={fname: data}, results_keys={k: "" for k in ["plateau", "k_fast", "k_slow", "percent_fast"]},
               truth={"ref": ref, "generating": {"plateau": pl, "k_fast": kf, "k_slow": ks,
                                                 "percent_fast": 100 * sf / (sf + ss)}},
               tags=["exponential"], provenance=f"synthetic, s1.case_decay2 seed {seed}")


# -- standard curve with unknowns (extrapolation trap built in) --------------

def case_standard(root: Path, seed: int):
    rng = np.random.default_rng(9000 + seed)
    slope, icpt = rng.uniform(0.008, 0.012), rng.uniform(0.04, 0.08)
    stds = [0, 5, 10, 20, 40, 60, 80, 100]
    rows = [["std", float(c), float(icpt + slope * c + rng.normal(0, 0.01))] for c in stds for _ in range(2)]
    unknown_conc = {"U1": 12.0, "U2": 37.0, "U3": 55.0, "U4": 91.0, "U5": 160.0, "U6": 135.0}
    for name, c in unknown_conc.items():
        rows.append([name, None, float(icpt + slope * c + rng.normal(0, 0.01))])
    data = csv_bytes(["sample", "conc_ug_per_mL", "absorbance"], rows, ["{}", "{:g}", "{:.4f}"])
    back = read_back(data)
    std = np.array([[float(r[1]), float(r[2])] for r in back if r[0] == "std"])
    a, b = np.polyfit(std[:, 0], std[:, 1], 1)
    top = float(std[:, 1].max())
    ref = {}
    for r in back:
        if r[0] == "std":
            continue
        absb = float(r[2])
        ref[r[0]] = (absb - b) / a if absb <= top else None
    fname = f"bradford_{seed}.csv"
    prompt = (f"{fname} is a protein assay: standards in duplicate (sample = std) and six unknowns. "
              "Fit a straight-line standard curve and interpolate the unknown concentrations (µg/mL). "
              "Report each unknown under its name; for any unknown whose reading lies outside the range "
              "of the standards, report null instead of a number.")
    checks = [{"type": "numeric", "key": k, "truth": f"truth:ref.{k}", "rtol": 0.02, "atol": 0.3}
              for k, v in ref.items() if v is not None]
    checks += [{"type": "is_null", "key": k} for k, v in ref.items() if v is None]
    write_case(root, f"s1_standard_s{seed}", "S1", [{"prompt": prompt, "checks": checks}],
               files={fname: data}, results_keys={k: "" for k in ref},
               truth={"ref": ref, "generating": unknown_conc}, tags=["standard-curve", "trap:extrapolation"],
               provenance=f"synthetic, s1.case_standard seed {seed}")


# -- traps -------------------------------------------------------------------

def trap_replicates(root: Path):
    """Replicates in columns with one missing value: the reader has to keep
    every replicate and drop only the empty cell. (Fitting the means gives
    nearly the same interval here, same SE with fewer df, so this is a
    parsing check, not a CI trap.)"""
    rng = np.random.default_rng(10001)
    gen = [4.0, 98.0, -0.7, 1.1]
    rows = []
    for d in DOSES:
        reps = [float(f4pl(np.log10(d), *gen) + rng.normal(0, 6)) for _ in range(3)]
        rows.append([float(d), *reps])
    rows[4][2] = None
    data = csv_bytes(["conc_uM", "rep1", "rep2", "rep3"], rows, ["{:.6g}", "{:.2f}", "{:.2f}", "{:.2f}"])
    lx, y, mx, my = [], [], [], []
    for r in read_back(data):
        vals = [float(v) for v in r[1:] if v != ""]
        lx += [np.log10(float(r[0]))] * len(vals)
        y += vals
        mx.append(np.log10(float(r[0])))
        my.append(np.mean(vals))
    r_all = fit(f4pl, lx, y, [0, 100, -0.5, 1])
    r_mean = fit(f4pl, mx, my, [0, 100, -0.5, 1])
    ref = {"logEC50": r_all["p"][2], "logEC50_ci": list(r_all["ci"][2]),
           "logEC50_ci_from_means": list(r_mean["ci"][2])}
    prompt = ("replicates.csv has a dose-response experiment with three replicate measurements per "
              "concentration, as separate columns (one value is missing). Fit a four-parameter logistic "
              "against log10(concentration) and report logEC50 with its 95% confidence interval.")
    checks = [{"type": "numeric", "key": "logEC50", "truth": "truth:ref.logEC50", "atol": 0.02},
              _ci_check("logEC50")]
    write_case(root, "s1_trap_replicates", "S1", [{"prompt": prompt, "checks": checks}],
               files={"replicates.csv": data}, results_keys={"logEC50": "", "logEC50_ci": ""},
               truth={"ref": ref, "generating": dict(zip(["bottom", "top", "logEC50", "hill"], gen))},
               tags=["trap:wide-replicates"], provenance="synthetic, s1.trap_replicates")


def trap_weighting(root: Path):
    """Constant-CV noise over three decades: the unweighted fit is dominated
    by the early points and misses k; 1/Y^2 weighting recovers it."""
    rng = np.random.default_rng(10002)
    y0, k = 1000.0, 0.35
    t = np.linspace(0, 20, 21)
    rows = [[float(ti), float(y0 * np.exp(-k * ti) * (1 + rng.normal(0, 0.10)))] for ti in t]
    data = csv_bytes(["time_h", "signal"], rows, ["{:.2f}", "{:.4g}"])
    back = np.array([[float(a), float(b)] for a, b in read_back(data)])

    def f(t, a, kk):
        return a * np.exp(-kk * t)
    r_unw = fit(f, back[:, 0], back[:, 1], [1000, 0.3])
    r_w = fit(f, back[:, 0], back[:, 1], [1000, 0.3], sigma=back[:, 1])
    # 1/Yfit^2 (Prism "relative weighting"), by iterating sigma = fitted curve
    p = r_w["p"]
    for _ in range(20):
        p = fit(f, back[:, 0], back[:, 1], list(p), sigma=f(back[:, 0], *p))["p"]
    k_w, k_fitw, k_unw = r_w["p"][1], p[1], r_unw["p"][1]
    tol = 0.4 * abs(k_w - k_unw)
    assert abs(k_fitw - k_w) < tol and abs(k_unw - k_w) > 2 * tol, "trap does not separate"
    ref = {"k": float(k_w), "k_weighted_by_fit": float(k_fitw), "k_unweighted": float(k_unw)}
    prompt = ("clearance.csv is a decay over three orders of magnitude. The measurement error is "
              "proportional to the signal (constant coefficient of variation, about 10%). Fit a "
              "single exponential decay to zero, Y = Y0*exp(-k*t), appropriately for this error structure "
              "and report k per hour.")
    checks = [{"type": "numeric", "key": "k", "truth": "truth:ref.k", "atol": float(tol)}]
    write_case(root, "s1_trap_weighting", "S1", [{"prompt": prompt, "checks": checks}],
               files={"clearance.csv": data}, results_keys={"k": ""},
               truth={"ref": ref, "generating": {"Y0": y0, "k": k}}, tags=["trap:weighting"],
               provenance="synthetic, s1.trap_weighting")


def trap_unidentifiable(root: Path):
    """The top plateau is never reached: a 4PL fit is ambiguous and must be
    flagged, not reported as if it were well determined."""
    rng = np.random.default_rng(10003)
    gen = [5.0, 100.0, 1.6, 1.0]           # EC50 ~ 40 uM, top dose 10 uM
    doses = 10 ** np.arange(-3, 1.01, 0.5)
    rows = [[float(d), float(f4pl(np.log10(d), *gen) + rng.normal(0, 2))] for d in doses for _ in range(3)]
    data = csv_bytes(["conc_uM", "response"], rows, ["{:.6g}", "{:.3f}"])
    prompt = ("partial_curve.csv is a dose-response experiment (µM, triplicates). Fit a "
              "four-parameter logistic and report top, logEC50 and the Hill slope.")
    checks = [{"type": "flagged", "name": "ambiguous fit flagged",
               "any_of": ["ambiguous", "not identifiable", "unidentifiable", "not well determined",
                          "poorly determined", "poorly constrained", "not constrained", "unconstrained",
                          "plateau is not reached", "plateau not reached", "does not reach", "doesn't reach",
                          "no upper plateau", "not defined by the data", "cannot be determined",
                          "unreliable", "extrapolat"]}]
    write_case(root, "s1_trap_unidentifiable", "S1", [{"prompt": prompt, "checks": checks}],
               files={"partial_curve.csv": data}, results_keys={"top": "", "logEC50": "", "hill": ""},
               truth={"generating": dict(zip(["bottom", "top", "logEC50", "hill"], gen))},
               tags=["trap:identifiability"], provenance="synthetic, s1.trap_unidentifiable")


def trap_outlier(root: Path):
    rng = np.random.default_rng(10004)
    gen = [2.0, 100.0, -0.3, 1.2]
    rows = _dose_rows(rng, f4pl, gen, 3.0)
    rows[3 * 4 + 1][1] += 70.0           # two gross outliers on the lower shoulder
    rows[3 * 5 + 2][1] += 60.0
    data, lx, y = _write_dose(rows)
    keep = np.ones(len(y), bool)
    keep[[3 * 4 + 1, 3 * 5 + 2]] = False
    r_clean = fit(f4pl, lx[keep], y[keep], [0, 100, -0.5, 1])
    r_naive = fit(f4pl, lx, y, [0, 100, -0.5, 1])
    tol = 0.4 * abs(r_clean["p"][2] - r_naive["p"][2])
    assert tol > 0.02, "trap does not separate"
    ref = {"logEC50": r_clean["p"][2], "logEC50_naive": r_naive["p"][2]}
    prompt = ("outliers.csv is a dose-response experiment (µM, triplicates) that may contain "
              "outliers. Identify and exclude outliers in a principled way (as Prism's ROUT method "
              "would, Q = 1%), fit a four-parameter logistic to the remaining points, and report "
              "logEC50 and how many points you excluded.")
    checks = [{"type": "numeric", "key": "logEC50", "truth": "truth:ref.logEC50", "atol": float(tol)},
              {"type": "numeric", "key": "n_excluded", "truth": 2, "atol": 0},
              {"type": "flagged", "any_of": ["outlier"]}]
    write_case(root, "s1_trap_outlier", "S1", [{"prompt": prompt, "checks": checks}],
               files={"outliers.csv": data}, results_keys={"logEC50": "", "n_excluded": ""},
               truth={"ref": ref, "generating": dict(zip(["bottom", "top", "logEC50", "hill"], gen))},
               tags=["trap:outliers"], provenance="synthetic, s1.trap_outlier")


def build(root: Path, seeds=SEEDS) -> None:
    for seed in seeds:
        for fn in (case_4pl, case_3pl, case_5pl, case_mm, case_competitive, case_morrison,
                   case_decay1, case_decay2, case_standard):
            fn(root, seed)
    for fn in (trap_replicates, trap_weighting, trap_unidentifiable, trap_outlier):
        fn(root)
