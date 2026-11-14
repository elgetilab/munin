"""S6 domain workflows: solution NMR first (HSQC titration, relaxation), then
the user's own solid-state data (NOESY buildup, 2H order profiles).

User data (fixtures/lab/userdata/) comes from the user's repositories
varghele/1H1H-NOESY (0e2b517, example/) and varghele/GNN2H (d030950,
data/2H_data/), copied with permission and relicensed with this repository.
Every reference value is recomputed here; nothing is taken from the old
scripts' outputs.
"""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

import numpy as np
from scipy import optimize

from common import fit, sig, write_case

USERDATA = Path(__file__).resolve().parent.parent / "userdata"
AA = "ACDEFGHIKLMNQRSTVWY"


def _zip(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name in sorted(files):
            info = zipfile.ZipInfo(name, date_time=(2026, 11, 3, 12, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            z.writestr(info, files[name])
    return buf.getvalue()


def _fb(p, l, kd):
    """Fraction of protein bound, single site with ligand depletion."""
    b = p + l + kd
    return (b - np.sqrt(b * b - 4 * p * l)) / (2 * p)


# -- HSQC titration ----------------------------------------------------------

def hsqc_titration(root: Path, seed: int):
    rng = np.random.default_rng(60000 + seed)
    n = 60
    seq = list("M" + "".join(rng.choice(list(AA), n - 1)))
    for i in (19, 33, 47):                         # prolines: never an amide peak
        seq[i - 1] = "P"
    seq = "".join(seq)
    kd = float(rng.uniform(60, 250))
    p_conc = 100.0
    lig = [0, 25, 50, 100, 200, 400, 800, 1600]
    site = set(rng.choice(np.arange(10, n), 9, replace=False).tolist())
    broaden = sorted(site)[:2]                      # vanish at intermediate points
    lost_last = 26 if 26 not in site else 27        # overlapped at the last point
    res = []
    for i, aa in enumerate(seq, start=1):
        if i == 1 or aa == "P":
            continue                                # no amide peak
        h0, n0 = rng.uniform(7.0, 9.6), rng.uniform(104, 130)
        if i in site:
            dh, dn = rng.choice([-1, 1]) * rng.uniform(0.08, 0.30), rng.choice([-1, 1]) * rng.uniform(0.5, 2.5)
        else:
            dh, dn = rng.normal(0, 0.004), rng.normal(0, 0.03)
        res.append((f"{aa}{i}", h0, n0, dh, dn))
    files, observed = {}, {}
    for k, l in enumerate(lig):
        f = _fb(p_conc, l, kd)
        lines = ["      Assignment         w1         w2   Data Height", ""]
        for name, h0, n0, dh, dn in res:
            idx = int(name[1:])
            if idx in broaden and k in (2, 3, 4):
                continue
            if idx == lost_last and k == len(lig) - 1:
                continue
            h = h0 + f * dh + rng.normal(0, 0.0015)
            nn = n0 + f * dn + rng.normal(0, 0.012)
            height = int(rng.uniform(2e5, 9e5) * (0.4 if idx in site and 0 < k < 6 else 1.0))
            lines.append(f"{name + 'N-H':>16} {nn:10.3f} {h:10.3f} {height:13d}")
            observed.setdefault(name, {})[k] = (round(h, 3), round(nn, 3))
        files[f"titration/L{k}_{l:04d}uM.list"] = ("\n".join(lines) + "\n").encode()
    files["titration/README.txt"] = (
        "Sparky peak lists of a 15N-HSQC titration. Protein 100 uM throughout (volume change negligible).\n"
        "Lk_XXXXuM.list = titration point k at XXXX uM total ligand. w1 = 15N (ppm), w2 = 1H (ppm).\n").encode()
    data = _zip(files)
    alpha = 0.14
    last = len(lig) - 1
    csp = {}
    for name, *_ in res:
        o = observed.get(name, {})
        if 0 in o and last in o:
            dh = o[last][0] - o[0][0]
            dn = o[last][1] - o[0][1]
            csp[name] = float(np.sqrt(dh ** 2 + (alpha * dn) ** 2))
        else:
            csp[name] = None
    sel = [r for r, v in csp.items() if v is not None and v > 0.05 and len(observed[r]) == len(lig)]
    # global fit: shared Kd, per-residue CSPmax
    L = np.array(lig, float)

    def resid(params):
        kd_ = params[0]
        out = []
        for j, r in enumerate(sel):
            o = observed[r]
            obs = np.array([np.sqrt((o[k][0] - o[0][0]) ** 2 + (alpha * (o[k][1] - o[0][1])) ** 2) for k in range(len(lig))])
            out.append(params[1 + j] * _fb(p_conc, L, kd_) - obs)
        return np.concatenate(out)
    x0 = [150.0] + [csp[r] for r in sel]
    sol = optimize.least_squares(resid, x0, bounds=([1e-3] + [0] * len(sel), [1e5] + [5] * len(sel)),
                                 xtol=1e-12, ftol=1e-12)
    kd_fit = float(sol.x[0])
    prompt = ("hsqc_titration.zip holds Sparky peak lists from a 15N-HSQC titration of a 100 µM protein with "
              "a ligand (README inside). For every assigned residue, compute the combined chemical shift "
              "perturbation between the first (0 µM) and the last (1600 µM) point as "
              "CSP = sqrt(Δδ_H² + (α·Δδ_N)²) with α = 0.14, keyed by residue (e.g. 'K12', without 'N-H'). "
              "A residue missing from either spectrum gets null; do not drop or interpolate it. Then fit a "
              "single-site binding model with ligand depletion globally (one shared Kd, a CSPmax per "
              "residue) to the CSP of every titration point, using only residues whose final CSP exceeds "
              "0.05 ppm and that are present at every point. Report the CSP map as 'csp', the residues used "
              "as 'fit_residues' and Kd in µM as 'kd_uM'. Plot CSP per residue as a bar chart.")
    checks = [{"type": "numeric_map", "key": "csp", "truth": "truth:csp", "atol": 0.002, "min_frac": 0.95},
              {"type": "numeric", "key": "kd_uM", "truth": "truth:kd_uM", "log": True, "atol": 0.05}]
    write_case(root, f"s6_hsqc_titration_s{seed}", "S6", [{"prompt": prompt, "checks": checks}],
               files={"hsqc_titration.zip": data}, results_keys={"csp": "", "fit_residues": "", "kd_uM": ""},
               truth={"csp": csp, "kd_uM": kd_fit, "fit_residues": sel, "generating": {"Kd_uM": kd},
                      "broadened": [r for r, *_ in res if int(r[1:]) in broaden]},
               tags=["nmr", "titration", "trap:missing-residues"],
               provenance=f"synthetic, s6.hsqc_titration seed {seed}")


# -- relaxation ----------------------------------------------------------------

def relaxation(root: Path):
    rng = np.random.default_rng(61001)
    names = [f"{aa}{i}" for i, aa in enumerate(rng.choice(list(AA), 40), start=3)]
    r1 = rng.uniform(1.1, 1.6, len(names))
    r2 = rng.uniform(9, 14, len(names))
    noe = rng.uniform(0.72, 0.85, len(names))
    for j in (0, 1, 2, 38, 39):                     # flexible termini
        r1[j], r2[j], noe[j] = rng.uniform(1.6, 2.0), rng.uniform(3, 6), rng.uniform(0.1, 0.45)
    for j in (17, 18):                              # conformational exchange
        r2[j] = rng.uniform(22, 30)
    d1 = [0.02, 0.10, 0.20, 0.40, 0.60, 0.80, 1.20, 1.60]
    d2 = [0.0163, 0.0326, 0.0490, 0.0653, 0.0979, 0.1306, 0.1632, 0.1958, 0.2448]

    def table(delays, rates):
        head = "residue," + ",".join(f"{d * 1000:.1f}ms" for d in delays)
        lines = [head]
        rows = {}
        for name, rate in zip(names, rates):
            i0 = rng.uniform(4e5, 1.2e6)
            vals = [i0 * np.exp(-rate * d) + rng.normal(0, 0.012 * i0) for d in delays]
            vals = [round(v) for v in vals]
            rows[name] = vals
            lines.append(name + "," + ",".join(str(v) for v in vals))
        return ("\n".join(lines) + "\n").encode(), rows
    t1, rows1 = table(d1, r1)
    t2, rows2 = table(d2, r2)
    nl = ["residue,I_sat,I_ref"]
    noe_ref = {}
    for name, x in zip(names, noe):
        ref_i = rng.uniform(4e5, 1e6)
        sat = round(ref_i * x + rng.normal(0, 0.01 * ref_i))
        ref_i = round(ref_i + rng.normal(0, 0.01 * ref_i))
        nl.append(f"{name},{sat},{ref_i}")
        noe_ref[name] = sat / ref_i

    def exp2(t, i0, r):
        return i0 * np.exp(-r * t)
    R1 = {k: float(fit(exp2, d1, v, [v[0], 1.0])["p"][1]) for k, v in rows1.items()}
    R2 = {k: float(fit(exp2, d2, v, [v[0], 10.0])["p"][1]) for k, v in rows2.items()}
    rigid = [k for k in names if noe_ref[k] > 0.65]
    ratio = float(np.mean([R2[k] / R1[k] for k in rigid]))
    files = {"R1_intensities.csv": t1, "R2_intensities.csv": t2, "hetNOE.csv": ("\n".join(nl) + "\n").encode()}
    prompt = ("R1_intensities.csv and R2_intensities.csv hold 15N relaxation peak intensities per residue "
              "at the delays in the column headers; hetNOE.csv has saturated and reference intensities. Fit a "
              "two-parameter mono-exponential decay I = I0·exp(-R·t) per residue to get R1 and R2 in s⁻¹, and "
              "compute the heteronuclear NOE as I_sat/I_ref. Report the three per-residue maps as 'R1', 'R2' "
              "and 'hetNOE', and the mean R2/R1 over residues with hetNOE > 0.65 as 'mean_R2_R1_rigid'. "
              "Plot all three against residue number.")
    checks = [{"type": "numeric_map", "key": "R1", "truth": "truth:R1", "rtol": 0.01, "min_frac": 0.95},
              {"type": "numeric_map", "key": "R2", "truth": "truth:R2", "rtol": 0.01, "min_frac": 0.95},
              {"type": "numeric_map", "key": "hetNOE", "truth": "truth:hetNOE", "atol": 0.002, "min_frac": 0.95},
              {"type": "numeric", "key": "mean_R2_R1_rigid", "truth": "truth:mean_R2_R1_rigid", "rtol": 0.01}]
    write_case(root, "s6_relaxation", "S6", [{"prompt": prompt, "checks": checks}], files=files,
               results_keys={"R1": "", "R2": "", "hetNOE": "", "mean_R2_R1_rigid": ""},
               truth={"R1": R1, "R2": R2, "hetNOE": noe_ref, "mean_R2_R1_rigid": ratio},
               tags=["nmr", "relaxation"], provenance="synthetic, s6.relaxation")


# -- NOESY buildup (user data) -------------------------------------------------

def _topspin_integrals(data: bytes) -> list[tuple[float, float, float]]:
    rows = []
    for line in data.decode("latin-1").splitlines()[1:]:
        parts = line.strip().split("\t")
        if len(parts) >= 4 and parts[0].startswith("Integral"):
            rows.append((float(parts[1]), float(parts[2]), float(parts[3])))
    return rows


def noesy(root: Path):
    src = USERDATA / "noesy_ceritinib"
    files = {p.name: p.read_bytes() for p in sorted(src.iterdir())}
    ligand = [l.strip() for l in files["ligand_peaks.txt"].decode().splitlines() if l.strip()]
    lipid = [l.strip() for l in files["lipid_groups.txt"].decode().splitlines() if l.strip()]
    lig_name, lig_peaks = ligand[0], ligand[1:]
    lip_name, lip_groups = lipid[0], lipid[1:]
    diag = _topspin_integrals(files["01ms.txt"])
    times = [0.1, 0.2, 0.3, 0.5]
    cross = {t: _topspin_integrals(files[f"{int(t * 1000)}ms.txt"]) for t in times}
    starts = [[0.001, 0.4], [0.01, 0.4], [1, 1], [0.1, 0.4], [0.1, 7], [5, 0.5], [0.5, 5], [0.2, 0.9], [0.27, 0.42]]
    sigma = {}
    for ci, lp in enumerate(lig_peaks):
        for li, lg in enumerate(lip_groups):
            a0 = diag[li][0]
            row = ci * len(lip_groups) + li
            t = np.array([0.0] + times)
            a = np.array([0.0] + [cross[x][row][0] for x in times])

            def resid(p):
                s, tis = p
                return (a0 / 2) * (1 - np.exp(-2 * s * t)) * np.exp(-t / tis) - a
            best = None
            for st in starts:
                try:
                    r = optimize.least_squares(resid, st, method="lm", xtol=1e-14, ftol=1e-14, max_nfev=20000)
                except Exception:
                    continue
                if best is None or r.cost < best.cost:
                    best = r
            sigma[f"{lp}|{lg}"] = float(best.x[0])
    prompt = (f"These are TopSpin integral exports of 1H-1H NOESY MAS NMR spectra of the drug {lig_name} in "
              f"{lip_name} membranes. 01ms.txt holds the diagonal peak integrals of the lipid groups at "
              "1 ms mixing time, in the order of lipid_groups.txt (first line is the lipid name). "
              "100ms.txt ... 500ms.txt hold the cross-peak integrals between each drug peak (ligand_peaks.txt, "
              "first line is the drug name) and each lipid group, ordered drug peak by drug peak and, within "
              "each, lipid group by lipid group. For each drug peak / lipid group pair, fit the NOE buildup "
              "A(t) = (A_ii(0)/2)·(1 − exp(−2σt))·exp(−t/T) with t in seconds, A_ii(0) the 1 ms diagonal "
              "integral of that lipid group, and the point A(0) = 0 included, for the cross-relaxation rate σ "
              "(s⁻¹) and T. Report σ keyed as '<drug ppm>|<lipid group>' (e.g. '8.6|CH3') under 'sigma', and "
              "plot σ per lipid group as grouped bars, one group per drug peak.")
    checks = [{"type": "numeric_map", "key": "sigma", "truth": "truth:sigma", "rtol": 0.03, "atol": 1e-4,
               "min_frac": 0.85}]
    write_case(root, "s6_noesy_buildup", "S6", [{"prompt": prompt, "checks": checks}],
               files=files, results_keys={"sigma": ""}, truth={"sigma": sigma},
               tags=["nmr", "solid-state", "user-data"],
               provenance="user data, varghele/1H1H-NOESY 0e2b517 example/ (Ceritinib in POPC); "
                          "reference sigma recomputed in s6.noesy")


# -- 2H order profile (user data) ----------------------------------------------

def order_2h(root: Path):
    src = USERDATA / "order_2h"
    pure = src / "2IBU303POPC_0_0_POPC_0_0_100_303_wt40.txt"
    drug = src / "2IBU303POPC_IBU_0_POPC_10_0_90_303_wt40.txt"

    def read(p):
        out = {}
        for line in p.read_text().splitlines():
            if line.strip():
                n, s = line.split(",")
                out[int(n)] = float(s)
        return out
    a, b = read(pure), read(drug)
    delta = {str(n): b[n] - a[n] for n in sorted(set(a) & set(b))}
    plateau = float(np.mean([delta[str(n)] for n in range(2, 9)]))
    files = {"POPC_pure_303K.txt": pure.read_bytes(), "POPC_ibuprofen10_303K.txt": drug.read_bytes()}
    prompt = ("The two files hold smoothed 2H NMR acyl chain order parameters S_CD of POPC (sn-1 chain, "
              "carbon number n, comma-separated, no header) at 303 K: pure POPC and POPC with 10 mol% "
              "ibuprofen. Compute ΔS = S(with drug) − S(pure) per carbon (key 'delta_S', keyed by carbon "
              "number as a string), the mean ΔS over the plateau region C2 to C8 ('plateau_mean_delta'), and "
              "plot both order profiles against carbon number.")
    checks = [{"type": "numeric_map", "key": "delta_S", "truth": "truth:delta_S", "atol": 1e-4, "min_frac": 1.0},
              {"type": "numeric", "key": "plateau_mean_delta", "truth": "truth:plateau_mean_delta", "atol": 1e-4}]
    write_case(root, "s6_order_2h", "S6", [{"prompt": prompt, "checks": checks}], files=files,
               results_keys={"delta_S": "", "plateau_mean_delta": ""},
               truth={"delta_S": delta, "plateau_mean_delta": plateau},
               tags=["nmr", "solid-state", "user-data"],
               provenance="user data, varghele/GNN2H d030950 data/2H_data/ (2IBU set)")


def build(root: Path) -> None:
    hsqc_titration(root, 1)
    hsqc_titration(root, 2)
    relaxation(root)
    noesy(root)
    order_2h(root)
