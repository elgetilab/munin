"""S8 EPR: CW-EPR and DEER (pulse dipolar) analyses (docs/agent-track/EPR-PLAN.md).

Synthetic spectra and traces from known parameters, written in the formats
labs actually get: Bruker BES3T (.DSC descriptor + .DTA binary), Bruker
ESP (.par + .spc), and ASCII exports. Every truth comes from the generating
parameters, never from a fit, and nothing here imports labkit: this file is
the independent reference labkit.epr is tested against.

Each case targets one convention an analysis gets wrong (the trap in its
`tags`): units (G vs mT, ns vs us, nm vs Angstrom), the microwave frequency
(read it, do not assume 9.5 GHz), peak-to-peak width vs FWHM, single vs
double integration and the normalisation chain for spin counting, the
half-saturation power, DEER phasing, zero time, background division, and
the distance range a trace of a given length supports.

Constants are CODATA 2022.
"""

from __future__ import annotations

import io
import math
from pathlib import Path

import numpy as np
from scipy import special

from common import sig, write_case

H = 6.62607015e-34            # J s (exact)
MU_B = 9.2740100657e-24       # J/T
G_E = 2.00231930436092
MU_0 = 1.25663706127e-6       # N/A^2
N_A = 6.02214076e23
HBAR = H / (2 * math.pi)
MT_PER_GHZ = H * 1e9 / MU_B * 1e3          # B[mT] * g = MT_PER_GHZ * nu[GHz]  (71.4477...)


# Fresh variants (a tuning check, as fixtures/lab_fresh for S1): VARIANT > 0
# redraws each case's parameters from a separate generator and changes the
# noise seed; VARIANT 0 is the committed suite, byte for byte.
VARIANT = 0


def _rng(seed: int):
    return np.random.default_rng(seed + 1000 * VARIANT)


def _id(cid: str) -> str:
    return cid if not VARIANT else f"{cid}_f{VARIANT}"


def _p(case: str, defaults: dict, spread: dict) -> dict:
    """Parameters of a case: the defaults, or for a fresh variant each one in
    `spread` redrawn uniformly: ("rel", f) -> x (1 + f u), ("abs", d) -> x + d u,
    u in [-1, 1]."""
    out = dict(defaults)
    if not VARIANT:
        return out
    import zlib
    r = np.random.default_rng(zlib.crc32(case.encode()) + 7919 * VARIANT)
    for k, (kind, amt) in spread.items():
        u = float(r.uniform(-1, 1))
        out[k] = defaults[k] * (1 + amt * u) if kind == "rel" else defaults[k] + amt * u
    return out


def field_mT(g: float, nu_ghz: float) -> float:
    return MT_PER_GHZ * nu_ghz / g


# -- file writers ----------------------------------------------------------------

def bes3t(x: np.ndarray, data: np.ndarray, *, xunit: str, spl: dict, dsl: dict | None = None,
          y: np.ndarray | None = None, yunit: str = "", yname: str = "", big: bool = True,
          fmt: str = "D", title: str = "sample") -> tuple[bytes, bytes]:
    """(DSC, DTA) for a linear X axis and optional linear Y axis. `data` has
    shape (nx,) or (ny, nx); complex data are written interleaved re, im."""
    data = np.asarray(data)
    cplx = np.iscomplexobj(data)
    arr = np.atleast_2d(data)                    # (ny, nx); X varies fastest
    ny, nx = arr.shape
    dtype = {"D": "f8", "F": "f4", "I": "i4"}[fmt]
    dt = np.dtype((">" if big else "<") + dtype)
    if cplx:
        flat = np.empty(arr.size * 2)
        flat[0::2] = arr.real.ravel()
        flat[1::2] = arr.imag.ravel()
    else:
        flat = arr.real.ravel()
    dta = flat.astype(dt).tobytes()
    lines = ["#DESC	1.2 * DESCRIPTOR INFORMATION ***********************",
             "*",
             "*	Dataset Type and Format:",
             "*",
             f"DSRC	EXP",
             f"BSEQ	{'BIG' if big else 'LIT'}",
             f"IKKF	{'CPLX' if cplx else 'REAL'}",
             "XTYP	IDX",
             f"YTYP	{'IDX' if ny > 1 else 'NODATA'}",
             "ZTYP	NODATA",
             "*",
             "*	Item Formats:",
             "*",
             f"IRFMT	{fmt}"]
    if cplx:
        lines.append(f"IIFMT	{fmt}")
    lines += ["*", "*	Data Ranges and Resolutions:", "*",
              f"XPTS	{nx}",
              f"XMIN	{x[0]:.6f}",
              f"XWID	{x[-1] - x[0]:.6f}"]
    if ny > 1:
        lines += [f"YPTS	{ny}", f"YMIN	{y[0]:.6f}", f"YWID	{y[-1] - y[0]:.6f}"]
    lines += ["*", "*	Documentational Text:", "*",
              f"TITL	'{title}'",
              "IRNAM	'Intensity'",
              f"XNAM	'{'Field' if xunit == 'G' else 'Time'}'",
              f"XUNI	'{xunit}'"]
    if ny > 1:
        lines += [f"YNAM	'{yname}'", f"YUNI	'{yunit}'"]
    lines += ["*", "************************************************************", "*",
              "#SPL	1.2 * STANDARD PARAMETER LAYER", "*"]
    lines += [f"{k}	{v}" for k, v in spl.items()]
    if dsl:
        lines += ["*", "************************************************************", "*",
                  "#DSL	1.0 * DEVICE SPECIFIC LAYER", "*", ".DVC     acqStart, 1.0", "*"]
        lines += [f"{k}	{v}" for k, v in dsl.items()]
    lines += ["*", "************************************************************"]
    return ("\n".join(lines) + "\n").encode(), dta


def esp(field_g: np.ndarray, y: np.ndarray, par: dict) -> tuple[bytes, bytes]:
    """(par, spc) of a Bruker ESP/ECS file: int32 big-endian, field in G."""
    scale = 2.0e7 / np.max(np.abs(y))
    spc = np.round(y * scale).astype(">i4").tobytes()
    centre = (field_g[0] + field_g[-1]) / 2
    width = field_g[-1] - field_g[0]
    keys = {"JSS": 0, "ANZ": len(y), "RES": len(y), "HCF": f"{centre:.3f}", "HSW": f"{width:.3f}"}
    keys.update(par)
    return ("".join(f"{k} {v}\r\n" for k, v in keys.items())).encode("latin-1"), spc


def csv_text(header: list[str], cols: list[np.ndarray], fmts: list[str], sep: str = ",") -> bytes:
    out = io.StringIO()
    out.write(sep.join(header) + "\n")
    for row in zip(*cols):
        out.write(sep.join(f % v for f, v in zip(fmts, row)) + "\n")
    return out.getvalue().encode()


# -- CW line shapes ----------------------------------------------------------------
# All in field units of the caller. `area` is the integral of the absorption.

