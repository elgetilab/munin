"""NMR helpers: Bruker 1D processing with every step explicit, peak
integration, chemical shift perturbations, Kd from titrations, relaxation.

    from labkit import nmr
    raw = nmr.read_bruker("inputs/bruker_1h.zip")      # dir, zip, or a file inside the dir
    raw.params                                          # SW (ppm), SW_h (Hz), SFO1, O1, DTYPA, GRPDLY ...
    spec = nmr.process_1d(raw, phase=(p0, p1))          # or autophase=True (the phases used are recorded)
    spec = nmr.reference(spec, ppm=0.0, near=0.0)       # put the tallest peak near 0 ppm at 0.00 (DSS/TMS)
    nmr.integrate(spec, [(3.2, 3.4), (7.1, 7.3)])

Units the code never mixes up: SW is in ppm, SW_h in Hz, SFO1 in MHz.
Dimension/acquisition values are read from acqus, never assumed.
"""
from __future__ import annotations

import io
import math
import os
import shutil
import tempfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional, Sequence

import numpy as np

from ._attrs import FieldHelp
from .fit import Model, fit, fit_global


# ==============================================================================
# Bruker reading and 1D processing
# ==============================================================================

@dataclass
class BrukerData(FieldHelp):
    fid: np.ndarray
    dic: dict
    path: str
    params: dict

    def __repr__(self):
        p = self.params
        return (f"BrukerData({self.path}: {p.get('TD_complex')} complex points, SFO1 {p.get('SFO1')} MHz, "
                f"SW {p.get('SW_ppm')} ppm = SW_h {p.get('SW_h')} Hz, O1 {p.get('O1_Hz')} Hz, "
                f"dtype {p.get('dtype')}, GRPDLY {p.get('GRPDLY')}, pulse program {p.get('PULPROG')})")


@dataclass
class Spectrum(FieldHelp):
    ppm: np.ndarray
    real: np.ndarray
    steps: list[str] = field(default_factory=list)
    phase: tuple = (0.0, 0.0)
    ref_offset_ppm: float = 0.0

    def __repr__(self):
        return (f"Spectrum({self.ppm.size} points, {self.ppm.min():.3f} to {self.ppm.max():.3f} ppm, "
                f"phase p0={self.phase[0]:.2f} p1={self.phase[1]:.2f} deg; steps: {'; '.join(self.steps)})")


def _find_acqus_dir(root: Path) -> Path:
    if root.is_file() and root.name in ("fid", "ser", "acqus"):
        return root.parent
    if (root / "acqus").exists():
        return root
    hits = sorted(p.parent for p in root.rglob("acqus"))
    if not hits:
        raise ValueError(f"no acqus file under {root}: not a Bruker experiment directory")
    with_fid = [h for h in hits if (h / "fid").exists() or (h / "ser").exists()]
    if len(with_fid) > 1:
        raise ValueError("several Bruker experiments found; pass the one you mean: "
                         + ", ".join(str(h) for h in with_fid))
    return (with_fid or hits)[0]


