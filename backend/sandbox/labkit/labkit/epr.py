"""labkit.epr: CW-EPR and DEER (4-pulse PELDOR) analysis (EPR-PLAN.md).

Units everywhere: field in mT, frequency in GHz, time in microseconds,
distance in nm, P(r) in 1/nm with integral 1. Loaders convert from the file's
unit and say which unit they read. Constants are CODATA 2022.

Conventions, written out because they are where analyses go wrong:
- g = h nu / (muB B) = 71.447735 * nu[GHz] / B[mT]. Use the measured
  microwave frequency (MWFQ in a BES3T file, in Hz), not a nominal 9.5 GHz.
- dBpp is the peak-to-peak width of the first-derivative line. FWHM of the
  absorption: Lorentzian sqrt(3) * dBpp, Gaussian sqrt(2 ln 2) * dBpp.
- Spin counting uses the DOUBLE integral of the derivative spectrum, with a
  baseline removed before and after the first integration, over a window of
  at least 10-20 linewidths. Normalisation: receiver gain 10^(dB/20),
  sqrt(power), modulation amplitude (linear, exact even when overmodulated),
  number of scans unless the data are already scan-normalised (SctNorm).
- DEER: phase the complex trace (never take |V|), find the zero time (it is
  not the first point), divide by the background (never subtract it), fit
  P(r), modulation depth, background and zero time together, with
  non-negative Tikhonov regularisation and the regularisation parameter
  chosen by AIC. A trace of length tmax supports the mean distance up to
  3.73 * tmax^(1/3) nm and the width/shape up to 2.96 * tmax^(1/3) nm
  (Schiemann et al., JACS 2021, 143, 17875).

Powder spectra (S = 1/2), unit conversion, isotopes and fitting a simulation
to a spectrum live in labkit/epr_sim.py and are re-exported here: SpinSystem,
Nucleus, simulate_powder, fit_spectrum, convert, isotope. Out of scope:
slow-motion spectra and S > 1/2; use EasySpin (chili, pepper) for those.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Sequence

import numpy as np
from scipy import optimize, special

from ._attrs import FieldHelp

H = 6.62607015e-34            # J s
MU_B = 9.2740100657e-24       # J/T
G_E = 2.00231930436092
MU_0 = 1.25663706127e-6       # N/A^2
N_A = 6.02214076e23
HBAR = H / (2 * math.pi)
MT_PER_GHZ = H * 1e9 / MU_B * 1e3        # 71.447735: B[mT] * g = MT_PER_GHZ * nu[GHz]
# Dipolar frequency constant for two g = g_e spins: nu_perp = NU_DD / r^3 (MHz, nm).
NU_DD = MU_0 / (4 * math.pi) * (G_E * MU_B) ** 2 / H / 1e-27 / 1e6        # 52.041 MHz nm^3
# Homogeneous 3D background: k [1/us] = K_HOM3D * lambda * c [uM].
K_HOM3D = (8 * math.pi ** 2 / (9 * math.sqrt(3)) * MU_0 / (4 * math.pi) * (G_E * MU_B) ** 2 / HBAR
           * N_A * 1e-3 / 1e6)                                             # 9.974e-4

_trapz = getattr(np, "trapezoid", None) or np.trapz


# ==============================================================================
# Reading
# ==============================================================================

@dataclass
class EPRData(FieldHelp):
    """One EPR dataset. `x` is the field in mT (kind "cw") or the time in us
    (kind "pulse"); `field_mT` / `t_us` name it explicitly. `y` is real or
    complex, shape (nx,), or (ny, nx) for 2D data: y[i] is the slice at
    y2[i] (a power step, a scan, a time point)."""
    kind: str
    x: np.ndarray
    y: np.ndarray
    x_unit_file: str
    y2: Optional[np.ndarray] = None
    y2_unit: str = ""
    y2_name: str = ""
    freq_GHz: Optional[float] = None
    power_mW: Optional[float] = None
    modamp_mT: Optional[float] = None
    gain_dB: Optional[float] = None
    n_scans: Optional[int] = None
    conv_time_ms: Optional[float] = None
    temperature_K: Optional[float] = None
    sctnorm: Optional[bool] = None
    source: str = ""
    params: dict = field(default_factory=dict)
    notes: list = field(default_factory=list)

    @property
    def field_mT(self) -> np.ndarray:
        if self.kind != "cw":
            raise AttributeError("field_mT: this is a pulse (time-domain) dataset; use t_us")
        return self.x

    @property
    def t_us(self) -> np.ndarray:
        if self.kind != "pulse":
            raise AttributeError("t_us: this is a CW (field-swept) dataset; use field_mT")
        return self.x

    @property
    def real(self) -> np.ndarray:
        return np.real(self.y)

    def __repr__(self):
        ax = "field" if self.kind == "cw" else "time"
        unit = "mT" if self.kind == "cw" else "us"
        shape = "x".join(str(s) for s in np.shape(self.y))
        bits = [f"{self.source}: {self.kind}, {shape} points{' complex' if np.iscomplexobj(self.y) else ''}",
                f"{ax} {self.x[0]:.4g} to {self.x[-1]:.4g} {unit} (file unit {self.x_unit_file!r})"]
        if self.y2 is not None:
            bits.append(f"second axis {self.y2_name or ''} {self.y2[0]:.4g} to {self.y2[-1]:.4g} {self.y2_unit}")
        for k in ("freq_GHz", "power_mW", "modamp_mT", "gain_dB", "n_scans", "conv_time_ms",
                  "temperature_K", "sctnorm"):
            v = getattr(self, k)
            if v is not None:
                bits.append(f"{k}={v:.6g}" if isinstance(v, float) else f"{k}={v}")
        if self.notes:
            bits.append("notes: " + "; ".join(self.notes))
        return "EPRData(" + ", ".join(bits) + ")"


_UNIT_TO_MT = {"G": 0.1, "MT": 1.0, "T": 1000.0}
_UNIT_TO_US = {"NS": 1e-3, "US": 1.0, "µS": 1.0, "MS": 1e3, "S": 1e6}


def _parse_dsc(text: str) -> dict:
    """KEY value lines of a BES3T descriptor, continuation lines joined,
    quotes removed; stops at the manipulation-history layer (#MHL)."""
    out: dict = {}
    lines = text.replace("\r\n", "\n").split("\n")
    i = 0
    while i < len(lines):
        line = lines[i]
        i += 1
        if line.startswith("#MHL"):
            break
        if not line or line[0] in "*#" or not line[0].isalpha():
            continue
        while line.endswith("\\") and i < len(lines):
            line = line[:-1] + "\n" + lines[i]
            i += 1
        parts = line.split(None, 1)
        key = parts[0]
        val = parts[1].strip() if len(parts) > 1 else ""
        if len(val) >= 2 and val[0] == val[-1] == "'":
            val = val[1:-1]
        out[key] = val
    return out


def _num_unit(v) -> tuple[Optional[float], str]:
    if v is None:
        return None, ""
    m = re.match(r"^\s*([-+]?\d*\.?\d+(?:[eE][-+]?\d+)?)\s*(\S*)", str(v))
    if not m:
        return None, ""
    return float(m.group(1)), m.group(2)


def _axis(d: dict, ax: str, n: int) -> np.ndarray:
    typ = d.get(f"{ax}TYP", "IDX")
    if typ == "IDX":
        xmin = float(d.get(f"{ax}MIN", 0))
        xwid = float(d.get(f"{ax}WID", 0))
        if xwid == 0:
            return np.arange(n, dtype=float)
        return xmin + np.linspace(0, xwid, n)          # endpoint included: step = XWID / (XPTS - 1)
    raise ValueError(f"{ax}TYP {typ}: nonlinear axes (IGD, companion .{ax}GF file) are handled by load()")


def _bes3t_paths(path) -> tuple[Path, Path]:
    p = Path(path)
    base = p.with_suffix("") if p.suffix.upper() in (".DSC", ".DTA") else p
    cands = [(base.with_suffix(s1), base.with_suffix(s2)) for s1, s2 in ((".DSC", ".DTA"), (".dsc", ".dta"))]
    for dsc, dta in cands:
        if dsc.exists() and dta.exists():
            return dsc, dta
    raise FileNotFoundError(f"{path}: a BES3T dataset needs both the .DSC descriptor and the .DTA data file")


def load_bes3t(path) -> EPRData:
    """Bruker BES3T (Xepr): .DSC descriptor + .DTA binary. Byte order BSEQ
    (BIG if absent), formats IRFMT D/F/I/S/C, complex (IKKF CPLX) interleaved
    re, im; X varies fastest."""
    dsc_p, dta_p = _bes3t_paths(path)
    d = _parse_dsc(dsc_p.read_text(encoding="latin-1"))
    notes = []
    bseq = d.get("BSEQ")
    if bseq is None:
        notes.append("BSEQ missing: assumed big-endian")
    order = "<" if (bseq or "BIG").upper() == "LIT" else ">"
    irfmt = d.get("IRFMT", "D").split(",")[0].strip().upper()
    if irfmt in ("A", "0", "N"):
        raise ValueError(f"IRFMT {irfmt}: no binary data in this dataset")
    dt = np.dtype(order + {"D": "f8", "F": "f4", "I": "i4", "S": "i2", "C": "i1"}[irfmt])
    ikkf = [s.strip().upper() for s in d.get("IKKF", "REAL").split(",")]
    nx, ny, nz = (int(d.get(k, 1)) for k in ("XPTS", "YPTS", "ZPTS"))
    raw = np.fromfile(dta_p, dtype=dt).astype(float)
    per_point = sum(2 if k == "CPLX" else 1 for k in ikkf)
    if raw.size != nx * ny * nz * per_point:
        raise ValueError(f"{dta_p.name}: {raw.size} values, but the descriptor says "
                         f"{nx}x{ny}x{nz} points x {per_point} values per point")
    raw = raw.reshape(nx * ny * nz, per_point)
    y = raw[:, 0] + 1j * raw[:, 1] if ikkf[0] == "CPLX" else raw[:, 0]
    if len(ikkf) > 1:
        notes.append(f"{len(ikkf)} channels in the file; only the first is returned")
    y = y.reshape(nz * ny, nx) if ny * nz > 1 else y
    if np.iscomplexobj(y) and ny * nz == 1:
        y = np.asarray(y)
    xu = d.get("XUNI", "").strip("'").strip()
    if d.get("XTYP", "IDX") == "IGD":
        xgf = dsc_p.with_suffix(".XGF")
        xfmt = d.get("XFMT", "D").upper()
        x = np.fromfile(xgf, dtype=np.dtype(order + {"D": "f8", "F": "f4", "I": "i4", "S": "i2"}[xfmt])).astype(float)
    else:
        x = _axis(d, "X", nx)
    kind, x_int = _convert_axis(x, xu, notes)
    y2 = None
    if ny > 1:
        try:
            y2 = _axis(d, "Y", ny)
        except ValueError:
            y2 = np.arange(ny, dtype=float)
            notes.append("nonlinear Y axis: returned as indices")
    freq = _num_unit(d.get("MWFQ"))[0]
    pw = _num_unit(d.get("MWPW"))[0]
    ma, ma_u = _num_unit(d.get("ModAmp"))
    data = EPRData(kind=kind, x=x_int, y=y, x_unit_file=xu, y2=y2, y2_unit=d.get("YUNI", ""),
                   y2_name=d.get("YNAM", ""), source=dsc_p.with_suffix("").name, params=d, notes=notes,
                   freq_GHz=freq / 1e9 if freq else None,
                   power_mW=pw * 1e3 if pw is not None else None,
                   modamp_mT=(ma * _UNIT_TO_MT.get(ma_u.upper(), 0.1)) if ma is not None else None,
                   gain_dB=_num_unit(d.get("RCAG"))[0],
                   n_scans=int(_num_unit(d.get("AVGS"))[0]) if d.get("AVGS") else None,
                   conv_time_ms=(_num_unit(d.get("SPTP"))[0] * 1e3) if d.get("SPTP") else None,
                   temperature_K=_num_unit(d.get("STMP"))[0],
                   sctnorm=(d.get("SctNorm", "").lower() == "true") if "SctNorm" in d else None)
    return data


def _convert_axis(x, unit: str, notes: list) -> tuple[str, np.ndarray]:
    u = unit.strip().upper()
    if u in _UNIT_TO_MT:
        return "cw", x * _UNIT_TO_MT[u]
    if u in _UNIT_TO_US:
        return "pulse", x * _UNIT_TO_US[u]
    if not u:
        notes.append("no axis unit in the file: guessed from the values")
        if np.nanmax(np.abs(x)) > 1500:
            return "cw", x * 0.1          # X-band in G
    raise ValueError(f"axis unit {unit!r} not recognised; expected G, mT, T, ns, us")


def load_esp(path) -> EPRData:
    """Bruker ESP/ECS/EMX (.par + .spc). WinEPR files (DOS key in .par) are
    float32 little-endian; ESP files int32 big-endian. Field in G."""
    p = Path(path)
    base = p.with_suffix("")
    par_p = next((base.with_suffix(s) for s in (".par", ".PAR") if base.with_suffix(s).exists()), None)
    spc_p = next((base.with_suffix(s) for s in (".spc", ".SPC") if base.with_suffix(s).exists()), None)
    if par_p is None or spc_p is None:
        raise FileNotFoundError(f"{path}: an ESP dataset needs both .par and .spc")
    par = {}
    for line in par_p.read_text(encoding="latin-1").splitlines():
        parts = line.strip().split(None, 1)
        if parts:
            par[parts[0]] = parts[1].strip() if len(parts) > 1 else ""
    winepr = "DOS" in par
    dt = np.dtype("<f4") if winepr else np.dtype(">i4")
    y = np.fromfile(spc_p, dtype=dt).astype(float)
    jss = int(float(par.get("JSS", 0)))
    notes = [f"{'WinEPR float32 little-endian' if winepr else 'ESP int32 big-endian'}"]
    if jss & (1 << 4):
        y = y[0::2] + 1j * y[1::2]
    n = y.size
    if "GST" in par and "GSI" in par:
        x = float(par["GST"]) + float(par["GSI"]) * np.linspace(0, 1, n)
    elif "HCF" in par and "HSW" in par:
        x = float(par["HCF"]) + float(par["HSW"]) / 2 * np.linspace(-1, 1, n)
    elif "XXLB" in par and "XXWI" in par:
        x = float(par["XXLB"]) + float(par["XXWI"]) * np.linspace(0, 1, n)
    else:
        raise ValueError(f"{par_p.name}: no field axis (HCF/HSW or GST/GSI)")
    unit = par.get("JUN", "G").strip() or "G"
    kind, xm = _convert_axis(x, unit, notes)
    return EPRData(kind=kind, x=xm, y=y, x_unit_file=unit, source=base.name, params=par, notes=notes,
                   freq_GHz=float(par["MF"]) if "MF" in par else None,
                   power_mW=float(par["MP"]) if "MP" in par else None,
                   modamp_mT=float(par["RMA"]) * 0.1 if "RMA" in par else None,
                   gain_dB=20 * math.log10(float(par["RRG"])) if "RRG" in par and float(par["RRG"]) > 0 else None,
                   n_scans=int(float(par["JSD"])) if "JSD" in par else None,
                   conv_time_ms=float(par["RCT"]) if "RCT" in par else None,
                   temperature_K=float(par["TE"]) if "TE" in par else None)


def load_ascii(path, *, x_unit: Optional[str] = None) -> EPRData:
    """A text export: columns of field (or time) and intensity, optionally an
    index column first and an imaginary column last. The axis unit comes from
    `x_unit`, else the header (Field [G], time_ns, t/us ...), else it raises."""
    p = Path(path)
    text = p.read_text(encoding="utf-8", errors="replace")
    lines = [l for l in text.splitlines() if l.strip()]
    header = []
    rows = []
    for l in lines:
        s = l.strip()
        cells = re.split(r"[\t,; ]+", s.lstrip("#%").strip())
        try:
            vals = [float(c.replace(",", ".")) if re.fullmatch(r"[-+]?\d+,\d+([eE][-+]?\d+)?", c) else float(c)
                    for c in cells]
            rows.append(vals)
        except ValueError:
            if not rows:
                header.append(s)
    if not rows:
        raise ValueError(f"{p.name}: no numeric rows")
    a = np.array([r for r in rows if len(r) == len(rows[0])], dtype=float)
    head = " ".join(header)
    names = re.split(r"[\t,;]+|\s{2,}", header[-1].lstrip("#%").strip()) if header else []
    col_x = 0
    if a.shape[1] >= 3 and np.allclose(a[:, 0], np.arange(1, len(a) + 1)) or (
            names and re.match(r"(?i)index|point|#", names[0] or "")):
        col_x = 1
    x = a[:, col_x]
    rest = a[:, col_x + 1:]
    y = rest[:, 0] + 1j * rest[:, 1] if rest.shape[1] >= 2 else rest[:, 0]
    unit = x_unit
    if unit is None:
        hx = (names[col_x] if len(names) > col_x else head)
        m = re.search(r"(?i)(?:\[|\(|_|/|\s|^)(mT|G|ns|us|µs|ms|T)(?:\]|\)|\b|$)", hx)
        if m:
            unit = m.group(1)
    if unit is None:
        raise ValueError(f"{p.name}: the axis unit is not in the header; pass x_unit='G', 'mT', 'ns' or 'us'")
    notes = [f"axis column {col_x + 1}, unit {unit}"]
    kind, xm = _convert_axis(x, unit, notes)
    if xm[0] > xm[-1]:
        xm, y = xm[::-1], y[::-1]
        notes.append("descending axis reversed")
    freq = _header_freq_GHz(header)
    if freq is not None:
        notes.append(f"frequency {freq:g} GHz read from the header")
    return EPRData(kind=kind, x=xm, y=y, x_unit_file=unit, source=p.stem, notes=notes,
                   params={"header": header}, freq_GHz=freq)


_FREQ_GHZ = re.compile(r"(?i)(?:\b(?:nu|freq\w*|mw\s*freq\w*|mwfq|microwave\s+frequency)\s*[=:]?\s*)?"
                       r"(\d+(?:\.\d+)?)\s*GHz\b")
_FREQ_HZ = re.compile(r"(?i)\b(?:MWFQ|mwFreq)\s*[=:]?\s*([\d.]+[eE][+]?\d+)")


def _header_freq_GHz(header: list) -> Optional[float]:
    """The microwave frequency stated in a text export's header ('nu = 9.4318 GHz',
    'MWFQ 9.4318e9'), or None. Several different values: None (ambiguous)."""
    vals = set()
    for line in header:
        for m in _FREQ_GHZ.finditer(line):
            vals.add(round(float(m.group(1)), 6))
        for m in _FREQ_HZ.finditer(line):
            v = float(m.group(1))
            vals.add(round(v / 1e9 if v > 1e6 else v, 6))
    vals = {v for v in vals if 0.5 <= v <= 400}
    return vals.pop() if len(vals) == 1 else None


def load(path, **kw) -> EPRData:
    """Read a Bruker BES3T (.DSC/.DTA), ESP/WinEPR (.par/.spc) or ASCII export."""
    p = Path(path)
    suf = p.suffix.lower()
    if suf in (".dsc", ".dta") or (not suf and (p.with_suffix(".DSC").exists() or p.with_suffix(".dsc").exists())):
        return load_bes3t(p)
    if suf in (".par", ".spc"):
        return load_esp(p)
    return load_ascii(p, **kw)


# ==============================================================================
# CW: field, g, lines
# ==============================================================================

def g_value(B_mT, freq_GHz) -> float:
    """g = h nu / (muB B) = 71.447735 * nu[GHz] / B[mT]."""
    return MT_PER_GHZ * float(freq_GHz) / float(B_mT)


def field_for_g(g, freq_GHz) -> float:
    """Resonance field in mT for a g-value at a frequency."""
    return MT_PER_GHZ * float(freq_GHz) / float(g)


def calibrate_field(field_mT, ref_B0_mT, ref_g, freq_GHz):
    """Field offset from a standard of known g measured on the same axis:
    offset = measured - true (positive: the axis reads high). Returns
    (corrected field, offset_mT). DPPH g = 2.0036, BDPA 2.0025."""
    offset = float(ref_B0_mT) - field_for_g(ref_g, freq_GHz)
    return np.asarray(field_mT, float) - offset, offset


def frequency_correct(field_mT, freq_GHz, target_GHz):
    """Field axis rescaled to another frequency (g preserved): B * target/nu.
    Scales; a constant shift is wrong over a wide sweep."""
    return np.asarray(field_mT, float) * float(target_GHz) / float(freq_GHz)


def fwhm_from_pp(dBpp, shape: str = "lorentzian") -> float:
    """Absorption FWHM from the derivative peak-to-peak width:
    Lorentzian sqrt(3) * dBpp, Gaussian sqrt(2 ln 2) * dBpp."""
    s = shape.lower()
    if s.startswith("lor"):
        return math.sqrt(3) * dBpp
    if s.startswith("gau"):
        return math.sqrt(2 * math.log(2)) * dBpp
    raise ValueError("shape: 'lorentzian' or 'gaussian' (fit a Voigt with fit_line for mixed shapes)")


def pp_from_fwhm(fwhm, shape: str = "lorentzian") -> float:
    return fwhm / fwhm_from_pp(1.0, shape)


def _window(x, y, window):
    x = np.asarray(x, float)
    y = np.real(np.asarray(y))
    if window is not None:
        m = (x >= window[0]) & (x <= window[1])
        x, y = x[m], y[m]
    return x, y


def _parabola_peak(x, y, i):
    if 0 < i < len(y) - 1:
        a, b, c = y[i - 1], y[i], y[i + 1]
        den = a - 2 * b + c
        if den != 0:
            d = 0.5 * (a - c) / den
            return x[i] + d * (x[i + 1] - x[i - 1]) / 2, b - 0.25 * (a - c) * d
    return x[i], y[i]


def despike(field_mT, y, max_width: int = 3, *, k: float = 6.0):
    """Remove spikes (cosmic rays, glitches) from a CW spectrum: runs of at
    most max_width points that stand out from a running median by more than
    k robust standard deviations of the noise, where the points around them
    are smooth; each is replaced by a cubic through its neighbours. Returns
    (y_clean, changed_indices).

    Never mask by amplitude (abs(y) > K): in a derivative spectrum the
    largest points ARE the line. A real line is wider than a spike, so its
    extrema are left alone: a run wider than max_width, a run whose
    neighbours do not lie on a smooth curve (the flank of a line), or one next
    to an opposite-sign excursion of similar size (the other lobe of a narrow
    derivative line) is kept. A line needs about 3 * max_width points across
    its peak-to-peak width to be told from a spike. Works on the real part of
    complex data; field_mT only has to be monotonic."""
    x = np.asarray(field_mT, float)
    yy = np.asarray(y)
    yr = (yy.real if np.iscomplexobj(yy) else yy).astype(float)
    n = yr.size
    if x.shape != yr.shape or yr.ndim != 1:
        raise ValueError("despike: field and y must be 1D and the same length (2D: despike each row)")
    w = int(max_width)
    if w < 1:
        raise ValueError("max_width must be at least 1")
    h = w + 1                      # a run of w points is less than half of the 2h+1 median window
    m = 2 * h                      # flank points on each side for the cubic
    if n < 2 * (m + h) + w:
        return yr.copy(), np.array([], int)
    pad = np.pad(yr, h, mode="reflect")
    med = np.median(np.lib.stride_tricks.sliding_window_view(pad, 2 * h + 1), axis=1)
    res = yr - med
    # noise from second differences (a smooth line barely contributes)
    d2 = np.diff(yr, 2)
    sig = 1.4826 * float(np.median(np.abs(d2 - np.median(d2)))) / math.sqrt(6)
    sig = max(sig, 1e-3 * float(np.ptp(yr)), 1e-300)
    out = np.abs(res) > k * sig
    clean = yr.copy()
    changed = []
    i = 0
    while i < n:
        if not out[i]:
            i += 1
            continue
        j = i
        while j + 1 < n and out[j + 1] and np.sign(res[j + 1]) == np.sign(res[i]):
            j += 1
        i_next = j + 1
        if j - i + 1 > w or i < m + 1 or j > n - m - 2 or out[i - 1] or out[j + 1]:
            i = i_next
            continue
        sgn = np.sign(res[i])
        peak = float(np.max(np.abs(res[i:j + 1])))
        near = np.r_[res[max(0, i - 2 * m):i], res[j + 1:j + 1 + 2 * m]]
        if np.any((np.sign(near) == -sgn) & (np.abs(near) > max(k * sig, 0.3 * peak))):
            i = i_next
            continue
        fl = np.r_[i - 1 - m:i - 1, j + 2:j + 2 + m]      # one point of margin on each side
        xc = x[fl] - x[i]
        c = np.polyfit(xc, yr[fl], 3)
        flank_rms = float(np.sqrt(np.mean((np.polyval(c, xc) - yr[fl]) ** 2)))
        pred = np.polyval(c, x[i:j + 1] - x[i])
        if flank_rms < 2 * sig and np.all(np.abs(yr[i:j + 1] - pred) > k * sig):
            clean[i:j + 1] = pred
            changed.extend(range(i, j + 1))
        i = i_next
    return clean, np.array(changed, int)


def peak_to_peak(field_mT, y, window=None) -> dict:
    """Derivative peak-to-peak: dBpp_mT, Ypp, B_max_mT, B_min_mT (parabolic
    refinement around the extremes). For a noisy line prefer fit_line."""
    x, yy = _window(field_mT, y, window)
    i, j = int(np.argmax(yy)), int(np.argmin(yy))
    bi, yi = _parabola_peak(x, yy, i)
    bj, yj = _parabola_peak(x, -yy, j)
    return {"dBpp_mT": abs(bj - bi), "Ypp": yi + yj, "B_max_mT": bi, "B_min_mT": bj}


def zero_crossing(field_mT, y, window=None) -> float:
    """Field where the derivative crosses zero between its maximum and
    minimum (the centre of a symmetric line). For anisotropic powder
    patterns this is NOT the g-value position."""
    x, yy = _window(field_mT, y, window)
    i, j = int(np.argmax(yy)), int(np.argmin(yy))
    lo, hi = sorted((i, j))
    seg = yy[lo:hi + 1]
    s = np.sign(seg)
    k = np.where(s[:-1] * s[1:] <= 0)[0]
    if k.size == 0:
        raise ValueError("no zero crossing between the derivative's maximum and minimum")
    k = int(k[len(k) // 2]) + lo
    return float(x[k] - yy[k] * (x[k + 1] - x[k]) / (yy[k + 1] - yy[k]))


def _lor_d(b, b0, dbpp, area):
    g = math.sqrt(3) * dbpp / 2
    return -area / math.pi * 2 * g * (b - b0) / ((b - b0) ** 2 + g ** 2) ** 2


def _gau_d(b, b0, dbpp, area):
    s = dbpp / 2
    return -area * (b - b0) / (s ** 3 * math.sqrt(2 * math.pi)) * np.exp(-0.5 * ((b - b0) / s) ** 2)


def _voigt_abs(b, b0, sig, gam, area):
    return area * special.voigt_profile(b - b0, sig, gam)


@dataclass
class LineFit(FieldHelp):
    shape: str
    B0_mT: float
    dBpp_mT: float
    fwhm_mT: float
    area: float                       # absorption area, intensity x mT
    g: Optional[float]
    se: dict
    residual_rms: float
    warnings: list

    def __repr__(self):
        g = f", g = {self.g:.6f}" if self.g else ""
        return (f"LineFit({self.shape}: B0 = {self.B0_mT:.5f} mT{g}, dBpp = {self.dBpp_mT:.5f} mT "
                f"(derivative peak-to-peak), FWHM = {self.fwhm_mT:.5f} mT (absorption), area = "
                f"{self.area:.6g} (absorption integral, intensity*mT); warnings: {self.warnings})")


def fit_line(field_mT, y, *, shape: str = "lorentzian", freq_GHz=None, window=None,
             baseline: int = 1) -> LineFit:
    """Least-squares fit of ONE first-derivative line (Lorentzian, Gaussian or
    Voigt) plus a polynomial baseline of order `baseline`. Gives B0, dBpp,
    the absorption FWHM, the area and g (if freq_GHz)."""
    x, yy = _window(field_mT, y, window)
    pp = peak_to_peak(x, yy)
    b0 = 0.5 * (pp["B_max_mT"] + pp["B_min_mT"])
    w = max(pp["dBpp_mT"], 2 * (x[1] - x[0]))
    s = shape.lower()[:3]
    xc = (x - x.mean()) / (np.ptp(x) or 1)

    def model(p):
        if s == "lor":
            line = _lor_d(x, p[0], abs(p[1]), p[2])
            k = 3
        elif s == "gau":
            line = _gau_d(x, p[0], abs(p[1]), p[2])
            k = 3
        else:
            h = 1e-4 * (x[1] - x[0])
            line = (_voigt_abs(x + h, p[0], abs(p[1]), abs(p[3]), p[2])
                    - _voigt_abs(x - h, p[0], abs(p[1]), abs(p[3]), p[2])) / (2 * h)
            k = 4
        return line + np.polyval(p[k:], xc) if baseline >= 0 else line

    a0 = pp["Ypp"] * w ** 2 * (math.pi / math.sqrt(3) / 2 if s == "lor" else 0.5)
    if s == "voi":
        p0 = [b0, w / 2.5, a0, w / 2.5]
    else:
        p0 = [b0, w, a0]
    p0 = p0 + [0.0] * (baseline + 1)
    res = optimize.least_squares(lambda p: model(p) - yy, p0, x_scale="jac")
    p = res.x
    warns = []
    if s == "voi":
        sig, gam = abs(p[1]), abs(p[3])
        fl, fg = 2 * gam, 2 * math.sqrt(2 * math.log(2)) * sig
        fwhm = 0.5346 * fl + math.sqrt(0.2166 * fl ** 2 + fg ** 2)
        xx = np.linspace(-5 * fwhm, 5 * fwhm, 20001)
        d = np.gradient(special.voigt_profile(xx, sig, gam), xx)
        dbpp = abs(xx[np.argmin(d)] - xx[np.argmax(d)])
    else:
        dbpp = abs(p[1])
        fwhm = fwhm_from_pp(dbpp, "lorentzian" if s == "lor" else "gaussian")
    try:
        J = res.jac
        cov = np.linalg.inv(J.T @ J) * (2 * res.cost / max(1, len(yy) - len(p)))
        se = {"B0_mT": float(np.sqrt(cov[0, 0])), "width": float(np.sqrt(cov[1, 1]))}
    except np.linalg.LinAlgError:
        se = {}
        warns.append("singular fit: no standard errors")
    span = x[-1] - x[0]
    if span < 10 * dbpp:
        warns.append(f"window {span:.3g} mT is under 10 linewidths: the area is truncated")
    g = g_value(p[0], freq_GHz) if freq_GHz else None
    rms = float(np.sqrt(np.mean((model(p) - yy) ** 2)))
    return LineFit(shape={"lor": "lorentzian", "gau": "gaussian", "voi": "voigt"}[s], B0_mT=float(p[0]),
                   dBpp_mT=float(dbpp), fwhm_mT=float(fwhm), area=float(p[2]), g=g, se=se,
                   residual_rms=rms, warnings=warns)


# ==============================================================================
# CW: integration and spin counting
# ==============================================================================

def _wing_mask(x, wings):
    n = len(x)
    if isinstance(wings, (int, float)):
        k = max(3, int(round(float(wings) * n)))
        m = np.zeros(n, bool)
        m[:k] = True
        m[-k:] = True
        return m
    m = np.zeros(n, bool)
    for lo, hi in wings:
        m |= (x >= lo) & (x <= hi)
    if m.sum() < 4:
        raise ValueError("wings select fewer than 4 points")
    return m


def baseline_correct(field_mT, y, *, wings=0.1, order: int = 1):
    """Subtract a polynomial of `order` fitted to the signal-free wings: a
    fraction of the points at each end (default 10%) or [(lo, hi), ...] in mT."""
    x = np.asarray(field_mT, float)
    yy = np.real(np.asarray(y, float))
    m = _wing_mask(x, wings)
    xc = (x - x.mean()) / (np.ptp(x) or 1)
    return yy - np.polyval(np.polyfit(xc[m], yy[m], order), xc)


@dataclass
class DoubleIntegral(FieldHelp):
    value: float                      # intensity * mT^2
    absorption: np.ndarray
    field_mT: np.ndarray
    window_mT: tuple
    warnings: list

    def __float__(self):
        return float(self.value)

    def __repr__(self):
        return (f"DoubleIntegral({self.value:.6g} (intensity*mT^2, field in mT; x100 if you work in G) "
                f"over {self.window_mT[0]:.4g}-{self.window_mT[1]:.4g} mT; warnings: {self.warnings})")


def double_integral(field_mT, y, *, wings=0.1, order_deriv: int = 1, order_abs: int = 1,
                    linewidth_mT: Optional[float] = None) -> DoubleIntegral:
    """Double integral of a first-derivative spectrum, the quantity spin
    counting compares: baseline (order_deriv) removed from the derivative on
    the wings, cumulative integration to the absorption, baseline (order_abs)
    removed from the absorption on the wings, integration again. Field in mT,
    so the value is in intensity*mT^2. Warns when the window is under 10
    linewidths (Lorentzian wings are long) or the absorption does not return
    to zero."""
    x = np.asarray(field_mT, float)
    if x[0] > x[-1]:
        x, y = x[::-1], np.asarray(y)[::-1]
    d = baseline_correct(x, y, wings=wings, order=order_deriv)
    a = np.concatenate([[0.0], np.cumsum(0.5 * (d[1:] + d[:-1]) * np.diff(x))])
    m = _wing_mask(x, wings)
    xc = (x - x.mean()) / (np.ptp(x) or 1)
    a = a - np.polyval(np.polyfit(xc[m], a[m], order_abs), xc)
    val = float(_trapz(a, x))
    warns = []
    if linewidth_mT is None:
        try:
            linewidth_mT = peak_to_peak(x, d)["dBpp_mT"]
        except Exception:
            linewidth_mT = None
    if linewidth_mT and (x[-1] - x[0]) < 20 * linewidth_mT:
        warns.append(f"window {x[-1] - x[0]:.3g} mT is under 20 linewidths ({linewidth_mT:.3g} mT): "
                     f"a Lorentzian line loses a noticeable part of its area")
    peak = np.max(np.abs(a))
    if peak > 0 and max(abs(a[m][:3].mean()), abs(a[m][-3:].mean())) > 0.05 * peak:
        warns.append("the absorption does not return to zero at the ends: check the baseline or widen the window")
    return DoubleIntegral(value=val, absorption=a, field_mT=x, window_mT=(float(x[0]), float(x[-1])),
                          warnings=warns)


def normalize_intensity(value, *, gain_dB=None, power_mW=None, modamp_mT=None, n_scans=None,
                        sctnorm: Optional[bool] = False) -> float:
    """value / (10^(gain_dB/20) * sqrt(power_mW) * modamp_mT * n_scans).
    Receiver gain in dB is an amplitude ratio (20 log10); signal grows with
    sqrt(power) below saturation and linearly with the modulation amplitude
    (exactly, for the double integral, even when overmodulated). Scans divide
    only if the data are summed (SctNorm false); Xepr's SctNorm true data are
    already averaged. Any factor given as None is left out. (Bruker's Xenon
    manual writes the gain as 20 * 10^(dB/20); the constant 20 cancels in a
    sample/standard ratio.)"""
    f = 1.0
    if gain_dB is not None:
        f *= 10 ** (float(gain_dB) / 20)
    if power_mW is not None:
        f *= math.sqrt(float(power_mW))
    if modamp_mT is not None:
        f *= float(modamp_mT)
    if n_scans is not None and not sctnorm:
        f *= float(n_scans)
    return float(value) / f


@dataclass
class SpinCount(FieldHelp):
    value: float
    unit: str
    ratio: float
    factors: dict
    di_sample: float
    di_standard: float
    warnings: list

    def __repr__(self):
        lines = [f"SpinCount: {self.value:.6g} {self.unit} (sample/standard normalised ratio {self.ratio:.6g})",
                 f"  double integrals (intensity*mT^2): sample {self.di_sample:.6g}, standard {self.di_standard:.6g}"]
        lines += [f"  {k}: {v}" for k, v in self.factors.items()]
        if self.warnings:
            lines.append("  warnings: " + "; ".join(self.warnings))
        return "\n".join(lines)


def spin_count(sample: EPRData, standard: EPRData, *, standard_amount: float, unit: str = "uM",
               S_sample: float = 0.5, S_standard: float = 0.5, g_sample=None, g_standard=None,
               wings=0.1, order_deriv: int = 1, order_abs: int = 1) -> SpinCount:
    """Spins in `sample` relative to a standard measured in the same tube
    type, volume and temperature: ratio of double integrals, each normalised
    by gain (10^(dB/20)), sqrt(power), modulation amplitude and scans (from
    the files), times S(S+1) of standard over sample and g of standard over
    sample (when given). Returns the amount in the standard's unit."""
    warns = []
    dis = []
    norm = {}
    for name, d in (("sample", sample), ("standard", standard)):
        di = double_integral(d.field_mT, np.real(d.y), wings=wings, order_deriv=order_deriv, order_abs=order_abs)
        warns += [f"{name}: {w}" for w in di.warnings]
        missing = [k for k in ("gain_dB", "power_mW", "modamp_mT", "n_scans") if getattr(d, k) is None]
        if missing:
            warns.append(f"{name}: {', '.join(missing)} not in the file, left out of the normalisation")
        norm[name] = normalize_intensity(di.value, gain_dB=d.gain_dB, power_mW=d.power_mW,
                                         modamp_mT=d.modamp_mT, n_scans=d.n_scans, sctnorm=d.sctnorm)
        dis.append(di.value)
        if d.temperature_K and name == "sample" and standard.temperature_K and \
                abs(d.temperature_K - standard.temperature_K) > 1:
            warns.append("sample and standard at different temperatures: Curie law (1/T) not applied")
    ratio = norm["sample"] / norm["standard"]
    spin_f = (S_standard * (S_standard + 1)) / (S_sample * (S_sample + 1))
    g_f = (g_standard / g_sample) if (g_sample and g_standard) else 1.0
    value = standard_amount * ratio * spin_f * g_f
    factors = {
        "gain_dB (sample, standard)": (sample.gain_dB, standard.gain_dB),
        "power_mW": (sample.power_mW, standard.power_mW),
        "modamp_mT": (sample.modamp_mT, standard.modamp_mT),
        "n_scans": (sample.n_scans, standard.n_scans),
        "S(S+1) standard/sample": spin_f,
        "g standard/sample": g_f,
    }
    return SpinCount(value=float(value), unit=unit, ratio=float(ratio), factors=factors,
                     di_sample=dis[0], di_standard=dis[1], warnings=warns)


# ==============================================================================
# CW: nitroxides, saturation
# ==============================================================================

@dataclass
class Nitroxide(FieldHelp):
    aN_mT: float
    aN_MHz: float
    g_iso: Optional[float]
    positions_mT: dict              # mI -> line centre
    dBpp_mT: dict                   # mI -> peak-to-peak width
    heights: dict                   # mI -> derivative peak-to-peak height
    warnings: list

    def __repr__(self):
        g = f"{self.g_iso:.6f}" if self.g_iso else "n/a (no frequency)"
        return (f"Nitroxide(aN = {self.aN_mT:.5f} mT = {self.aN_MHz:.4f} MHz (half the outer-line "
                f"spacing), g_iso = {g} (outer-line midpoint); lines mI=+1 (low field), 0, -1 (high field): "
                f"positions {self._fmt(self.positions_mT)} mT, dBpp {self._fmt(self.dBpp_mT)} mT, "
                f"heights {self._fmt(self.heights)}; warnings: {self.warnings})")

    @staticmethod
    def _fmt(d):
        return "{" + ", ".join(f"{k:+d}: {v:.5g}" for k, v in d.items()) + "}"


def nitroxide_hyperfine(field_mT, y, *, freq_GHz=None) -> Nitroxide:
    """Fast-motion 14N nitroxide triplet: fits three Lorentzian derivative
    lines (mI = +1, 0, -1 from low to high field). aN is half the spacing of
    the OUTER lines (second-order shifts move the centre line by about
    aN^2/B0); g_iso from the outer-line midpoint; aN in MHz = aN[mT] * g muB/h.
    Heights are derivative peak-to-peak heights (for kivelson_tau)."""
    x, yy = _window(field_mT, y, None)
    from scipy.signal import find_peaks
    prom = 0.1 * (np.max(yy) - np.min(yy))
    pk, _ = find_peaks(yy, prominence=prom)
    if len(pk) < 3:
        raise ValueError("found fewer than three lines; is this a fast-motion nitroxide spectrum?")
    pk = np.sort(pk[np.argsort(yy[pk])[-3:]])
    tr, _ = find_peaks(-yy, prominence=prom)
    p0 = []
    for i in pk:
        after = tr[tr > i]
        j = after[0] if after.size else min(len(x) - 1, i + 5)
        w = max(abs(x[j] - x[i]), 2 * (x[1] - x[0]))
        p0 += [0.5 * (x[i] + x[j]), w, (yy[i] - yy[j]) * w ** 2 * 0.9]

    def model(p):
        return sum(_lor_d(x, p[3 * k], abs(p[3 * k + 1]), p[3 * k + 2]) for k in range(3)) + p[9]
    res = optimize.least_squares(lambda p: model(p) - yy, p0 + [0.0], x_scale="jac")
    p = res.x
    pos = {+1: p[0], 0: p[3], -1: p[6]}
    wid = {+1: abs(p[1]), 0: abs(p[4]), -1: abs(p[7])}
    hts = {}
    # exact pp height of a Lorentzian derivative of area A, dBpp w: 3 sqrt(3) A / (2 pi gamma^2) with gamma = sqrt(3) w / 2
    for k, mi in enumerate((+1, 0, -1)):
        gam = math.sqrt(3) * wid[mi] / 2
        hts[mi] = 3 * math.sqrt(3) * abs(p[3 * k + 2]) / (4 * math.pi * gam ** 2)
    a = (pos[-1] - pos[+1]) / 2
    g = g_value(0.5 * (pos[-1] + pos[+1]), freq_GHz) if freq_GHz else None
    a_mhz = a * 1e-3 * (g or G_E) * MU_B / H / 1e6
    warns = []
    if not (1.2 < a < 2.0):
        warns.append(f"aN = {a:.3g} mT is outside the usual 1.3-1.8 mT of nitroxides")
    return Nitroxide(aN_mT=float(a), aN_MHz=float(a_mhz), g_iso=g, positions_mT={k: float(v) for k, v in pos.items()},
                     dBpp_mT={k: float(v) for k, v in wid.items()}, heights={k: float(v) for k, v in hts.items()},
                     warnings=warns)


def kivelson_tau(dB0, h0, h_plus1, h_minus1, *, unit: str = "mT", constant_s_per_G: float = 6.51e-10) -> dict:
    """Rotational correlation time from line heights (Kivelson):
    tau_c = C * dB0[G] * (sqrt(h0/h+1) + sqrt(h0/h-1) - 2), C = 6.51e-10 s/G
    (X band, typical nitroxide tensors; the constant depends on them).
    h+1 is the LOW-field line, h-1 the HIGH-field line; dB0 the centre-line
    peak-to-peak width (converted to G from `unit`). Valid for fast isotropic
    motion, roughly 5e-11 to 1e-9 s."""
    dB0_G = float(dB0) * (10.0 if unit == "mT" else 1.0)
    tau = constant_s_per_G * dB0_G * (math.sqrt(h0 / h_plus1) + math.sqrt(h0 / h_minus1) - 2)
    warns = []
    if not (5e-11 <= tau <= 1e-9):
        warns.append(f"tau_c = {tau:.3g} s is outside 5e-11-1e-9 s, where the formula holds "
                     f"(slow motion needs a simulation, e.g. EasySpin chili)")
    return {"tau_c_s": tau, "dB0_G": dB0_G, "constant_s_per_G": constant_s_per_G, "warnings": warns}


def attenuation_to_mW(dB, p_max_mW: float = 200.0):
    """Microwave power from attenuation: P = p_max * 10^(-dB/10) (power dB,
    10 log10; 200 mW is a typical X-band bridge maximum, check the file)."""
    return p_max_mW * 10 ** (-np.asarray(dB, float) / 10)


def _sat(P, I, P_half, epsilon):
    return I * np.sqrt(P) * (1 + (2 ** (1 / epsilon) - 1) * P / P_half) ** (-epsilon)


def power_saturation_fit(power_mW, amplitude, *, fit_epsilon: bool = True):
    """Fit A(P) = I sqrt(P) [1 + (2^(1/eps) - 1) P/P_half]^(-eps) (Altenbach et
    al. 2005). P_half is the power at which A is half its unsaturated value;
    eps = 1.5 homogeneous, 0.5 inhomogeneous. NOT the older b-form P' (P' and
    P_half differ: P_half = P' (2^(2/b) - 1)). Returns a labkit FitResult."""
    from .fit import fit as _fit
    P = np.asarray(power_mW, float)
    A = np.asarray(amplitude, float)
    order = np.argsort(P)
    lin = np.polyfit(np.sqrt(P[order][:3]), A[order][:3], 1)[0] if len(P) >= 3 else A[0] / math.sqrt(P[0])
    p0 = [max(lin, 1e-12), float(np.median(P)), 1.5]
    fixed = None if fit_epsilon else {"epsilon": 1.5}
    return _fit(_sat, P, A, p0=p0, fixed=fixed,
                bounds={"I": (0, None), "P_half": (1e-6, None), "epsilon": (0.2, 5.0)})


# ==============================================================================
# CW: isotropic simulation and fit
# ==============================================================================

def _lines(freq_GHz, g, nuclei):
    """Stick spectrum (positions in mT, relative intensities) for isotropic
    hyperfine to groups of equivalent nuclei [(I, a_mT, n)], first order."""
    b0 = field_for_g(g, freq_GHz)
    pos = np.array([b0])
    amp = np.array([1.0])
    mis = [()]
    for (I, a, n) in nuclei:
        m_vals = np.arange(-I * n, I * n + 1e-9, 1.0)
        # multiplicity of total M for n spins I: repeated convolution
        single = np.ones(int(round(2 * I + 1)))
        mult = np.array([1.0])
        for _ in range(int(n)):
            mult = np.convolve(mult, single)
        new_pos, new_amp = [], []
        for M, w in zip(m_vals, mult):
            new_pos.append(pos - M * a)
            new_amp.append(amp * w)
        pos = np.concatenate(new_pos)
        amp = np.concatenate(new_amp)
    return pos, amp / amp.sum()


def _breit_rabi_shift(J: float, m: float, a_mT: float, g: float, freq_GHz: float) -> float:
    """Exact resonance field minus B0 (mT) of the EPR line (+1/2, m) <->
    (-1/2, m) for S = 1/2 coupled isotropically (a, here in mT) to one spin J,
    nuclear Zeeman neglected. The 2x2 blocks of fixed M = mS + mI are solved
    in closed form and the field found by root finding."""
    k = g * MU_B / H * 1e-9                         # MHz per mT
    hv = freq_GHz * 1000.0
    a = a_mT * k
    b0 = field_for_g(g, freq_GHz)
    if J == 0 or a == 0:
        return 0.0

    def upper(G, mm):           # |+1/2, mm> branch of block M = mm + 1/2
        if mm >= J - 1e-9:
            return G / 2 + a * J / 2
        h11, h22 = G / 2 + a * mm / 2, -G / 2 - a * (mm + 1) / 2
        h12 = a / 2 * math.sqrt(J * (J + 1) - mm * (mm + 1))
        return (h11 + h22) / 2 + math.hypot((h11 - h22) / 2, h12)

    def lower(G, mm):           # |-1/2, mm> branch of block M = mm - 1/2
        if mm <= -J + 1e-9:
            return -G / 2 + a * J / 2
        h11, h22 = G / 2 + a * (mm - 1) / 2, -G / 2 - a * mm / 2
        h12 = a / 2 * math.sqrt(J * (J + 1) - (mm - 1) * mm)
        return (h11 + h22) / 2 - math.hypot((h11 - h22) / 2, h12)

    def f(B):
        G = B * k * 1.0
        return upper(G, m) - lower(G, m) - hv

    first = b0 - m * a_mT
    span = 2 * abs(a_mT) * (J + 1) + 1e-3 * b0 + 1e-6
    lo, hi = max(first - span, 1e-9), first + span
    for _ in range(20):
        if f(lo) * f(hi) < 0:
            break
        lo, hi = max(lo - span, 1e-9), hi + span
    else:
        return float(first - b0)
    return float(optimize.brentq(f, lo, hi, xtol=1e-12 * max(b0, 1.0), rtol=1e-14)) - b0


def _lines_exact(freq_GHz, g, nuclei):
    """As _lines, with exact (Breit-Rabi) positions. Each group of n
    equivalent nuclei is decomposed into total spins J with their
    multiplicities; shifts of different groups add."""
    b0 = field_for_g(g, freq_GHz)
    pos = np.array([b0])
    amp = np.array([1.0])
    for (I, a, n) in nuclei:
        single = np.ones(int(round(2 * I + 1)))
        cnt = np.array([1.0])
        for _ in range(int(n)):
            cnt = np.convolve(cnt, single)
        Mmax = I * n
        Ms = np.arange(-Mmax, Mmax + 1e-9, 1.0)
        c_of = {round(M, 6): c for M, c in zip(Ms, cnt)}
        shifts, weights = [], []
        J = Mmax
        while J >= -1e-9:
            mult = c_of.get(round(J, 6), 0.0) - c_of.get(round(J + 1, 6), 0.0)
            if mult > 0:
                for m in np.arange(-J, J + 1e-9, 1.0):
                    shifts.append(_breit_rabi_shift(J, m, a, g, freq_GHz))
                    weights.append(mult)
            J -= 1.0
        shifts = np.array(shifts)
        weights = np.array(weights)
        pos = (pos[:, None] + shifts[None, :]).ravel()
        amp = (amp[:, None] * weights[None, :]).ravel()
    return pos, amp / amp.sum()


def simulate_iso(field_mT, freq_GHz, g, *, nuclei: Sequence = (), lw_mT: float = 0.1,
                 shape: str = "lorentzian", lw_gauss_mT: Optional[float] = None, harmonic: int = 1,
                 modamp_mT: Optional[float] = None, area: float = 1.0,
                 linewidth_mI: Optional[tuple] = None, second_order: bool = False,
                 exact: bool = False) -> np.ndarray:
    """Isotropic fast-motion CW spectrum (EasySpin's garlic, simplified).
    nuclei: [(I, a_mT, n_equivalent)], first-order positions; for a single
    nitroxide-like nucleus `linewidth_mI=(A, B, C)` gives dBpp(mI) =
    A + B mI + C mI^2 and `second_order` adds -(a^2/2B0)(I(I+1) - mI^2).
    lw_mT is the derivative peak-to-peak width (Lorentzian); lw_gauss_mT adds
    Gaussian broadening (Voigt). harmonic 0: absorption, 1: first derivative,
    with the exact first-harmonic modulation kernel if modamp_mT is given.

    exact=True places the lines at the exact (Breit-Rabi) fields: each group
    of n equivalent nuclei is treated in its coupled representation (total
    nuclear spin J, so the second-order splitting of equivalent nuclei
    appears), groups are combined additively (cross terms between groups are
    third order for isotropic couplings), nuclear Zeeman neglected; a_mT is
    converted to MHz with this g (Breit and Rabi, Phys. Rev. 38, 2082 (1931);
    Weil and Bolton, App. C). Use it for large couplings (31P, isotropic Cu)
    or many nuclei. nuclei may also be epr.Nucleus objects with isotropic A
    (an element name is its natural isotope mixture)."""
    x = np.asarray(field_mT, float)
    if any(hasattr(n, "A_spec") for n in nuclei):          # epr.Nucleus objects
        from .epr_sim import _iso_nuclei_mixture
        kw = dict(lw_mT=lw_mT, shape=shape, lw_gauss_mT=lw_gauss_mT, harmonic=harmonic, modamp_mT=modamp_mT,
                  area=area, linewidth_mI=linewidth_mI, second_order=second_order, exact=exact)
        return sum(w * simulate_iso(field_mT, freq_GHz, g, nuclei=tuples, **kw)
                   for w, tuples in _iso_nuclei_mixture(nuclei, g))
    if exact and second_order:
        raise ValueError("exact=True already includes all orders; drop second_order")
    if linewidth_mI is not None or second_order:
        if len(nuclei) != 1 or nuclei[0][2] != 1:
            raise ValueError("linewidth_mI / second_order: give exactly one nucleus with n = 1")
        I, a, _ = nuclei[0]
        b0 = field_for_g(g, freq_GHz)
        mis = np.arange(I, -I - 1e-9, -1.0)
        pos = b0 - mis * a
        if second_order:
            pos = pos - a * a / (2 * b0) * (I * (I + 1) - mis ** 2)
        if exact:
            pos = b0 + np.array([_breit_rabi_shift(I, m, a, g, freq_GHz) for m in mis])
        amps = np.full(mis.size, 1.0 / mis.size)
        widths = (np.array([linewidth_mI[0] + linewidth_mI[1] * m + linewidth_mI[2] * m * m for m in mis])
                  if linewidth_mI is not None else np.full(mis.size, lw_mT))
    else:
        pos, amps = _lines_exact(freq_GHz, g, nuclei) if exact else _lines(freq_GHz, g, nuclei)
        widths = np.full(pos.size, lw_mT)

    def absorption(b):
        out = np.zeros_like(b, dtype=float)
        for p_, a_, w_ in zip(pos, amps, widths):
            if shape.lower().startswith("gau"):
                s = w_ / 2
                out += a_ * np.exp(-0.5 * ((b - p_) / s) ** 2) / (s * math.sqrt(2 * math.pi))
            else:
                gam = math.sqrt(3) * w_ / 2
                if lw_gauss_mT:
                    out += a_ * special.voigt_profile(b - p_, lw_gauss_mT / 2, gam)
                else:
                    out += a_ / math.pi * gam / ((b - p_) ** 2 + gam ** 2)
        return out * area

    if harmonic == 0:
        return absorption(x)
    if modamp_mT:
        return modulate_abs(absorption, x, modamp_mT)
    h = 1e-4 * (x[1] - x[0]) if x.size > 1 else 1e-6
    return (absorption(x + h) - absorption(x - h)) / (2 * h)


def modulate_abs(absorption, field_mT, modamp_mT: float, n_theta: int = 256) -> np.ndarray:
    """Exact first-harmonic signal for sinusoidal modulation of peak-to-peak
    amplitude modamp (Bruker's ModAmp), scaled to (modamp/2) dA/dB in the
    small-modulation limit; its double integral is exactly (modamp/2) * area.
    `absorption` is a function of the field."""
    x = np.asarray(field_mT, float)
    th = (np.arange(n_theta) + 0.5) * math.pi / n_theta
    acc = np.zeros_like(x)
    for t in th:
        acc += absorption(x + modamp_mT / 2 * math.cos(t)) * math.cos(t)
    return acc * 2 / n_theta / (modamp_mT / 2)


# ==============================================================================
# DEER
# ==============================================================================

def correct_phase(V) -> tuple:
    """Rotate a complex trace so the imaginary part carries no signal:
    returns (real part, phase_deg, imag_rms). The phase maximises the real
    part's energy (closed form, 0.5 * angle(sum V^2)); the sign is chosen so
    the real part is positive. Never use |V|: it biases the noise and is
    non-linear near zero. imag_rms is a noise estimate."""
    V = np.asarray(V)
    if not np.iscomplexobj(V):
        return V.astype(float), 0.0, None
    phi = 0.5 * np.angle(np.sum(V ** 2))
    W = V * np.exp(-1j * phi)
    if np.sum(W.real) < 0:
        W = -W
        phi += math.pi
    phi = (phi + math.pi) % (2 * math.pi) - math.pi
    return W.real, math.degrees(phi), float(np.std(W.imag))


def average_scans(V2d, axis: int = 0) -> np.ndarray:
    """Mean over scans (rows of an EPRData 2D `y`)."""
    return np.mean(np.asarray(V2d), axis=axis)


def zero_time(t_us, V) -> float:
    """Zero time: the maximum of the (phased, real) echo trace, refined by a
    quadratic fit over the points within 2% of the maximum. fit_deer refines
    it further."""
    t = np.asarray(t_us, float)
    v = np.real(np.asarray(V))
    i = int(np.argmax(v))
    top = v[i]
    m = np.where(v >= top - 0.02 * (top - np.min(v)))[0]
    m = m[(m >= i - 25) & (m <= i + 25)]
    if m.size >= 5:
        c = np.polyfit(t[m], v[m], 2)
        if c[0] < 0:
            tz = -c[1] / (2 * c[0])
            if t[m].min() <= tz <= t[m].max():
                return float(tz)
    return float(t[i])


def dipolar_kernel(t_us, r_nm, *, weighted: bool = True) -> np.ndarray:
    """K(t, r) = int_0^1 cos[(1 - 3z^2) w t] dz, w = 2 pi NU_DD / r^3 (rad/us),
    in closed form with Fresnel integrals (scipy.special.fresnel returns
    (S, C), in that order). weighted: times the r step, so V = K @ P for P in
    1/nm with sum(P) * dr = 1."""
    t = np.abs(np.asarray(t_us, float))
    r = np.asarray(r_nm, float)
    w = 2 * math.pi * NU_DD / r[None, :] ** 3
    wt = w * t[:, None]
    K = np.ones_like(wt)
    m = wt > 1e-9
    z = np.sqrt(6 * wt[m] / math.pi)
    S, C = special.fresnel(z)
    K[m] = np.sqrt(math.pi / (6 * wt[m])) * (np.cos(wt[m]) * C + np.sin(wt[m]) * S)
    if weighted:
        K = K * np.gradient(r)[None, :]
    return K


def bg_hom3d(t_us, conc_uM, lam):
    """Homogeneous 3D background exp(-k|t|), k = K_HOM3D * lam * c[uM]."""
    return np.exp(-K_HOM3D * lam * conc_uM * np.abs(np.asarray(t_us, float)))


def bg_strexp(t_us, k_per_us, dim: float = 3.0):
    """Stretched exponential exp(-(k|t|)^(dim/3)); dim 3 = homogeneous 3D,
    about 2 for membranes."""
    return np.exp(-(k_per_us * np.abs(np.asarray(t_us, float))) ** (dim / 3))


def bg_rate_to_conc(k_per_us, lam) -> float:
    """Spin concentration in uM from a 3D background rate: c = k / (K_HOM3D lam)."""
    return float(k_per_us) / (K_HOM3D * float(lam))


def distance_limits(tmax_us) -> dict:
    """Distances a trace of length tmax supports (Schiemann et al. 2021):
    mean distance up to (NU_DD tmax)^(1/3) = 3.73 tmax^(1/3) nm (one dipolar
    period), width and shape up to (NU_DD tmax / 2)^(1/3) = 2.96 tmax^(1/3) nm
    (two periods)."""
    t = float(tmax_us)
    return {"r_max_mean_nm": (NU_DD * t) ** (1 / 3), "r_max_width_nm": (NU_DD * t / 2) ** (1 / 3),
            "tmax_us": t}


class LabelingEfficiency(float):
    """A float (the labelling efficiency) that also carries .formula,
    .scheme, .lam, .inversion and .warnings."""

    def __repr__(self):
        return f"{float(self):.4g} (labelling efficiency, {self.scheme}: {self.formula})"


def labeling_efficiency(lam, inversion, scheme: str = "both_equal") -> LabelingEfficiency:
    """Spin-labelling efficiency f from the DEER modulation depth of a
    doubly labelled molecule (two sites), and the pump inversion efficiency p
    (the fraction of partner spins the pump pulse inverts; a property of the
    spectrometer and pulses, from the question or a standard, often 0.3-0.5).

    scheme="both_equal": both sites labelled with the same efficiency f:
        lambda = p f, so f = lambda / p
    scheme="one_full": site 1 fully labelled, site 2 with efficiency f; the
        observer is either site, so lambda = 2 p f / (1 + f), and
        f = lambda / (2 p - lambda)
    Prints the formula it used and returns f (a float with .formula).
    lambda comes from epr.fit_deer (r.lam), never 1 - V(end)/V(0)."""
    lam, p = float(lam), float(inversion)
    if not 0 < p <= 1:
        raise ValueError(f"inversion efficiency {p:g} must be in (0, 1] (a fraction, not a percentage)")
    if not 0 < lam < 1:
        raise ValueError(f"modulation depth {lam:g} must be in (0, 1): pass r.lam from fit_deer")
    warns = []
    if scheme == "both_equal":
        f, formula = lam / p, f"f = lambda / p = {lam:.4g} / {p:.4g}"
    elif scheme == "one_full":
        f, formula = lam / (2 * p - lam), f"f = lambda / (2p - lambda) = {lam:.4g} / (2 * {p:.4g} - {lam:.4g})"
    else:
        raise ValueError("scheme: 'both_equal' (both sites labelled alike) or 'one_full' (site 1 fully labelled)")
    if lam > p:
        warns.append(f"lambda {lam:.3g} exceeds the inversion efficiency {p:.3g}, so f > 1: check p, or the "
                     f"sample has more than two spins per molecule (aggregation)")
    out = LabelingEfficiency(f)
    out.formula, out.scheme, out.lam, out.inversion, out.warnings = formula, scheme, lam, p, warns
    print(f"labelling efficiency ({scheme}): {formula} = {f:.4g}" + "".join(f"\n  WARNING: {w}" for w in warns))
    return out


def simulate_deer(t_us, r_nm, P, lam, *, conc_uM=None, k_per_us=None, dim: float = 3.0, t0_us: float = 0.0,
                  phase_deg: float = 0.0, noise: float = 0.0, V0: float = 1.0, seed: int = 0) -> np.ndarray:
    """V(t) = V0 [(1 - lam) + lam K P] B(t - t0) e^(i phase) + noise. P is
    normalised here (sum P dr = 1). Complex if phase_deg or noise is given."""
    t = np.asarray(t_us, float) - t0_us
    r = np.asarray(r_nm, float)
    P = np.asarray(P, float)
    P = P / np.sum(P * np.gradient(r))
    form = (1 - lam) + lam * dipolar_kernel(t, r) @ P
    if k_per_us is None:
        k_per_us = K_HOM3D * lam * (conc_uM or 0.0)
    V = V0 * form * bg_strexp(t, k_per_us, dim)
    if phase_deg or noise:
        rng = np.random.default_rng(seed)
        V = V * np.exp(1j * math.radians(phase_deg))
        if noise:
            V = V + rng.normal(0, noise, V.shape) + 1j * rng.normal(0, noise, V.shape)
    return V


def gauss_P(r_nm, means, stds, weights=None) -> np.ndarray:
    """Sum of Gaussians (std, not FWHM; FWHM = 2.3548 std), normalised."""
    r = np.asarray(r_nm, float)
    means, stds = np.atleast_1d(means), np.atleast_1d(stds)
    w = np.ones(len(means)) if weights is None else np.asarray(weights, float)
    P = sum(wi * np.exp(-0.5 * ((r - m) / s) ** 2) / (s * math.sqrt(2 * math.pi)) for wi, m, s in zip(w, means, stds))
    return P / np.sum(P * np.gradient(r))


@dataclass
class DeerResult(FieldHelp):
    method: str
    background: str
    r_nm: np.ndarray
    P: np.ndarray
    mean_nm: float
    std_nm: float
    modal_nm: float
    populations: list               # [{"r_nm": mode, "fraction": weight, "std_nm": ...}]
    lam: float
    k_per_us: float
    conc_uM: Optional[float]
    dim: float
    t0_us: float
    alpha: Optional[float]
    criterion: str
    t_us: np.ndarray
    V: np.ndarray                   # the trace fitted (real, phased, normalised)
    V_fit: np.ndarray
    residual_rms: float
    snr: float
    phase_deg: Optional[float]
    limits: dict
    warnings: list
    gauss: list = field(default_factory=list)
    ci: dict = field(default_factory=dict)
    aic: Optional[float] = None
    P_ci95: Optional[tuple] = None
    main_nm: Optional[float] = None         # main distance of the reliable part (see fit_deer)
    reliable_populations: list = field(default_factory=list)
    long_fraction: Optional[float] = None   # set when the trace holds distances beyond the limit

    @property
    def t0_ns(self) -> float:
        return 1000 * self.t0_us

    @property
    def mod_depth(self) -> float:
        return self.lam

    @property
    def fraction_above_limit(self) -> float:
        m = self.r_nm > self.limits["r_max_mean_nm"]
        return float(np.sum(self.P[m] * np.gradient(self.r_nm)[m]))

    def summary(self) -> str:
        L = self.limits
        out = [f"DEER analysis (labkit.epr.fit_deer, {self.method}, background {self.background}; one-step fit)",
               f"  mean distance <r> = {self.mean_nm:.3f} nm{self._ci('mean_nm')}, modal {self.modal_nm:.3f} nm, "
               f"std {self.std_nm:.3f} nm{self._ci('std_nm')} (std, not FWHM; FWHM of a Gaussian = 2.355 std)",
               "  populations: " + "; ".join(f"{p['r_nm']:.2f} nm ({100 * p['fraction']:.0f}%)" for p in self.populations),
               f"  modulation depth lambda = {self.lam:.3f}{self._ci('lam')}",
               f"  background: k = {self.k_per_us:.4g} 1/us, dim = {self.dim:.2f}"
               + (f", spin concentration {self.conc_uM:.4g} uM (3D homogeneous)" if self.conc_uM else ""),
               f"  zero time t0 = {1000 * self.t0_us:.1f} ns" + (f", phase {self.phase_deg:.1f} deg" if self.phase_deg is not None else ""),
               f"  regularisation: alpha = {self.alpha:.3g} by {self.criterion}" if self.alpha else "  parametric fit (no regularisation)",
               f"  residual rms {self.residual_rms:.3g}, SNR (lambda / noise) {self.snr:.3g}",
               f"  trace {L['tmax_us']:.3g} us supports <r> up to {L['r_max_mean_nm']:.2f} nm and width/shape up to "
               f"{L['r_max_width_nm']:.2f} nm"]
        if self.long_fraction is not None:
            out.append(f"  main distance (reliable part) = {self.main_nm:.3f} nm (r.main_nm), from a fit allowed "
                       f"past {L['r_max_mean_nm']:.2f} nm; populations below that limit: "
                       + "; ".join(f"{p['r_nm']:.2f} nm ({100 * p['fraction']:.0f}%)" for p in self.reliable_populations)
                       + f"; {100 * self.long_fraction:.0f}% above it (unreliable: this trace is too short to "
                       f"measure it). Report main_nm as the main distance, not the mean or modal above")
        if self.warnings:
            out.append("  WARNINGS (report them): " + " | ".join(self.warnings))
        return "\n".join(out)

    def _ci(self, k):
        c = self.ci.get(k)
        return f" (95% CI {c[0]:.3f}-{c[1]:.3f})" if c else ""

    def __repr__(self):
        return self.summary()


def _second_diff(n):
    L = np.zeros((n - 2, n))
    for i in range(n - 2):
        L[i, i:i + 3] = [1, -2, 1]
    return L


def _nnls_tikhonov(A, v, L, alpha, n_free):
    """min ||A x - v||^2 + alpha^2 ||L x[n_free:]||^2, x >= 0 (NNLS on the
    augmented system). The first n_free columns are not regularised."""
    n = A.shape[1]
    Lfull = np.zeros((L.shape[0], n))
    Lfull[:, n_free:] = L
    Aa = np.vstack([A, alpha * Lfull])
    va = np.concatenate([v, np.zeros(L.shape[0])])
    x, _ = optimize.nnls(Aa, va, maxiter=50 * n)
    return x


def _aic(A, v, L, alpha, x, n_free):
    """AIC = N log(RSS/N) + 2 tr(H), H the influence matrix of the
    regularised (unconstrained) problem. Counting only the non-zero NNLS
    coefficients undercounts the degrees of freedom and drives alpha too low
    (P(r) breaks into noise peaks)."""
    Lfull = np.zeros((L.shape[0], A.shape[1]))
    Lfull[:, n_free:] = L
    try:
        AtA = A.T @ A
        # tr(A (A'A + a^2 L'L)^-1 A') = tr((A'A + a^2 L'L)^-1 A'A): an M x M solve, not N x N
        dof = float(np.trace(np.linalg.solve(AtA + alpha ** 2 * Lfull.T @ Lfull + 1e-12 * np.eye(A.shape[1]), AtA)))
    except np.linalg.LinAlgError:
        dof = float(np.sum(x > 0))
    rss = float(np.sum((A @ x - v) ** 2))
    N = len(v)
    return N * math.log(rss / N + 1e-300) + 2 * dof


_BG_AIC = 8.0        # AIC units: d = 2 fits about as well as hom3d (S8: membranes 2.6-4.7, others >= 12.9)
_BG_SHIFT_NM = 0.05  # and moves the mean distance by more than this


def fit_deer(t_us, V, *, r_nm=None, method: str = "tikhonov", n_gauss: int = 1, background: str = "hom3d",
             fit_t0: bool = True, t0_us: Optional[float] = None, n_boot: int = 0, seed: int = 0,
             alpha: Optional[float] = None, check_long: bool = True,
             check_background: bool = True, dim: Optional[float] = None) -> DeerResult:
    """One-step 4-pulse DEER analysis: P(r), modulation depth lambda,
    background and zero time fitted together (the community recommendation;
    no separate background-then-distance step).

    t_us      time in MICROSECONDS (divide ns by 1000); the raw axis, t0 is
              fitted (or give t0_us and fit_t0=False).
    V         the trace: complex is phased here (correct_phase), real is
              taken as already phased. Scans: average_scans first.
    method    "tikhonov": non-negative Tikhonov (second derivative), alpha by
              AIC; "gauss": a sum of n_gauss Gaussians (std, not FWHM).
    background "hom3d" (exp(-k t), k = 9.974e-4 lambda c[uM]: frozen
              solution), "strexp" (exp(-(k t)^(d/3)), fractal dimension d
              fitted in 1-3; dim=2.0 fixes it: spins in a membrane plane),
              or "none".
    r_nm      distance grid; default from about 1.5 nm to the longest distance
              the trace supports (distance_limits: 3.73 tmax^(1/3) nm). Beyond
              it, P(r) and the background cannot be told apart.
    check_long  also fit with r up to 1.5x that limit and warn when that fit
              puts distance there and fits better: then the trace may hold
              distances it is too short to measure (or the background model
              is wrong); report the long part as unreliable. Then the
              distribution limited to the trace can split or shift the short
              peak (the long part leaks into the background), so the result
              takes the short part from the wider fit: r.main_nm (main
              distance of the reliable part), r.reliable_populations (its
              populations below the limit, fractions of the whole) and
              r.long_fraction (the part above it); summary() prints them
              as "main distance (reliable part)". Otherwise r.main_nm is
              the largest population of this fit, r.reliable_populations
              equals r.populations and r.long_fraction is None, so code can
              always read r.main_nm.
    check_background  with the default hom3d background, also fit a
              membrane background (strexp, dim=2) and warn when it fits about as
              well and gives other distances: the trace cannot choose, the
              sample does (use what the question says, or ask the user).
    n_boot    residual bootstrap repeats for 95% CIs (about 1-2 s each). The
              checks above run on the point fit (not on the bootstrap fits).
    Returns DeerResult: print it (summary() has what a report needs), with
    warnings for distance mass beyond what the trace supports.
    """
    t = np.asarray(t_us, float)
    if np.nanmax(np.abs(t)) > 50:
        raise ValueError(f"t goes to {np.nanmax(t):.4g}: that looks like ns. Pass t_us = t_ns / 1000.")
    V = np.asarray(V)
    if V.ndim == 2:
        raise ValueError("V is 2D (scans): average_scans(V) first, or pass one trace")
    warns = []
    phase = None
    if np.iscomplexobj(V):
        v, phase, imag_rms = correct_phase(V)
    else:
        v, imag_rms = V.astype(float), None
    if t0_us is None:
        t0_us = zero_time(t, v)
    scale = float(np.max(v))
    v = v / scale
    tmax = float(t[-1] - t0_us)
    lim = distance_limits(tmax)
    auto_r = r_nm is None
    if r_nm is None:
        dt = float(np.median(np.diff(t)))
        rmin = max(1.5, (4 * NU_DD * dt) ** (1 / 3))
        r_nm = np.arange(rmin, min(10.0, lim["r_max_mean_nm"]) + 1e-9, 0.02)
    r = np.asarray(r_nm, float)
    L = _second_diff(len(r))
    nb = {"hom3d": 1, "strexp": 2, "none": 0}[background]
    dim_fixed = None if dim is None else float(dim)
    if background == "strexp" and dim_fixed is not None:
        if not 1.0 <= dim_fixed <= 3.0:
            raise ValueError("dim (fractal dimension of the background) must be between 1 and 3")
        nb = 1

    def bg(tt, th):
        if background == "none":
            return np.ones_like(tt)
        k = th[0]
        d = (dim_fixed if dim_fixed is not None else th[1]) if background == "strexp" else 3.0
        return np.exp(-(max(k, 0.0) * np.abs(tt)) ** (d / 3))

    def design(th):
        t0 = th[nb] if fit_t0 else t0_us
        tt = t - t0
        B = bg(tt, th)
        K = dipolar_kernel(tt, r)
        return np.column_stack([B, B[:, None] * K]), tt

    # starting background rate from the last half of the trace
    tail = t - t0_us > 0.5 * tmax
    k0 = 0.1
    if background != "none" and tail.sum() > 5 and np.all(v[tail] > 0):
        slope = np.polyfit(t[tail] - t0_us, np.log(v[tail]), 1)[0]
        k0 = float(min(max(-slope, 1e-3), 3.0))
    th0 = ([k0] if nb >= 1 else []) + ([2.5] if nb == 2 else []) + ([t0_us] if fit_t0 else [])
    lb = ([0.0] if nb >= 1 else []) + ([1.0] if nb == 2 else []) + ([t0_us - 0.1] if fit_t0 else [])
    ub = ([10.0] if nb >= 1 else []) + ([3.0] if nb == 2 else []) + ([t0_us + 0.1] if fit_t0 else [])

    if method.startswith("tik"):
        alphas = np.logspace(-3, 2, 16)

        def pick_alpha(th):
            A, _ = design(th)
            best = None
            for a in alphas:
                x = _nnls_tikhonov(A, v, L, a, 1)
                s = _aic(A, v, L, a, x, 1)
                if best is None or s < best[0]:
                    best = (s, a)
            return best[1]

        def solve(th, a):
            A, _ = design(th)
            x = _nnls_tikhonov(A, v, L, a, 1)
            return A, x

        def resid(th, a):
            A, x = solve(th, a)
            return np.concatenate([A @ x - v, a * (L @ x[1:])])

        if nb == 2 and not alpha:
            # the stretched background has local minima in (k, d): start from several d
            cands = []
            for d0 in (1.5, 2.25, 3.0):
                tk = np.array(th0, float)
                tk[1] = min(d0, 2.999)
                a_k = pick_alpha(tk)
                sol = optimize.least_squares(resid, tk, args=(a_k,), bounds=(lb, ub), diff_step=1e-3,
                                             xtol=1e-6, ftol=1e-6)
                A_k, x_k = solve(sol.x, a_k)
                cands.append((_aic(A_k, v, L, a_k, x_k, 1), sol.x, a_k))
            _, th0b, a_cur = min(cands, key=lambda c: c[0])
            th0 = list(th0b)
        a_cur = alpha if alpha else (a_cur if nb == 2 else pick_alpha(np.array(th0, float)))
        th = np.array(th0, float)
        for _ in range(2):
            if th.size:
                sol = optimize.least_squares(resid, th, args=(a_cur,), bounds=(lb, ub), diff_step=1e-3,
                                             xtol=1e-7, ftol=1e-7)
                th = sol.x
            if alpha:
                break
            a_new = pick_alpha(th)
            if a_new == a_cur:
                break
            a_cur = a_new
        A, x = solve(th, a_cur)
        u, q = x[0], x[1:]                   # q = V0 * lambda * P (1/nm); K carries the dr weight
        area = float(np.sum(q * np.gradient(r)))
        V0 = u + area
        lam = area / V0 if V0 > 0 else 0.0
        P = q / area if area > 0 else np.zeros_like(r)
        vfit = A @ x
        crit, alpha_used, gpar = "AIC", float(a_cur), []
        aic_val = _aic(A, v, L, a_cur, x, 1)
    elif method.startswith("gau"):
        ng = int(n_gauss)
        span = lim["r_max_mean_nm"] - 1.8

        def unpack(p):
            thb = p[:len(th0)]
            g = p[len(th0):]
            return thb, g

        def model(p):
            thb, g = unpack(p)
            A, _ = design(thb)
            means = g[0:ng]
            stds = np.abs(g[ng:2 * ng]) + 1e-3
            ws = np.abs(g[2 * ng:3 * ng - 1])
            ws = np.concatenate([ws, [max(1e-6, 1 - ws.sum())]]) if ng > 1 else np.array([1.0])
            Pg = gauss_P(r, means, stds, ws)
            V0, lam_ = g[3 * ng - 1], g[3 * ng]
            # A[:, 0] is B; A[:, 1:] is B * K with the dr weight, so it takes P in 1/nm
            return A[:, 0] * V0 * (1 - lam_) + A[:, 1:] @ (V0 * lam_ * Pg), (means, stds, ws, V0, lam_, Pg)

        best = None
        rng = np.random.default_rng(seed)
        starts = []
        for s in range(8 if ng > 1 else 4):
            if ng == 1:
                mu = [1.8 + span * (s + 0.5) / 4]
            else:
                mu = sorted(rng.uniform(1.8, lim["r_max_mean_nm"], ng))
            starts.append(list(th0) + list(mu) + [0.3] * ng + ([1.0 / ng] * (ng - 1)) + [1.0, 0.3])
        glb = [1.0] * ng + [0.02] * ng + [0.0] * (ng - 1) + [0.5, 0.0]
        gub = [r[-1]] * ng + [2.0] * ng + [1.0] * (ng - 1) + [2.0, 1.0]
        for p0 in starts:
            try:
                sol = optimize.least_squares(lambda p: model(p)[0] - v, p0, bounds=(lb + glb, ub + gub))
            except ValueError:
                continue
            if best is None or sol.cost < best.cost:
                best = sol
        p = best.x
        th = p[:len(th0)]
        vfit, (means, stds, ws, V0, lam, P) = model(p)
        lam = float(lam)
        order = np.argsort(means)
        gpar = [{"mean_nm": float(means[i]), "std_nm": float(stds[i]), "fwhm_nm": float(2.3548 * stds[i]),
                 "fraction": float(ws[i] / np.sum(ws))} for i in order]
        crit, alpha_used = "parametric", None
        aic_val = None
    else:
        raise ValueError("method: 'tikhonov' or 'gauss'")

    k = float(th[0]) if nb >= 1 else 0.0
    dim = float(th[1]) if nb == 2 else (dim_fixed if background == "strexp" and dim_fixed is not None else 3.0)
    t0 = float(th[nb]) if fit_t0 else float(t0_us)
    dr = np.gradient(r)
    mean = float(np.sum(r * P * dr))
    std = float(math.sqrt(max(0.0, np.sum((r - mean) ** 2 * P * dr))))
    modal = float(r[int(np.argmax(P))])
    pops = _populations(r, P) if not gpar else [{"r_nm": g["mean_nm"], "fraction": g["fraction"],
                                                 "std_nm": g["std_nm"]} for g in gpar]
    resid_v = v - vfit
    noise = float(np.std(resid_v))
    snr = lam / noise if noise > 0 else float("inf")
    conc = bg_rate_to_conc(k, lam) if background == "hom3d" and lam > 0 and k > 0 else None
    # warnings
    above = float(np.sum((P * dr)[r > lim["r_max_mean_nm"]]))
    if above > 0.05:
        warns.append(f"{100 * above:.0f}% of P(r) lies above {lim['r_max_mean_nm']:.2f} nm, beyond what a "
                     f"{tmax:.2g} us trace supports (mean distances up to {lim['r_max_mean_nm']:.2f} nm, widths "
                     f"and shape up to {lim['r_max_width_nm']:.2f} nm): report that part as unreliable, or "
                     f"record a longer trace")
    main_pops, long_frac = pops, None
    if check_long and auto_r and method.startswith("tik"):
        r_ext = np.arange(r[0], min(12.0, 1.5 * lim["r_max_mean_nm"]) + 1e-9, 0.02)
        ext = fit_deer(t, v * scale, r_nm=r_ext, method=method, background=background, fit_t0=False,
                       t0_us=t0, check_long=False, check_background=False, alpha=alpha_used,
                       dim=dim_fixed)
        beyond = float(np.sum((ext.P * np.gradient(ext.r_nm))[ext.r_nm > lim["r_max_mean_nm"]]))
        # The wider fit always fits at least as well; only a clear gain counts
        # (on the S8 fixtures: 52 AIC units and 13% lower residual for a trace
        # really holding a long distance, at most 4.5 units and 1% otherwise).
        if beyond > 0.05 and ext.aic is not None and aic_val is not None and \
                aic_val - ext.aic > 20 and ext.residual_rms < 0.97 * noise:
            warns.append(f"the trace looks like it holds distances beyond {lim['r_max_mean_nm']:.2f} nm, the "
                         f"longest a {tmax:.2g} us trace supports: a fit allowed to go further puts "
                         f"{100 * beyond:.0f}% of P(r) there. Distances above that limit cannot be measured "
                         f"from this trace (they look like background): report them as unreliable and "
                         f"recommend a longer trace; the distribution above is limited to {lim['r_max_mean_nm']:.2f} nm. "
                         f"The short part is taken from the wider fit: main distance (reliable part) r.main_nm")
            # The short part: the wider fit with its own alpha (by AIC). The alpha
            # of the limited fit, tuned to a distribution squeezed below the
            # limit, can split the short peak on the wider grid.
            ext_a = fit_deer(t, v * scale, r_nm=r_ext, method=method, background=background, fit_t0=False,
                             t0_us=t0, check_long=False, check_background=False, dim=dim_fixed)
            beyond_a = float(np.sum((ext_a.P * np.gradient(ext_a.r_nm))[ext_a.r_nm > lim["r_max_mean_nm"]]))
            below = [p_ for p_ in ext_a.populations if p_["r_nm"] < lim["r_max_mean_nm"]]
            if below:
                # populations' fractions are over the populations kept; give them as fractions of the whole P(r)
                tot = sum(p_["fraction"] for p_ in below)
                main_pops = [dict(p_, fraction=p_["fraction"] / tot * (1 - beyond_a)) for p_ in below]
                long_frac = beyond_a
    if check_background and background == "hom3d" and method.startswith("tik"):
        # A membrane background (d about 2) and hom3d (d = 3) often fit a
        # trace equally well; a free d is a flat valley (on the S8 membrane
        # trace d = 2.2 and d = 3 differ by 0.1 AIC units). So fit d = 2 and
        # warn when it fits about as well and moves the distances.
        alt = fit_deer(t, v * scale, r_nm=r, method=method, background="strexp", dim=2.0,
                       fit_t0=fit_t0, t0_us=None if fit_t0 else t0, alpha=alpha_used,
                       check_long=False, check_background=False)
        if alt.aic <= aic_val + _BG_AIC and abs(alt.mean_nm - mean) > _BG_SHIFT_NM:
            warns.append(f"the background model changes the answer: hom3d (frozen solution) gives mean "
                         f"{mean:.2f} nm, lambda {lam:.3f}; a membrane background (strexp, d = 2) gives mean "
                         f"{alt.mean_nm:.2f} nm, lambda {alt.lam:.3f}, and the trace fits both about equally "
                         f"well. The sample decides: membrane, liposome, micelle or bicelle (spins in a plane): "
                         f"background='strexp', dim=2.0; crowded or other: background='strexp' (d fitted); "
                         f"frozen solution: keep hom3d. If the question does not say which, ask the user "
                         f"before reporting distances")
    long_pops = [p_ for p_ in pops if p_["r_nm"] > lim["r_max_width_nm"] and p_["fraction"] > 0.05]
    if long_pops and above <= 0.05:
        where = ", ".join("%.2f" % p_["r_nm"] for p_ in long_pops)
        warns.append(f"population(s) at {where} nm are beyond "
                     f"{lim['r_max_width_nm']:.2f} nm, where the width and shape are not reliable for this trace length")
    if lam > 0.6:
        warns.append(f"modulation depth {lam:.2f} > 0.6: more than two spins per molecule? (ghost peaks)")
    if snr < 10:
        warns.append(f"SNR {snr:.3g} (lambda / noise) is low: distances are uncertain")
    if imag_rms is not None and noise > 0 and imag_rms / scale > 2 * noise:
        warns.append("the imaginary part after phasing is larger than the fit residual: check the phase "
                     "or the trace (imaginary part should be noise)")
    if fit_t0 and (abs(t0 - lb[nb]) < 1e-3 or abs(t0 - ub[nb]) < 1e-3):
        warns.append("zero time at its search limit: pass t0_us")
    if nb == 2 and (abs(dim - 1.0) < 1e-3 or abs(dim - 3.0) < 1e-3):
        warns.append(f"background dimension at its bound ({dim:.2f})")

    ci: dict = {}
    P_ci = None
    if n_boot and n_boot > 0:
        rng = np.random.default_rng(seed)
        stats = {"mean_nm": [], "std_nm": [], "lam": [], "k_per_us": [], "t0_us": []}
        Ps = []
        for _ in range(int(n_boot)):
            vb = vfit + rng.choice(resid_v, size=resid_v.size, replace=True)
            rb = fit_deer(t, vb * scale, r_nm=r, method=method, n_gauss=n_gauss, background=background,
                          fit_t0=fit_t0, t0_us=t0, n_boot=0, alpha=alpha_used,
                          check_long=False, check_background=False, dim=dim_fixed)
            for kk in stats:
                stats[kk].append(getattr(rb, kk))
            Ps.append(rb.P)
        ci = {kk: (float(np.percentile(vv, 2.5)), float(np.percentile(vv, 97.5))) for kk, vv in stats.items()}
        Ps = np.array(Ps)
        P_ci = (np.percentile(Ps, 2.5, axis=0), np.percentile(Ps, 97.5, axis=0))

    return DeerResult(method="tikhonov" if method.startswith("tik") else f"{n_gauss} Gaussian(s)",
                      background=background, r_nm=r, P=P, mean_nm=mean, std_nm=std, modal_nm=modal,
                      populations=pops, lam=lam, k_per_us=k, conc_uM=conc, dim=dim, t0_us=t0,
                      alpha=alpha_used, criterion=crit, t_us=t, V=v, V_fit=vfit,
                      residual_rms=noise, snr=float(snr), phase_deg=phase, limits=lim, warnings=warns,
                      gauss=gpar, ci=ci, P_ci95=P_ci, aic=aic_val,
                      main_nm=float(max(main_pops, key=lambda p_: p_["fraction"])["r_nm"]) if main_pops else modal,
                      reliable_populations=main_pops, long_fraction=long_frac)


def _populations(r, P, min_fraction: float = 0.05) -> list:
    """Split P at its minima into peaks; keep those holding at least
    min_fraction of the area: [{"r_nm": mode, "fraction": area, "std_nm"}]."""
    from scipy.signal import find_peaks
    dr = np.gradient(r)
    if P.max() <= 0:
        return []
    # a population needs a real dip between it and its neighbour (prominence
    # 20% of the maximum), not a ripple of the regularised solution
    pk, _ = find_peaks(P, prominence=0.2 * P.max())
    if pk.size == 0:
        pk = np.array([int(np.argmax(P))])
    bounds = [0]
    for a, b in zip(pk[:-1], pk[1:]):
        bounds.append(a + int(np.argmin(P[a:b + 1])))
    bounds.append(len(r))
    out = []
    for i, p in enumerate(pk):
        s = slice(bounds[i], bounds[i + 1])
        w = float(np.sum(P[s] * dr[s]))
        if w >= min_fraction:
            m = float(np.sum(r[s] * P[s] * dr[s]) / w)
            sd = float(math.sqrt(max(0.0, np.sum((r[s] - m) ** 2 * P[s] * dr[s]) / w)))
            out.append({"r_nm": float(r[p]), "mean_nm": m, "fraction": w, "std_nm": sd})
    tot = sum(o["fraction"] for o in out) or 1.0
    for o in out:
        o["fraction"] /= tot
    return out


# ==============================================================================
# Powder simulation, fitting, units and isotopes (labkit/epr_sim.py)
# ==============================================================================

from .epr_sim import (Isotope, Nucleus, SpectrumFit, SpinSystem, convert, fit_spectrum,  # noqa: E402
                      isotope, known_isotopes, simulate_powder)