def lorentz_abs(b, b0, dbpp, area=1.0):
    gamma = math.sqrt(3) * dbpp / 2                  # HWHM; FWHM = sqrt(3) * dBpp
    return area / math.pi * gamma / ((b - b0) ** 2 + gamma ** 2)


def gauss_abs(b, b0, dbpp, area=1.0):
    s = dbpp / 2                                     # dBpp = 2 sigma
    return area / (s * math.sqrt(2 * math.pi)) * np.exp(-0.5 * ((b - b0) / s) ** 2)


def first_harmonic(absorption, b, modamp, n_theta: int = 400):
    """Exact first-harmonic signal for sinusoidal field modulation of
    peak-to-peak amplitude `modamp` (the Bruker ModAmp convention), scaled so
    that for small modamp it equals (modamp / 2) * dA/dB. Its double integral
    is exactly (modamp / 2) * area, overmodulated or not."""
    th = (np.arange(n_theta) + 0.5) * math.pi / n_theta
    acc = np.zeros_like(b, dtype=float)
    for t in th:
        acc += absorption(b + (modamp / 2) * math.cos(t)) * math.cos(t)
    return acc * (2 / n_theta)                       # (2/pi) * integral over theta in [0, pi]


def deriv(absorption, b, modamp):
    """Small-modulation first-derivative spectrum: (modamp / 2) dA/dB, analytic
    enough via a centred difference on the analytic absorption."""
    h = 1e-4 * (b[1] - b[0])
    return (modamp / 2) * (absorption(b + h) - absorption(b - h)) / (2 * h)


def nitroxide_abs(b, b0, a, widths, area, shape="lorentz", second_order=True):
    """14N nitroxide fast-motion triplet: mI = +1, 0, -1 from low to high field.
    widths: dBpp per mI. Second-order shift -(a^2 / 2 B0) [I(I+1) - mI^2]."""
    out = 0.0
    for mi in (+1, 0, -1):
        pos = b0 - mi * a
        if second_order:
            pos -= a * a / (2 * b0) * (2 - mi * mi)
        f = lorentz_abs if shape == "lorentz" else gauss_abs
        out = out + f(b, pos, widths[mi], area / 3)
    return out


# -- CW cases ------------------------------------------------------------------

def _noise(rng, y, snr):
    return rng.normal(0, np.max(np.abs(y)) / snr, y.shape)


def cw_g_bes3t(root: Path):
    rng = _rng(80001)
    q = _p("g_bes3t", {"nu": 9.452103, "g": 2.00580, "dbpp": 1.50},
           {"nu": ("rel", 0.004), "g": ("abs", 0.0025), "dbpp": ("rel", 0.3)})
    nu, g, dbpp = q["nu"], q["g"], q["dbpp"]   # GHz (MWFQ in Hz), -, G
    b0 = field_mT(g, nu) * 10               # G
    x = np.linspace(3320.0, 3420.0, 2048)    # G
    y = deriv(lambda bb: lorentz_abs(bb, b0, dbpp, 1e4), x, 0.5)
    y = y + _noise(rng, y, 150)
    dsc, dta = bes3t(x, y, xunit="G", title="radical_sample",
                     spl={"EXPT": "CW", "MWFQ": f"{nu * 1e9:.6e}", "MWPW": "2.000e-03",
                          "AVGS": 4, "RCAG": "60.00", "SPTP": "0.04096", "STMP": "295"},
                     dsl={"ModAmp": "0.500 G", "ModFreq": "100.00 kHz", "Attenuation": "20.0 dB"})
    write_case(root, _id("s8_cw_g_bes3t"), "S8", [{
        "prompt": "sample.DSC / sample.DTA are a Bruker Xepr CW-EPR spectrum (BES3T) of a radical "
                  "with a single line. Report its g-value (g) and the peak-to-peak linewidth of the "
                  "first-derivative line in mT (dBpp_mT).",
        "checks": [{"type": "numeric", "key": "g", "truth": "truth:g", "atol": 2e-4},
                   {"type": "numeric", "key": "dBpp_mT", "truth": "truth:dBpp_mT", "atol": 0.01}]}],
        files={"sample.DSC": dsc, "sample.DTA": dta},
        results_keys={"g": "", "dBpp_mT": "mT"},
        truth={"g": g, "dBpp_mT": dbpp / 10, "freq_GHz": nu, "B0_mT": b0 / 10},
        tags=["format:bes3t", "trap:units_G_mT", "trap:frequency_from_file"],
        provenance="synthetic Lorentzian derivative, s8.cw_g_bes3t")


def cw_dpph_calibration(root: Path):
    rng = _rng(80002)
    q = _p("dpph", {"nu": 9.761180, "g": 2.00320, "offset": 0.35},
           {"nu": ("rel", 0.003), "g": ("abs", 0.0012), "offset": ("abs", 0.2)})
    nu, g_sample, offset = round(q["nu"], 5), q["g"], q["offset"]   # offset in mT: the axis reads high
    g_dpph = 2.0036
    x = np.linspace(342.0, 352.0, 2000)    # mT as displayed
    true_b = x - offset
    ys = deriv(lambda bb: lorentz_abs(bb, field_mT(g_sample, nu), 0.20, 1.0), true_b, 0.05)
    yd = deriv(lambda bb: lorentz_abs(bb, field_mT(g_dpph, nu), 0.15, 1.0), true_b, 0.05)
    ys = ys + _noise(rng, ys, 120)
    yd = yd + _noise(rng, yd, 300)
    files = {
        "sample.csv": csv_text(["Field [mT]", "Intensity"], [x, ys], ["%.4f", "%.6e"]),
        "dpph.csv": csv_text(["Field [mT]", "Intensity"], [x, yd], ["%.4f", "%.6e"]),
    }
    write_case(root, _id("s8_cw_dpph_calibration"), "S8", [{
        "prompt": "sample.csv and dpph.csv are CW-EPR spectra (first derivative) recorded "
                  f"back to back at {nu:.5f} GHz on the same spectrometer. The DPPH standard has "
                  "g = 2.0036. Use it to calibrate the field axis and report the sample's g-value "
                  "(g) and the field offset of the spectrometer in mT (field_offset_mT, measured "
                  "minus true).",
        "checks": [{"type": "numeric", "key": "g", "truth": "truth:g", "atol": 1e-4},
                   {"type": "numeric", "key": "field_offset_mT", "truth": "truth:field_offset_mT",
                    "atol": 0.02}]}],
        files=files, results_keys={"g": "", "field_offset_mT": "mT"},
        truth={"g": g_sample, "field_offset_mT": offset},
        tags=["format:csv", "trap:field_calibration"],
        provenance="synthetic, s8.cw_dpph_calibration")