def read_bruker(path) -> BrukerData:
    """Read a Bruker 1D experiment from a directory, a zip, or a file in it.

    Uses nmrglue, which takes the data type from DTYPA (0 = int32,
    2 = float64) and the byte order from BYTORDA; never read the fid with
    np.fromfile and a guessed dtype.
    """
    import nmrglue as ng
    p = Path(path)
    tmp = None
    try:
        if p.is_file() and zipfile.is_zipfile(p):
            tmp = tempfile.mkdtemp(prefix="labkit-bruker-")
            with zipfile.ZipFile(p) as z:
                z.extractall(tmp)
            d = _find_acqus_dir(Path(tmp))
        else:
            d = _find_acqus_dir(p)
        dic, data = ng.bruker.read(str(d))
    finally:
        pass
    acq = dic.get("acqus", {})
    sw_ppm = float(acq.get("SW", float("nan")))
    sw_h = float(acq.get("SW_h", float("nan")))
    sfo1 = float(acq.get("SFO1", float("nan")))
    params = {
        "PULPROG": acq.get("PULPROG"), "NUC1": acq.get("NUC1"),
        "SFO1": sfo1, "BF1": float(acq.get("BF1", float("nan"))),
        "O1_Hz": float(acq.get("O1", float("nan"))),
        "O1_ppm": float(acq.get("O1", float("nan"))) / float(acq.get("BF1", sfo1) or sfo1),
        "SW_ppm": sw_ppm, "SW_h": sw_h, "TD": acq.get("TD"), "TD_complex": int(np.size(data)),
        "DTYPA": acq.get("DTYPA"), "BYTORDA": acq.get("BYTORDA"),
        "dtype": {0: "int32", 1: "float32", 2: "float64"}.get(acq.get("DTYPA"), f"DTYPA={acq.get('DTYPA')}"),
        "DECIM": acq.get("DECIM"), "DSPFVS": acq.get("DSPFVS"), "GRPDLY": acq.get("GRPDLY"),
        "NS": acq.get("NS"), "D1": (acq.get("D") or [None, None])[1] if isinstance(acq.get("D"), list) else None,
        "SF_procs": (dic.get("procs") or {}).get("SF"), "OFFSET_procs": (dic.get("procs") or {}).get("OFFSET"),
    }
    if np.isfinite(sw_ppm) and np.isfinite(sfo1) and np.isfinite(sw_h):
        if abs(sw_ppm * sfo1 - sw_h) > 0.01 * sw_h:
            params["warning"] = (f"SW*SFO1 = {sw_ppm * sfo1:.1f} Hz but SW_h = {sw_h:.1f} Hz: "
                                 f"check which parameter set is right")
    out = BrukerData(fid=np.asarray(data), dic=dic, path=str(d), params=params)
    if tmp:
        shutil.rmtree(tmp, ignore_errors=True)
    return out


def process_1d(raw: BrukerData, *, phase: Optional[tuple] = None, autophase: bool = False,
               zero_fill: Optional[int] = None, lb_hz: float = 0.0) -> Spectrum:
    """Digital filter removal, optional line broadening, zero filling, FT,
    phase correction, ppm axis from acqus. Phasing must be chosen: give
    phase=(p0, p1) in degrees, or autophase=True (ACME), whose result is
    recorded in the returned Spectrum.phase."""
    import nmrglue as ng
    if phase is None and not autophase:
        raise ValueError("choose phasing: phase=(p0_deg, p1_deg) or autophase=True")
    steps = []
    data = ng.bruker.remove_digital_filter(raw.dic, raw.fid)
    steps.append(f"digital filter removed (GRPDLY={raw.params.get('GRPDLY')})")
    if lb_hz:
        sw = raw.params["SW_h"]
        data = ng.proc_base.em(data, lb=lb_hz / sw)
        steps.append(f"exponential line broadening {lb_hz} Hz")
    size = zero_fill or int(2 ** math.ceil(math.log2(max(data.size, 2)) + 1))
    data = ng.proc_base.zf_size(data, size)
    steps.append(f"zero filled to {size}")
    spec = ng.proc_base.fft(data)
    steps.append("Fourier transform")
    if autophase:
        spec, (p0, p1) = ng.proc_autophase.autops(spec, "acme", return_phases=True, disp=False)
        steps.append(f"autophased (ACME): p0={p0:.2f} p1={p1:.2f} deg")
    else:
        p0, p1 = phase
        spec = ng.proc_base.ps(spec, p0=p0, p1=p1)
        steps.append(f"phased p0={p0:.2f} p1={p1:.2f} deg")
    real = ng.proc_base.rev(ng.proc_base.di(spec))
    udic = ng.bruker.guess_udic(raw.dic, real)
    udic[0]["size"] = real.size
    udic[0]["complex"] = False
    uc = ng.fileiobase.uc_from_udic(udic)
    ppm = uc.ppm_scale()
    steps.append(f"ppm axis from acqus (SFO1 {raw.params['SFO1']} MHz, SW_h {raw.params['SW_h']} Hz, "
                 f"O1 {raw.params['O1_Hz']} Hz)")
    return Spectrum(ppm=np.asarray(ppm, float), real=np.asarray(real, float), steps=steps, phase=(float(p0), float(p1)))