def cw_complex_le(root: Path):
    rng = _rng(80003)
    q = _p("complex", {"nu": 9.6402, "g": 2.0042, "dbpp": 0.84},
           {"nu": ("rel", 0.004), "g": ("abs", 0.002), "dbpp": ("rel", 0.3)})
    nu, g, dbpp = q["nu"], q["g"], q["dbpp"]  # dbpp in G
    b0 = field_mT(g, nu) * 10
    x = np.linspace(b0 - 40.3, b0 + 39.7, 1024)
    y = deriv(lambda bb: lorentz_abs(bb, b0, dbpp, 3e3), x, 0.2)
    re = y + _noise(rng, y, 200)
    im = rng.normal(0, np.max(np.abs(y)) / 200, y.shape)
    dsc, dta = bes3t(x, re + 1j * im, xunit="G", big=False, fmt="F", title="quadrature",
                     spl={"EXPT": "CW", "MWFQ": f"{nu * 1e9:.6e}", "MWPW": "1.000e-03",
                          "AVGS": 1, "RCAG": "50.00", "SPTP": "0.02048"},
                     dsl={"ModAmp": "0.200 G"})
    write_case(root, _id("s8_cw_complex"), "S8", [{
        "prompt": "spec.DSC / spec.DTA hold a CW-EPR spectrum recorded with both quadrature "
                  "channels. The line is Lorentzian. Report the peak-to-peak width of the "
                  "derivative line (dBpp_mT) and the full width at half maximum of the absorption "
                  "line (fwhm_mT), both in mT.",
        "checks": [{"type": "numeric", "key": "dBpp_mT", "truth": "truth:dBpp_mT", "rtol": 0.03},
                   {"type": "numeric", "key": "fwhm_mT", "truth": "truth:fwhm_mT", "rtol": 0.03}]}],
        files={"spec.DSC": dsc, "spec.DTA": dta}, results_keys={"dBpp_mT": "mT", "fwhm_mT": "mT"},
        truth={"dBpp_mT": dbpp / 10, "fwhm_mT": math.sqrt(3) * dbpp / 10},
        tags=["format:bes3t", "trap:complex_interleaved", "trap:little_endian", "trap:pp_vs_fwhm"],
        provenance="synthetic, s8.cw_complex_le")


def cw_gaussian_fwhm(root: Path):
    rng = _rng(80004)
    q = _p("gaussian", {"b0": 3368.42, "dbpp": 6.80}, {"b0": ("abs", 15.0), "dbpp": ("rel", 0.3)})
    b0, dbpp = q["b0"], q["dbpp"]             # G
    x = np.linspace(3300.0, 3440.0, 1401)
    y = deriv(lambda bb: gauss_abs(bb, b0, dbpp, 5e4), x, 1.0)
    y = y + _noise(rng, y, 180)
    # Xepr "Export ASCII": index, field, intensity, tab separated
    body = io.StringIO()
    body.write("Index\tField [G]\tIntensity []\n")
    for i, (xx, yy) in enumerate(zip(x, y), start=1):
        body.write(f"{i}\t{xx:.3f}\t{yy:.6e}\n")
    write_case(root, _id("s8_cw_gaussian_fwhm"), "S8", [{
        "prompt": "defect.txt is an ASCII export of a CW-EPR spectrum (first derivative) of an "
                  "inhomogeneously broadened (Gaussian) line. Report the full width at half "
                  "maximum of the absorption line in mT (fwhm_mT).",
        "checks": [{"type": "numeric", "key": "fwhm_mT", "truth": "truth:fwhm_mT", "rtol": 0.03}]}],
        files={"defect.txt": body.getvalue().encode()}, results_keys={"fwhm_mT": "mT"},
        truth={"fwhm_mT": math.sqrt(2 * math.log(2)) * dbpp / 10, "dBpp_mT": dbpp / 10},
        tags=["format:xepr_ascii", "trap:index_column", "trap:gaussian_factor"],
        provenance="synthetic, s8.cw_gaussian_fwhm")


def _nitroxide_spectrum(x_g, nu, g, a_mt, widths_mt, area, modamp_g):
    b0 = field_mT(g, nu) * 10
    a = a_mt * 10
    w = {k: v * 10 for k, v in widths_mt.items()}
    return deriv(lambda bb: nitroxide_abs(bb, b0, a, w, area), x_g, modamp_g)


def cw_spin_count(root: Path):
    rng = _rng(80005)
    nu = 9.4410
    widths = {+1: 0.130, 0: 0.120, -1: 0.150}      # mT
    x = np.linspace(3300.0, 3440.0, 4096)
    q = _p("spin_count", {"c": 37.5}, {"c": ("rel", 0.5)})
    c_std, c_sample = 100.0, q["c"]                # uM, same tube and volume
    # acquisition: (gain dB, power mW, modamp G, scans)
    acq_s = (60.0, 2.0, 1.0, 4)
    acq_t = (50.0, 0.5, 2.0, 1)

    def signal(c, acq):
        gain_db, p_mw, bm, n = acq
        scale = c * 10 ** (gain_db / 20) * math.sqrt(p_mw) * n   # Xepr sums scans (SctNorm false)
        return _nitroxide_spectrum(x, nu, 2.0058, 1.70, widths, scale, bm)

    ys = signal(c_sample, acq_s)
    yt = signal(c_std, acq_t)
    ys = ys + 0.004 * np.max(ys) + 0.003 * np.max(ys) * (x - x[0]) / (x[-1] - x[0]) + _noise(rng, ys, 2000)
    yt = yt - 0.002 * np.max(yt) + _noise(rng, yt, 3000)
    files = {}
    for name, yy, (gain_db, p_mw, bm, n) in (("sample", ys, acq_s), ("tempol", yt, acq_t)):
        dsc, dta = bes3t(x, yy, xunit="G", title=name,
                         spl={"EXPT": "CW", "MWFQ": f"{nu * 1e9:.6e}", "MWPW": f"{p_mw / 1000:.4e}",
                              "AVGS": n, "RCAG": f"{gain_db:.2f}", "SPTP": "0.04096", "STMP": "295"},
                         dsl={"ModAmp": f"{bm:.3f} G", "SctNorm": "false", "QValue": "4800"})
        files[f"{name}.DSC"], files[f"{name}.DTA"] = dsc, dta
    write_case(root, _id("s8_cw_spin_count"), "S8", [{
        "prompt": "sample.DSC/.DTA is a CW-EPR spectrum of a spin-labelled protein; tempol.DSC/.DTA "
                  "is a 100 uM TEMPOL standard in the same tube type, volume and buffer, at the same "
                  "temperature. The acquisition parameters differ between the two (see the files). "
                  "Report the spin concentration of the sample in uM (concentration_uM).",
        "checks": [{"type": "numeric", "key": "concentration_uM", "truth": "truth:concentration_uM",
                    "rtol": 0.07}]}],
        files=files, results_keys={"concentration_uM": "uM"},
        truth={"concentration_uM": c_sample},
        tags=["format:bes3t", "trap:double_integral", "trap:baseline", "trap:gain_dB",
              "trap:sqrt_power", "trap:modamp_linear", "trap:scans"],
        provenance="synthetic, s8.cw_spin_count; signal = c * 10^(dB/20) * sqrt(P) * ModAmp * scans")


def cw_broad_window(root: Path):
    rng = _rng(80006)
    x = np.linspace(3100.0, 3600.0, 4001)          # G
    area_ratio = _p("broad", {"r": 2.40}, {"r": ("rel", 0.4)})["r"]   # broad / narrow
    yb = deriv(lambda bb: lorentz_abs(bb, 3345.0, 4.0, area_ratio * 1e4), x, 1.0)
    yn = deriv(lambda bb: gauss_abs(bb, 3349.0, 2.5, 1e4), x, 1.0)
    # Noise low enough that the double integral over the wide window is not
    # noise-limited: the case tests the window and baseline, not denoising.
    yb = yb + 0.01 * np.max(yb) + _noise(rng, yb, 5000)
    yn = yn + _noise(rng, yn, 8000)
    files = {
        "broad.csv": csv_text(["field_G", "signal"], [x, yb], ["%.2f", "%.6e"]),
        "narrow.csv": csv_text(["field_G", "signal"], [x, yn], ["%.2f", "%.6e"]),
    }
    write_case(root, _id("s8_cw_broad_window"), "S8", [{
        "prompt": "broad.csv (a broad Lorentzian line) and narrow.csv (a narrow reference line) are "
                  "first-derivative CW-EPR spectra recorded with identical settings. Both are S = 1/2 "
                  "with the same g. Report the ratio of spins, broad over narrow (spin_ratio).",
        "checks": [{"type": "numeric", "key": "spin_ratio", "truth": "truth:spin_ratio",
                    "rtol": 0.08}]}],
        files=files, results_keys={"spin_ratio": ""},
        truth={"spin_ratio": area_ratio},
        tags=["format:csv", "trap:integration_window", "trap:baseline"],
        provenance="synthetic, s8.cw_broad_window; the window spans about +-72 HWHM of the broad line; integrating +-5 HWHM loses 13%")


def cw_nitroxide(root: Path):
    rng = _rng(80007)
    q = _p("nitroxide", {"nu": 9.4987, "g": 2.00600, "a": 1.620},
           {"nu": ("rel", 0.004), "g": ("abs", 0.0006), "a": ("abs", 0.12)})
    nu, g, a = q["nu"], q["g"], q["a"]           # GHz, -, mT
    widths = {+1: 0.12 + 0.01 + 0.02, 0: 0.12, -1: 0.12 - 0.01 + 0.02}
    b0 = field_mT(g, nu) * 10
    x = np.linspace(b0 - 60, b0 + 60, 4096)
    y = _nitroxide_spectrum(x, nu, g, a, widths, 1e4, 0.5)
    y = y + _noise(rng, y, 300)
    dsc, dta = bes3t(x, y, xunit="G", title="sl_protein",
                     spl={"EXPT": "CW", "MWFQ": f"{nu * 1e9:.6e}", "MWPW": "2.000e-03", "AVGS": 9,
                          "RCAG": "60.00", "SPTP": "0.04096"},
                     dsl={"ModAmp": "0.500 G"})
    a_mhz = a * 1e-3 * g * MU_B / H / 1e6              # a[mT] -> MHz: a * 1e-3 T * g muB / h
    write_case(root, _id("s8_cw_nitroxide"), "S8", [{
        "prompt": "nitroxide.DSC/.DTA is the X-band CW-EPR spectrum of a nitroxide spin label in "
                  "fast motion (three 14N lines). Report the isotropic hyperfine coupling a_N in mT "
                  "(aN_mT) and in MHz (aN_MHz), and the isotropic g-value (g_iso).",
        "checks": [{"type": "numeric", "key": "aN_mT", "truth": "truth:aN_mT", "atol": 0.01},
                   {"type": "numeric", "key": "aN_MHz", "truth": "truth:aN_MHz", "atol": 0.3},
                   {"type": "numeric", "key": "g_iso", "truth": "truth:g_iso", "atol": 2e-4}]}],
        files={"nitroxide.DSC": dsc, "nitroxide.DTA": dta},
        results_keys={"aN_mT": "mT", "aN_MHz": "MHz", "g_iso": ""},
        truth={"aN_mT": a, "aN_MHz": a_mhz, "g_iso": g},
        tags=["format:bes3t", "trap:units_G_mT", "trap:mT_to_MHz", "trap:second_order"],
        provenance="synthetic triplet with second-order shifts, s8.cw_nitroxide")


def cw_kivelson(root: Path):
    rng = _rng(80008)
    nu, g, a = 9.4987, 2.00600, 1.600
    q = _p("kivelson", {"wp": 0.135, "w0": 0.120, "wm": 0.170},
           {"wp": ("rel", 0.08), "w0": ("rel", 0.08), "wm": ("rel", 0.15)})
    widths = {+1: q["wp"], 0: q["w0"], -1: q["wm"]}  # mT, Lorentzian dBpp
    b0 = field_mT(g, nu) * 10
    x = np.linspace(b0 - 60, b0 + 60, 8192)
    y = _nitroxide_spectrum(x, nu, g, a, widths, 1e4, 0.2)
    clean = y.copy()
    y = y + _noise(rng, y, 400)
    files = {"label.csv": csv_text(["B_G", "dI/dB"], [x, y], ["%.3f", "%.6e"])}
    # Truth: the formula applied to the written, noise-free spectrum's lines.
    def pp(lo, hi):
        m = (x > lo) & (x < hi)
        return float(np.max(clean[m]) - np.min(clean[m]))
    pos = {mi: b0 - mi * a * 10 - (a * 10) ** 2 / (2 * b0) * (2 - mi * mi) for mi in (1, 0, -1)}
    h = {mi: pp(pos[mi] - 8, pos[mi] + 8) for mi in pos}
    tau = 6.51e-10 * widths[0] * 10 * (math.sqrt(h[0] / h[1]) + math.sqrt(h[0] / h[-1]) - 2)
    write_case(root, _id("s8_cw_kivelson"), "S8", [{
        "prompt": "label.csv is the X-band CW-EPR spectrum (first derivative, field in G) of a "
                  "nitroxide in fast isotropic motion. Estimate the rotational correlation time "
                  "with the Kivelson formula tau_c = 6.51e-10 s/G * dB0 * (sqrt(h0/h+1) + "
                  "sqrt(h0/h-1) - 2), where dB0 is the peak-to-peak width of the centre line in G "
                  "and h-1, h0, h+1 are the peak-to-peak heights of the high-field, centre and "
                  "low-field lines (mI = -1, 0, +1). Report tau_c in seconds (tau_c_s).",
        "checks": [{"type": "numeric", "key": "tau_c_s", "truth": "truth:tau_c_s", "rtol": 0.10}]}],
        files=files, results_keys={"tau_c_s": "s"},
        truth={"tau_c_s": tau, "dB0_G": widths[0] * 10, "h0_over_hp1": h[0] / h[1],
               "h0_over_hm1": h[0] / h[-1]},
        tags=["format:csv", "trap:mI_order", "trap:units_G_mT"],
        provenance="synthetic Lorentzian triplet; truth = the formula on the noise-free spectrum")