def reference(spec: Spectrum, *, ppm: float = 0.0, near: Optional[float] = None, window: float = 0.3) -> Spectrum:
    """Shift the axis so the tallest peak within `window` ppm of `near`
    (default: of `ppm`) sits at `ppm` (DSS/TMS: ppm=0)."""
    centre = ppm if near is None else near
    mask = np.abs(spec.ppm - centre) <= window
    if not mask.any():
        raise ValueError(f"no data within {window} ppm of {centre}")
    i = np.flatnonzero(mask)[np.argmax(spec.real[mask])]
    off = ppm - spec.ppm[i]
    return Spectrum(ppm=spec.ppm + off, real=spec.real, steps=spec.steps + [f"referenced: peak at {spec.ppm[i]:.4f} -> {ppm} ppm"],
                    phase=spec.phase, ref_offset_ppm=spec.ref_offset_ppm + off)


def peaks(spec: Spectrum, *, threshold: Optional[float] = None, min_separation_ppm: float = 0.02,
          region: Optional[tuple] = None, min_rel_height: float = 0.005) -> list[dict]:
    """Peak maxima, with ppm refined by a parabola through the top 3 points.

    The baseline level and the noise come from the outer 10% of the
    spectrum at each end. A peak must rise at least 5x the noise above the
    baseline and stand out by 5x the noise from its surroundings
    (prominence), and reach `min_rel_height` (default 0.5%) of the tallest
    peak above the baseline, which drops the truncation ripples beside
    strong lines (set 0 to keep everything). `threshold`, if given, is an
    absolute height instead.
    "snr" is the height above the baseline over the noise. (Up to 0.1.2 the
    threshold ignored the baseline, and a spectrum whose baseline sat above
    zero returned hundreds of noise peaks.)"""
    from scipy.signal import find_peaks
    y, x = spec.real, spec.ppm
    n = y.size
    edge = np.r_[y[: n // 10], y[-n // 10:]]
    base = float(np.median(edge))
    noise = float(1.4826 * np.median(np.abs(edge - base))) or float(np.std(edge)) or 1e-12
    tallest = float(np.max(y)) - base
    thr = threshold if threshold is not None else base + max(5 * noise, min_rel_height * tallest)
    step = abs(x[1] - x[0])
    idx, props = find_peaks(y, height=thr, prominence=5 * noise,
                            distance=max(1, int(min_separation_ppm / step)))
    out = []
    for i in idx:
        if region and not (min(region) <= x[i] <= max(region)):
            continue
        if 0 < i < n - 1:
            a, b, c = y[i - 1], y[i], y[i + 1]
            den = a - 2 * b + c
            d = 0.5 * (a - c) / den if den else 0.0
            ppm_i = x[i] + d * (x[i + 1] - x[i])
        else:
            ppm_i = x[i]
        out.append({"ppm": float(ppm_i), "height": float(y[i]), "snr": float((y[i] - base) / noise)})
    return sorted(out, key=lambda r: r["ppm"])


def integrate(spec: Spectrum, regions: Sequence[tuple], *, baseline: str = "linear",
              relative_to: Optional[int] = None) -> list[dict]:
    """Integrate each (ppm_a, ppm_b) region. baseline='linear' subtracts a
    line through the region's end points; 'none' does not. With
    relative_to=k, adds 'relative' = integral / integral of region k."""
    out = []
    for a, b in regions:
        lo, hi = min(a, b), max(a, b)
        m = (spec.ppm >= lo) & (spec.ppm <= hi)
        if m.sum() < 3:
            raise ValueError(f"region {a}-{b} ppm holds fewer than 3 points")
        x, y = spec.ppm[m], spec.real[m]
        order = np.argsort(x)
        x, y = x[order], y[order]
        if baseline == "linear":
            y = y - np.interp(x, [x[0], x[-1]], [y[0], y[-1]])
        elif baseline != "none":
            raise ValueError("baseline: 'linear' or 'none'")
        out.append({"region": (lo, hi), "integral": float(np.trapz(y, x))})
    if relative_to is not None:
        ref = out[relative_to]["integral"]
        for r in out:
            r["relative"] = r["integral"] / ref if ref else float("nan")
    return out


# ==============================================================================
# Chemical shift perturbation and titrations
# ==============================================================================

def _read_text(path) -> str:
    raw = Path(path).read_bytes()
    for enc in ("utf-8-sig", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1", errors="replace")


def read_sparky(path) -> dict:
    """A Sparky 2D 1H-15N peak list as {residue: (1H ppm, 15N ppm)}, H FIRST,
    the order csp() takes.

    Sparky writes w1 = 15N and w2 = 1H; which column is which is decided
    from the values (amide 1H 5-12 ppm, 15N 95-140 ppm), not assumed.
    Backbone amides lose the "N-H" suffix ("G2N-H" -> "G2"); other
    assignments (side chains, "W48NE1-HE1") keep their full label;
    unassigned peaks ("?-?") are skipped and counted in out.warnings.
    """
    rows, skipped = [], 0
    for line in _read_text(path).splitlines():
        t = line.split()
        if len(t) < 3 or t[0].lower() == "assignment":
            continue
        try:
            a, b = float(t[1]), float(t[2])
        except ValueError:
            continue
        if t[0].startswith("?"):
            skipped += 1
            continue
        rows.append((t[0], a, b))
    if not rows:
        raise ValueError(f"{path}: no peak rows (expected 'Assignment w1 w2 ...')")
    m1 = float(np.median([r[1] for r in rows]))
    m2 = float(np.median([r[2] for r in rows]))
    if m1 > 50 > m2:
        h_first = False
    elif m2 > 50 > m1:
        h_first = True
    else:
        raise ValueError(f"{path}: cannot tell 1H from 15N (column medians {m1:.1f} and {m2:.1f} ppm); "
                         "build {residue: (H, N)} yourself")
    out = _Peaks()
    for label, a, b in rows:
        key = label[:-3] if label.endswith("N-H") else label
        out[key] = (a, b) if h_first else (b, a)
    if skipped:
        out.warnings.append(f"{skipped} unassigned peak(s) skipped")
    return out


class _Peaks(dict):
    """dict of {residue: (H, N)} that also carries .warnings."""

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.warnings: list[str] = []


def read_integrals(path):
    """A TopSpin integral list ("Object  Integral [abs]  (F2) [ppm]  (F1) [ppm]")
    as a DataFrame in file order: columns integral, f2_ppm and f1_ppm (2D) or
    ppm (1D). Rows are numbered as in the file (Integral 1 = row 0)."""
    import pandas as pd
    lines = _read_text(path).splitlines()
    header = None
    rows = []
    for line in lines:
        t = [c.strip() for c in line.strip().split("\t")] if "\t" in line else line.split()
        if not t:
            continue
        if header is None and t[0].lower().startswith("object"):
            header = t
            continue
        if t[0].lower().startswith("integral"):
            nums = t[1:] if t[0].lower() != "integral" else t[2:]
            try:
                rows.append([float(v.replace(",", ".")) for v in nums if v])
            except ValueError:
                continue
    if not rows:
        raise ValueError(f"{path}: no 'Integral N' rows; not a TopSpin integral list")
    width = min(len(r) for r in rows)
    names = ["integral", "f2_ppm", "f1_ppm"] if width >= 3 else ["integral", "ppm"][:width]
    if header:
        h = " ".join(header).upper()
        if width >= 3 and "F1" in h and h.index("F1") < h.index("F2"):
            names = ["integral", "f1_ppm", "f2_ppm"]
    df = pd.DataFrame([r[:len(names)] for r in rows], columns=names)
    df.attrs["source"] = str(path)
    return df


def _check_hn_order(shifts: dict) -> None:
    """Raise when the tuples are (15N, 1H) instead of (1H, 15N).

    Sparky and most peak lists give w1 = 15N first; in Phase 4 round 3 the
    model passed them in that order and got CSPs 2-7x too large with no
    error. Amide 1H sits near 6-11 ppm and 15N near 100-135 ppm, so the
    medians tell the order apart.
    """
    pairs = [v for v in shifts.values() if v is not None and None not in v]
    if len(pairs) < 3:
        return
    h = float(np.median([p[0] for p in pairs]))
    n = float(np.median([p[1] for p in pairs]))
    if h > 50 and n < 50:
        raise ValueError(
            f"csp expects (dH, dN) = (1H ppm, 15N ppm) per residue, but the first values (median {h:.1f} ppm) "
            f"look like 15N and the second (median {n:.1f} ppm) like 1H. Peak lists such as Sparky's give "
            "w1 = 15N first: swap them to (H, N).")


def csp(free: dict, bound: dict, *, alpha: float, scheme: str) -> dict:
    """Combined 1H/15N chemical shift perturbation per residue.

    free, bound  {residue: (dH_ppm, dN_ppm)}; residues missing from either
                 (or None) are returned as None, never as 0.
    alpha        15N weighting, REQUIRED (commonly 0.14 or 0.2; say which).
    scheme       REQUIRED: 'williamson' = sqrt(0.5*(dH^2 + (alpha*dN)^2))
                 (Williamson 2013), or 'euclidean' = sqrt(dH^2 + (alpha*dN)^2).
    """
    if scheme not in ("williamson", "euclidean"):
        raise ValueError("scheme='williamson' (sqrt(0.5*(...))) or 'euclidean' (sqrt(...)); the two differ by sqrt(2)")
    _check_hn_order(free)
    _check_hn_order(bound)
    out = {}
    for res in sorted(set(free) | set(bound), key=_res_key):
        f, b = free.get(res), bound.get(res)
        if f is None or b is None or any(v is None for v in (*f, *b)):
            out[res] = None
            continue
        dh, dn = b[0] - f[0], b[1] - f[1]
        s = dh * dh + (alpha * dn) ** 2
        out[res] = math.sqrt(0.5 * s) if scheme == "williamson" else math.sqrt(s)
    return out


def _res_key(r):
    try:
        return (0, int("".join(ch for ch in str(r) if ch.isdigit()) or 0), str(r))
    except ValueError:
        return (1, 0, str(r))


def _binding_isotherm(L, csp_max, kd, P):
    s = P + L + kd
    return csp_max * (s - np.sqrt(np.maximum(s * s - 4 * P * L, 0.0))) / (2 * P)


def kd_fit(ligand_conc, csp_by_residue: dict, *, protein_conc, residues: Optional[Sequence] = None,
           level: float = 0.95) -> dict:
    """Global Kd from an NMR titration with ligand depletion (1:1 binding).

    ligand_conc      total ligand at each point (same units as protein_conc)
    csp_by_residue   {residue: [CSP at each point]} (None/NaN = missing)
    protein_conc     total protein (scalar, or an array if it changed by dilution)
    One Kd shared by all residues, one CSPmax per residue.
    Returns {"Kd", "Kd_ci", "per_residue": {res: {"CSPmax", "CSPmax_ci"}}, "result"}.
    """
    L = np.asarray(ligand_conc, float)
    P = np.broadcast_to(np.asarray(protein_conc, float), L.shape)
    res = list(residues) if residues is not None else list(csp_by_residue)
    datasets = []
    for r in res:
        y = np.asarray([np.nan if v is None else v for v in csp_by_residue[r]], float)
        datasets.append(((L, P), y))
    model = Model("isotherm", lambda x, CSPmax, Kd: _binding_isotherm(x[0], CSPmax, Kd, x[1]),
                  ["CSPmax", "Kd"], "CSP = CSPmax*((P+L+Kd) - sqrt((P+L+Kd)^2 - 4PL))/(2P)",
                  {"CSPmax": "CSP at saturation (ppm)", "Kd": "dissociation constant (ligand units)"},
                  guess=lambda x, y: [float(np.nanmax(y)) * 1.2, float(np.nanmedian(x[0])) or 1.0],
                  bounds={"Kd": (1e-12, None)})
    g = fit_global(model, datasets, shared=["Kd"], level=level)
    per = {r: {"CSPmax": d["CSPmax"], "CSPmax_ci": d.get("CSPmax_ci")} for r, d in zip(res, g["per_dataset"])}
    return {"Kd": g["shared"]["Kd"], "Kd_ci": g["shared_ci"]["Kd"], "per_residue": per, "result": g["result"]}


# ==============================================================================
# Relaxation
# ==============================================================================

def relaxation_fit(delays, intensities: dict, *, kind: str = "decay", level: float = 0.95) -> dict:
    """Per-residue relaxation rates from peak intensities vs delay.

    delays       relaxation delays in SECONDS (convert ms first)
    intensities  {residue: [I at each delay]}
    kind         'decay' (I = I0*exp(-R*t), R1 by saturation-recovery-free
                 pseudo-3D and R2/CPMG) or 'inversion_recovery'
                 (I = I0*(1 - 2*exp(-R*t))).
    Returns {residue: {"R": rate in 1/s, "R_err": SE, "R_ci": [lo, hi], "T": 1/R}}.
    Residues with fewer points than parameters + 1 come back as None.
    """
    t = np.asarray(delays, float)
    if kind == "decay":
        model = Model("decay", lambda x, I0, R: I0 * np.exp(-R * x), ["I0", "R"], "I = I0*exp(-R*t)",
                      {"I0": "intensity at t = 0", "R": "rate (1/s)"},
                      guess=lambda x, y: [float(np.nanmax(y)), 1.0 / (float(np.ptp(x)) or 1.0)])
    elif kind == "inversion_recovery":
        model = Model("inversion_recovery", lambda x, I0, R: I0 * (1 - 2 * np.exp(-R * x)), ["I0", "R"],
                      "I = I0*(1 - 2*exp(-R*t))", {"I0": "equilibrium intensity", "R": "rate (1/s)"},
                      guess=lambda x, y: [float(np.nanmax(np.abs(y))), 1.0 / (float(np.ptp(x)) or 1.0)])
    else:
        raise ValueError("kind: 'decay' or 'inversion_recovery'")
    out: dict[Any, Optional[dict]] = {}
    for res in sorted(intensities, key=_res_key):
        y = np.asarray([np.nan if v is None else v for v in intensities[res]], float)
        ok = np.isfinite(y)
        if ok.sum() < 3:
            out[res] = None
            continue
        try:
            r = fit(model, t[ok], y[ok], level=level)
        except (RuntimeError, ValueError):
            out[res] = None
            continue
        R = r.params["R"]
        out[res] = {"R": R, "R_err": r.se.get("R"), "R_ci": list(r.ci["R"]), "T": 1 / R if R else None,
                    "I0": r.params["I0"]}
    return out


def hetnoe(saturated: dict, reference: dict, *, noise_sat: Optional[float] = None,
           noise_ref: Optional[float] = None) -> dict:
    """{residue: {"hetNOE": I_sat/I_ref, "err": propagated from the noise}}.
    Missing residues come back as None."""
    out = {}
    for res in sorted(set(saturated) | set(reference), key=_res_key):
        s, r = saturated.get(res), reference.get(res)
        if s is None or r is None or not r:
            out[res] = None
            continue
        v = s / r
        err = None
        if noise_sat is not None and noise_ref is not None:
            err = abs(v) * math.sqrt((noise_sat / s) ** 2 + (noise_ref / r) ** 2) if s else None
        out[res] = {"hetNOE": v, "err": err}
    return out


# ==============================================================================
# NOE buildup
# ==============================================================================

def _noe_buildup_f(t, sigma, T, A0):
    return (A0 / 2.0) * (1.0 - np.exp(-2.0 * sigma * t)) * np.exp(-t / T)


_NOE_MODEL = Model(
    "noe_buildup", _noe_buildup_f, ["sigma", "T"],
    "A(t) = (A0/2)*(1 - exp(-2*sigma*t))*exp(-t/T), A0 = diagonal integral at t = 0",
    {"sigma": "cross-relaxation rate (1/s)", "T": "apparent decay time of the buildup (s)"},
    constants=("A0",), bounds={"T": (1e-9, None)})
# T must be positive: a negative T (a growing exponential) can fit a
# buildup that is still linear at the last mixing time with a lower RSS,
# but means nothing; the bound lets T run long instead (no decay seen).
# Buildup fits have local minima (sigma small with T long, or the reverse):
# start from a spread grid and keep the best, as the reference fit does.
_NOE_STARTS = [(0.001, 0.4), (0.01, 0.4), (1, 1), (0.1, 0.4), (0.1, 7), (5, 0.5), (0.5, 5),
               (0.2, 0.9), (0.27, 0.42)]


def noe_buildup(mixing_s, cross: dict, diag0, *, include_origin: bool = True, level: float = 0.95) -> dict:
    """Cross-relaxation rates from NOESY buildup curves, one fit per pair.

    mixing_s        mixing times in SECONDS (convert ms first)
    cross           {pair: [cross-peak integral at each mixing time]}
    diag0           {pair: A_ii(0)} (the diagonal integral at the shortest
                    mixing time), or one number for all pairs
    include_origin  add the point A(0) = 0 (default, as in the usual analysis)
    Model: A(t) = (A0/2)*(1 - exp(-2*sigma*t))*exp(-t/T), fitted from a grid
    of starting points (the surface has local minima).
    Returns {pair: {"sigma", "sigma_ci", "T", "T_ci", "warnings"} or None},
    keyed exactly as `cross`: write it that way.
    """
    t = np.asarray(mixing_s, float)
    if t.size and float(np.max(t)) > 20:
        raise ValueError("mixing_s looks like milliseconds (max > 20): pass seconds")
    out: dict[Any, Optional[dict]] = {}
    for key, vals in cross.items():
        a0 = diag0[key] if isinstance(diag0, dict) else diag0
        y = np.asarray([np.nan if v is None else v for v in vals], float)
        if y.size != t.size:
            raise ValueError(f"{key!r}: {y.size} integrals for {t.size} mixing times")
        ok = np.isfinite(y)
        tt, yy = t[ok], y[ok]
        if include_origin:
            tt, yy = np.r_[0.0, tt], np.r_[0.0, yy]
        if a0 is None or not np.isfinite(a0) or yy.size < 3:
            out[key] = None
            continue
        best = None
        for s0, T0 in _NOE_STARTS:
            try:
                r = fit(_NOE_MODEL, tt, yy, p0=[s0, T0], constants={"A0": float(a0)}, n_starts=1, level=level)
            except (RuntimeError, ValueError, FloatingPointError):
                continue
            if best is None or r.rss < best.rss:
                best = r
        if best is None:
            out[key] = None
            continue
        warn = list(best.warnings)
        if best.params["T"] > 100 * float(np.max(tt)):
            warn.append(f"T = {best.params['T']:.3g} s is far beyond the longest mixing time: no decay is "
                        "resolved, so T is undetermined (sigma is still defined by the initial slope)")
        out[key] = {"sigma": best.params["sigma"], "sigma_ci": list(best.ci["sigma"]),
                    "T": best.params["T"], "T_ci": list(best.ci["T"]), "warnings": warn}
    return out