def cw_power_saturation(root: Path):
    rng = _rng(80009)
    nu = 9.4500
    q = _p("psat", {"p_half": 12.0, "eps": 1.5}, {"p_half": ("rel", 0.5), "eps": ("abs", 0.4)})
    p_max, p_half, eps = 200.0, q["p_half"], min(q["eps"], 1.5)
    att = np.arange(0, 31, 2.0)                       # dB, 0 ... 30 (16 slices)
    p = p_max * 10 ** (-att / 10)
    amp = np.sqrt(p) * (1 + (2 ** (1 / eps) - 1) * p / p_half) ** (-eps)
    b0 = field_mT(2.0060, nu) * 10
    x = np.linspace(b0 - 15, b0 + 15, 600)
    line = deriv(lambda bb: lorentz_abs(bb, b0, 1.6, 1.0), x, 0.5)
    line = line / (np.max(line) - np.min(line))      # unit peak-to-peak
    data = np.array([ai * line for ai in amp]) * 1e4
    data = data + rng.normal(0, 0.004 * np.max(data), data.shape)
    dsc, dta = bes3t(x, data, y=att, yunit="dB", yname="Attenuation", xunit="G", title="psat",
                     spl={"EXPT": "CW", "MWFQ": f"{nu * 1e9:.6e}", "MWPW": f"{p_max / 1000:.4e}",
                          "AVGS": 1, "RCAG": "60.00"},
                     dsl={"ModAmp": "0.500 G", "PowerAt0dB": "200.0 mW"})
    write_case(root, _id("s8_cw_power_saturation"), "S8", [{
        "prompt": "psat.DSC/.DTA is a 2D CW-EPR power-saturation series of a spin label: the second "
                  "axis is the microwave attenuation in dB, with 200 mW at 0 dB. Fit the centre-line "
                  "peak-to-peak amplitude A(P) = I sqrt(P) [1 + (2^(1/eps) - 1) P/P_half]^(-eps) and "
                  "report the half-saturation power in mW (P_half_mW) and eps (epsilon).",
        "checks": [{"type": "numeric", "key": "P_half_mW", "truth": "truth:P_half_mW", "rtol": 0.10},
                   {"type": "numeric", "key": "epsilon", "truth": "truth:epsilon", "atol": 0.15}]}],
        files={"psat.DSC": dsc, "psat.DTA": dta}, results_keys={"P_half_mW": "mW", "epsilon": ""},
        truth={"P_half_mW": p_half, "epsilon": eps},
        tags=["format:bes3t_2d", "trap:dB_to_mW", "trap:reshape_order"],
        provenance="synthetic, s8.cw_power_saturation (Altenbach et al. 2005 form)")


def cw_overmodulation(root: Path):
    rng = _rng(80010)
    nu = 9.4500
    b0 = field_mT(2.0031, nu) * 10
    q = _p("overmod", {"dbpp": 1.50, "bm": 4.0}, {"dbpp": ("rel", 0.2), "bm": ("rel", 0.25)})
    dbpp, bm_big = q["dbpp"], round(q["bm"], 2)
    x = np.linspace(b0 - 80, b0 + 80, 4001)
    absf = lambda bb: lorentz_abs(bb, b0, dbpp, 1e4)
    files = {}
    for tag, bm in (("a", 0.5), ("b", bm_big)):
        y = first_harmonic(absf, x, bm)
        y = y + _noise(rng, y, 800)
        dsc, dta = bes3t(x, y, xunit="G", title=f"scan_{tag}",
                         spl={"EXPT": "CW", "MWFQ": f"{nu * 1e9:.6e}", "MWPW": "1.000e-03",
                              "AVGS": 1, "RCAG": "60.00"},
                         dsl={"ModAmp": f"{bm:.3f} G"})
        files[f"scan_{tag}.DSC"], files[f"scan_{tag}.DTA"] = dsc, dta
    write_case(root, _id("s8_cw_overmodulation"), "S8", [{
        "prompt": "scan_a and scan_b (Bruker BES3T) are CW-EPR spectra of the same sample, same "
                  "settings except the modulation amplitude. Is one of them overmodulated? Report the "
                  "modulation amplitude in G of the overmodulated scan (overmodulated_modamp_G, null "
                  "if neither), and the ratio of the double integrals after normalising each by its "
                  "modulation amplitude, b over a (di_ratio_normalised).",
        "checks": [{"type": "numeric", "key": "overmodulated_modamp_G", "truth": bm_big, "atol": 0.01},
                   {"type": "numeric", "key": "di_ratio_normalised", "truth": "truth:di_ratio",
                    "rtol": 0.05}]}],
        files=files, results_keys={"overmodulated_modamp_G": "G", "di_ratio_normalised": ""},
        truth={"di_ratio": 1.0, "dBpp_G": dbpp},
        tags=["format:bes3t", "trap:overmodulation", "trap:modamp_linear"],
        provenance="synthetic, exact first-harmonic modulation kernel, s8.cw_overmodulation")


def cw_esp(root: Path):
    rng = _rng(80011)
    nu = 9.7714                  # GHz
    q = _p("esp", {"g": 2.0210, "dbpp": 4.20}, {"g": ("abs", 0.01), "dbpp": ("rel", 0.3)})
    g, dbpp = q["g"], q["dbpp"]  # dbpp in G
    b0 = field_mT(g, nu) * 10
    x = np.linspace(3354.0, 3554.0, 1024)
    y = deriv(lambda bb: lorentz_abs(bb, b0, dbpp, 1e5), x, 1.0)
    y = y + _noise(rng, y, 120)
    par, spc = esp(x, y, {"MF": f"{nu:.4f}", "MP": "2.000e+00", "RMA": "1.000", "RRG": "2.00e+04",
                          "JSD": 4, "TE": 295, "JEX": "field-sweep", "JUN": "G"})
    write_case(root, _id("s8_cw_esp"), "S8", [{
        "prompt": "cu.par / cu.spc are an older Bruker ESP CW-EPR file of a single broad line. "
                  "Report the g-value at the zero crossing (g) and the peak-to-peak linewidth in mT "
                  "(dBpp_mT).",
        "checks": [{"type": "numeric", "key": "g", "truth": "truth:g", "atol": 3e-4},
                   {"type": "numeric", "key": "dBpp_mT", "truth": "truth:dBpp_mT", "atol": 0.02}]}],
        files={"cu.par": par, "cu.spc": spc}, results_keys={"g": "", "dBpp_mT": "mT"},
        truth={"g": g, "dBpp_mT": dbpp / 10},
        tags=["format:esp", "trap:int32_big_endian", "trap:hcf_hsw"],
        provenance="synthetic, s8.cw_esp")


# -- DEER ------------------------------------------------------------------------

NU_DD = MU_0 / (4 * math.pi) * (G_E * MU_B) ** 2 / H / 1e-27 / 1e6   # MHz nm^3, both g = g_e
# hom3d background: k [1/us] = K3D * lam * c[uM]
K3D = 8 * math.pi ** 2 / (9 * math.sqrt(3)) * MU_0 / (4 * math.pi) * (G_E * MU_B) ** 2 / HBAR \
    * N_A * 1e-3 / 1e6


def kernel(t_us: np.ndarray, r_nm: np.ndarray) -> np.ndarray:
    """K(t, r) = int_0^1 cos[(1 - 3 z^2) w t] dz, w = 2 pi nu_dd / r^3, closed
    form with Fresnel integrals (scipy returns (S, C))."""
    w = 2 * math.pi * NU_DD / r_nm[None, :] ** 3
    wt = w * np.abs(t_us)[:, None]
    out = np.ones_like(wt)
    m = wt > 1e-9
    x = np.sqrt(6 * wt[m] / math.pi)
    s, c = special.fresnel(x)
    out[m] = np.sqrt(math.pi / (6 * wt[m])) * (np.cos(wt[m]) * c + np.sin(wt[m]) * s)
    return out


def _kernel_selftest():
    t = np.array([0.0, 0.3, 1.1, 2.7]); r = np.array([2.0, 3.5, 5.0])
    z = np.linspace(0, 1, 200001)
    trap = getattr(np, "trapezoid", None) or np.trapz
    ref = np.array([[trap(np.cos((1 - 3 * z * z) * 2 * math.pi * NU_DD / rr ** 3 * tt), z)
                     for rr in r] for tt in t])
    assert np.max(np.abs(kernel(t, r) - ref)) < 1e-6, "Fresnel kernel disagrees with quadrature"


R_SIM = np.arange(1.5, 8.0001, 0.005)


def gauss_p(r, comps):
    """comps: [(weight, mean, std)]; normalised so sum P dr = 1."""
    p = sum(w * np.exp(-0.5 * ((r - m) / s) ** 2) / (s * math.sqrt(2 * math.pi)) for w, m, s in comps)
    return p / (np.sum(p) * (r[1] - r[0]))


def deer_trace(t_us, comps, lam, *, k=None, conc_uM=None, dim=3.0, t0=0.0, phase_deg=0.0,
               noise=0.0, rng=None, v0=1.0):
    p = gauss_p(R_SIM, comps)
    dr = R_SIM[1] - R_SIM[0]
    tt = t_us - t0
    form = (1 - lam) + lam * kernel(tt, R_SIM) @ p * dr
    if k is None:
        k = K3D * lam * conc_uM
    bg = np.exp(-(k * np.abs(tt)) ** (dim / 3))
    v = v0 * form * bg * np.exp(1j * math.radians(phase_deg))
    if noise:
        v = v + rng.normal(0, noise, v.shape) + 1j * rng.normal(0, noise, v.shape)
    return v, k


def _mix_stats(comps):
    w = np.array([c[0] for c in comps]); w = w / w.sum()
    m = np.array([c[1] for c in comps]); s = np.array([c[2] for c in comps])
    mean = float(np.sum(w * m))
    var = float(np.sum(w * (s ** 2 + m ** 2)) - mean ** 2)
    return mean, math.sqrt(var)


def _deer_case(root, cid, prompt, checks, files, keys, truth, tags, prov):
    write_case(root, cid, "S8", [{"prompt": prompt, "checks": checks}], files=files,
               results_keys=keys, truth=truth, tags=tags, provenance=prov)


def deer_ns_axis(root: Path):
    rng = _rng(80101)
    t = np.arange(0, 3000.1, 8.0)                       # ns
    q = _p("ns_axis", {"r": 3.50, "s": 0.30, "lam": 0.40, "c": 200.0},
           {"r": ("abs", 0.4), "s": ("rel", 0.25), "lam": ("abs", 0.06), "c": ("rel", 0.4)})
    comps = [(1.0, q["r"], q["s"])]
    v, k = deer_trace(t / 1000, comps, q["lam"], conc_uM=q["c"], noise=0.01, rng=rng)
    files = {"deer.csv": csv_text(["time_ns", "V"], [t, v.real], ["%.1f", "%.6f"])}
    _deer_case(root, _id("s8_deer_ns_axis"),
               "deer.csv is a background-containing 4-pulse DEER trace of a doubly spin-labelled "
               "protein in frozen solution (real part, already phased; time zero is the first "
               "point). Report the mean distance (mean_nm) and the standard deviation of the "
               "distance distribution (std_nm), both in nm, and the modulation depth (mod_depth).",
               [{"type": "numeric", "key": "mean_nm", "truth": "truth:mean_nm", "atol": 0.10},
                {"type": "numeric", "key": "std_nm", "truth": "truth:std_nm", "atol": 0.12},
                {"type": "numeric", "key": "mod_depth", "truth": "truth:mod_depth", "atol": 0.03}],
               files, {"mean_nm": "nm", "std_nm": "nm", "mod_depth": ""},
               {"mean_nm": q["r"], "std_nm": q["s"], "mod_depth": q["lam"], "k_per_us": k},
               ["format:csv", "trap:ns_to_us", "trap:background_division"],
               "synthetic, s8.deer_ns_axis; hom3d background, 200 uM")


def deer_angstrom(root: Path):
    rng = _rng(80102)
    t = np.arange(0, 2.5001, 0.008)
    q = _p("angstrom", {"r": 2.80, "lam": 0.35}, {"r": ("abs", 0.3), "lam": ("abs", 0.05)})
    v, k = deer_trace(t, [(1.0, q["r"], 0.20)], q["lam"], k=0.12, noise=0.008, rng=rng)
    files = {"trace.dat": csv_text(["# t/us", "V"], [t, v.real], ["%.4f", "%.6f"], sep=" ")}
    _deer_case(root, _id("s8_deer_angstrom"),
               "trace.dat (time in microseconds, phased real signal, zero time at the first point) "
               "is a 4-pulse DEER trace. Report the most probable distance in Angstrom (modal_A).",
               [{"type": "numeric", "key": "modal_A", "truth": "truth:modal_A", "atol": 1.0}],
               files, {"modal_A": "Angstrom"}, {"modal_A": 10 * q["r"], "mod_depth": q["lam"]},
               ["format:ascii", "trap:nm_to_angstrom", "trap:modal_vs_mean"],
               "synthetic, s8.deer_angstrom")


def deer_phase(root: Path):
    rng = _rng(80103)
    t = np.arange(0, 3.0001, 0.008)
    q = _p("phase", {"r": 3.20, "s": 0.25, "lam": 0.35, "ph": 37.0},
           {"r": ("abs", 0.4), "s": ("rel", 0.25), "lam": ("abs", 0.05), "ph": ("abs", 60.0)})
    v, k = deer_trace(t, [(1.0, q["r"], q["s"])], q["lam"], k=0.10, phase_deg=q["ph"], noise=0.008, rng=rng,
                      v0=1250.0)
    files = {"quad.csv": csv_text(["t_us", "real", "imag"], [t, v.real, v.imag],
                                  ["%.4f", "%.5f", "%.5f"])}
    _deer_case(root, _id("s8_deer_phase"),
               "quad.csv is a 4-pulse DEER trace as recorded, both quadrature channels (time zero at "
               "the first point). Report the mean distance (mean_nm), the standard deviation of the "
               "distance distribution (std_nm) and the modulation depth (mod_depth).",
               [{"type": "numeric", "key": "mean_nm", "truth": "truth:mean_nm", "atol": 0.10},
                {"type": "numeric", "key": "std_nm", "truth": "truth:std_nm", "atol": 0.12},
                {"type": "numeric", "key": "mod_depth", "truth": "truth:mod_depth", "atol": 0.03}],
               files, {"mean_nm": "nm", "std_nm": "nm", "mod_depth": ""},
               {"mean_nm": q["r"], "std_nm": q["s"], "mod_depth": q["lam"], "phase_deg": q["ph"]},
               ["format:csv", "trap:phase_correction", "trap:abs_value"],
               "synthetic, s8.deer_phase")


def deer_zero_time(root: Path):
    rng = _rng(80104)
    t = np.arange(0, 2.5001, 0.008)
    q = _p("zero_time", {"t0": 0.120, "r": 2.60}, {"t0": ("abs", 0.05), "r": ("abs", 0.3)})
    t0 = q["t0"]
    v, k = deer_trace(t, [(1.0, q["r"], 0.20)], 0.45, k=0.09, t0=t0, noise=0.008, rng=rng)
    files = {"deer_raw.csv": csv_text(["t_us", "V"], [t, v.real], ["%.4f", "%.6f"])}
    _deer_case(root, _id("s8_deer_zero_time"),
               "deer_raw.csv is a phased 4-pulse DEER trace; the time axis is as recorded by the "
               "spectrometer. Report the zero time in ns (t0_ns) and the mean distance in nm "
               "(mean_nm).",
               [{"type": "numeric", "key": "t0_ns", "truth": "truth:t0_ns", "atol": 16},
                {"type": "numeric", "key": "mean_nm", "truth": "truth:mean_nm", "atol": 0.08}],
               files, {"t0_ns": "ns", "mean_nm": "nm"}, {"t0_ns": 1000 * t0, "mean_nm": q["r"]},
               ["format:csv", "trap:zero_time"], "synthetic, s8.deer_zero_time")


def deer_bimodal(root: Path):
    rng = _rng(80105)
    t = np.arange(0, 5.0001, 0.008)
    q = _p("bimodal", {"r1": 3.00, "r2": 4.50, "f": 0.6}, {"r1": ("abs", 0.25), "r2": ("abs", 0.3), "f": ("abs", 0.15)})
    comps = [(q["f"], q["r1"], 0.25), (1 - q["f"], q["r2"], 0.35)]
    v, k = deer_trace(t, comps, 0.40, k=0.06, noise=0.005, rng=rng)
    files = {"bimodal.csv": csv_text(["t_us", "V"], [t, v.real], ["%.4f", "%.6f"])}
    mean, std = _mix_stats(comps)
    _deer_case(root, _id("s8_deer_bimodal"),
               "bimodal.csv is a phased 4-pulse DEER trace (time zero at the first point) of a "
               "protein that may populate more than one conformation. Report the distance of the "
               "shorter (r1_nm) and longer (r2_nm) population if there are two (null for r2_nm if "
               "only one), and the fraction of the shorter one (fraction1).",
               [{"type": "numeric", "key": "r1_nm", "truth": "truth:r1_nm", "atol": 0.15},
                {"type": "numeric", "key": "r2_nm", "truth": "truth:r2_nm", "atol": 0.15},
                {"type": "numeric", "key": "fraction1", "truth": "truth:fraction1", "atol": 0.10}],
               files, {"r1_nm": "nm", "r2_nm": "nm", "fraction1": ""},
               {"r1_nm": q["r1"], "r2_nm": q["r2"], "fraction1": q["f"], "mean_nm": mean, "std_nm": std},
               ["format:csv", "trap:single_gaussian", "trap:oversmoothing"],
               "synthetic, s8.deer_bimodal")


def deer_short_trace(root: Path):
    rng = _rng(80106)
    t = np.arange(0, 1.5001, 0.008)
    q = _p("short", {"r1": 3.00, "r2": 5.50}, {"r1": ("abs", 0.2), "r2": ("abs", 0.3)})
    comps = [(0.65, q["r1"], 0.20), (0.35, q["r2"], 0.40)]
    v, k = deer_trace(t, comps, 0.40, k=0.08, noise=0.006, rng=rng)
    files = {"short.csv": csv_text(["t_us", "V"], [t, v.real], ["%.4f", "%.6f"])}
    r_mean_max = (NU_DD * 1.5) ** (1 / 3)
    _deer_case(root, _id("s8_deer_short_trace"),
               "short.csv is a phased 4-pulse DEER trace (time zero at the first point). Report the "
               "distance distribution: the main distance in nm (r_main_nm) and anything a reader "
               "should know about the reliability of the result.",
               [{"type": "numeric", "key": "r_main_nm", "truth": "truth:r_main_nm", "atol": 0.12},
                {"type": "flagged", "any_of": ["too short", "trace length", "not reliable",
                                               "unreliable", "cannot be resolved", "beyond",
                                               "longer trace", "maximum reliable", "upper limit",
                                               "cannot be determined", "outside the reliable"]}],
               files, {"r_main_nm": "nm"},
               {"r_main_nm": q["r1"], "r_max_mean_nm": r_mean_max,
                "r_max_width_nm": (NU_DD * 1.5 / 2) ** (1 / 3)},
               ["format:csv", "trap:trace_length_limit"],
               "synthetic, s8.deer_short_trace; a 5.5 nm component is beyond what 1.5 us supports")


def deer_concentration(root: Path):
    rng = _rng(80107)
    t = np.arange(0, 4.0001, 0.008)
    q = _p("conc", {"c": 800.0, "r": 3.20}, {"c": ("rel", 0.4), "r": ("abs", 0.3)})
    v, k = deer_trace(t, [(1.0, q["r"], 0.30)], 0.50, conc_uM=q["c"], noise=0.006, rng=rng)
    files = {"conc.csv": csv_text(["t_us", "V"], [t, v.real], ["%.4f", "%.6f"])}
    _deer_case(root, _id("s8_deer_concentration"),
               "conc.csv is a phased 4-pulse DEER trace (time zero at the first point) of a "
               "doubly labelled protein in a homogeneous frozen solution. Estimate the spin "
               "concentration in uM from the intermolecular background (conc_uM) and report the "
               "mean distance in nm (mean_nm).",
               [{"type": "numeric", "key": "conc_uM", "truth": "truth:conc_uM", "rtol": 0.25},
                {"type": "numeric", "key": "mean_nm", "truth": "truth:mean_nm", "atol": 0.10}],
               files, {"conc_uM": "uM", "mean_nm": "nm"},
               {"conc_uM": q["c"], "mean_nm": q["r"], "k_per_us": k, "mod_depth": 0.50},
               ["format:csv", "trap:background_division", "trap:hom3d_constant"],
               "synthetic, s8.deer_concentration; k = 8 pi^2/(9 sqrt 3) D N_A lam c")


def deer_labeling(root: Path):
    rng = _rng(80108)
    t = np.arange(0, 3.5001, 0.008)
    q = _p("labeling", {"lam": 0.20, "r": 3.80}, {"lam": ("abs", 0.06), "r": ("abs", 0.3)})
    v, k = deer_trace(t, [(1.0, q["r"], 0.35)], q["lam"], k=0.07, noise=0.004, rng=rng)
    files = {"label.csv": csv_text(["t_us", "V"], [t, v.real], ["%.4f", "%.6f"])}
    _deer_case(root, _id("s8_deer_labeling"),
               "label.csv is a phased 4-pulse DEER trace (time zero at the first point). The pump "
               "pulse inverts 50% of the spins at its frequency (inversion efficiency 0.50). Report "
               "the modulation depth (mod_depth) and the labelling efficiency of the second site "
               "(labeling_efficiency).",
               [{"type": "numeric", "key": "mod_depth", "truth": "truth:mod_depth", "atol": 0.03},
                {"type": "numeric", "key": "labeling_efficiency", "truth": "truth:labeling_efficiency",
                 "atol": 0.07}],
               files, {"mod_depth": "", "labeling_efficiency": ""},
               {"mod_depth": q["lam"], "labeling_efficiency": q["lam"] / 0.5},
               ["format:csv", "trap:modulation_depth"],
               "synthetic, s8.deer_labeling; mod depth = inversion efficiency * labelling efficiency")


def deer_bes3t(root: Path):
    rng = _rng(80109)
    t_ns = np.arange(400) * 8.0
    q = _p("bes3t", {"r": 4.00, "s": 0.30, "lam": 0.38, "t0": 0.100, "ph": -20.0},
           {"r": ("abs", 0.4), "s": ("rel", 0.2), "lam": ("abs", 0.05), "t0": ("abs", 0.04), "ph": ("abs", 60.0)})
    scans = []
    for _ in range(8):
        v, k = deer_trace(t_ns / 1000, [(1.0, q["r"], q["s"])], q["lam"], k=0.07, t0=q["t0"],
                          phase_deg=q["ph"], noise=0.03, rng=rng)
        scans.append(v * 3.0e4)
    data = np.array(scans)
    dsc, dta = bes3t(t_ns, data, y=np.arange(1, 9, dtype=float), yunit="", yname="Scan",
                     xunit="ns", title="DEER 4p",
                     spl={"EXPT": "PLS", "MWFQ": "3.396210e+10", "AVGS": 8, "STMP": "50"},
                     dsl={"Pulse1": "16 ns", "Tau1": "400 ns", "Tau2": "3200 ns"})
    _deer_case(root, _id("s8_deer_bes3t"),
               "deer.DSC/.DTA is a Q-band 4-pulse DEER measurement from Xepr, saved scan by scan. "
               "Report the mean distance (mean_nm), the standard deviation of the distribution "
               "(std_nm), the modulation depth (mod_depth) and the zero time in ns (t0_ns).",
               [{"type": "numeric", "key": "mean_nm", "truth": "truth:mean_nm", "atol": 0.10},
                {"type": "numeric", "key": "std_nm", "truth": "truth:std_nm", "atol": 0.12},
                {"type": "numeric", "key": "mod_depth", "truth": "truth:mod_depth", "atol": 0.03},
                {"type": "numeric", "key": "t0_ns", "truth": "truth:t0_ns", "atol": 16}],
               {"deer.DSC": dsc, "deer.DTA": dta},
               {"mean_nm": "nm", "std_nm": "nm", "mod_depth": "", "t0_ns": "ns"},
               {"mean_nm": q["r"], "std_nm": q["s"], "mod_depth": q["lam"], "t0_ns": 1000 * q["t0"]},
               ["format:bes3t_2d", "trap:complex_interleaved", "trap:average_scans",
                "trap:phase_correction", "trap:zero_time"],
               "synthetic, s8.deer_bes3t")


def deer_membrane(root: Path):
    rng = _rng(80110)
    t = np.arange(0, 3.5001, 0.008)
    q = _p("membrane", {"r": 3.40, "k": 0.30}, {"r": ("abs", 0.3), "k": ("rel", 0.3)})
    v, k = deer_trace(t, [(1.0, q["r"], 0.25)], 0.35, k=q["k"], dim=2.0, noise=0.005, rng=rng)
    files = {"liposome.csv": csv_text(["t_us", "V"], [t, v.real], ["%.4f", "%.6f"])}
    _deer_case(root, _id("s8_deer_membrane"),
               "liposome.csv is a phased 4-pulse DEER trace (time zero at the first point) of a "
               "membrane protein reconstituted in liposomes. Report the mean distance (mean_nm) and "
               "the standard deviation of the distance distribution (std_nm).",
               [{"type": "numeric", "key": "mean_nm", "truth": "truth:mean_nm", "atol": 0.12}],
               files, {"mean_nm": "nm", "std_nm": "nm"},
               {"mean_nm": q["r"], "std_nm": 0.25, "background_dim": 2.0, "k_per_us": q["k"]},
               ["format:csv", "trap:membrane_background"],
               "synthetic, s8.deer_membrane; stretched background exp(-(k t)^(d/3)), d = 2")


def build(root: Path) -> None:
    _kernel_selftest()
    cw_g_bes3t(root)
    cw_dpph_calibration(root)
    cw_complex_le(root)
    cw_gaussian_fwhm(root)
    cw_spin_count(root)
    cw_broad_window(root)
    cw_nitroxide(root)
    cw_kivelson(root)
    cw_power_saturation(root)
    cw_overmodulation(root)
    cw_esp(root)
    deer_ns_axis(root)
    deer_angstrom(root)
    deer_phase(root)
    deer_zero_time(root)
    deer_bimodal(root)
    deer_short_trace(root)
    deer_concentration(root)
    deer_labeling(root)
    deer_bes3t(root)
    deer_membrane(root)
