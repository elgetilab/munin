"""labkit tests. Standalone (the sandbox image has no pytest); run inside the
sandbox image so the library versions are the ones kernels get:

    backend/scripts/ci/run_sandbox_live_tests.sh      (CI: builds munin-sandbox:ci-live)

Independent of the eval cases by design (docs/agent-track/LAB-PHASE3-PLAN.md):
nothing here reads fixtures/lab/cases or the case generators. The NIST StRD
certificates are read as published data (NIST_RAW, default: the copy under
backend/benchmarks/fixtures/lab/nist/raw).
"""
from __future__ import annotations

import io
import math
import os
import sys
import tempfile
import traceback
import warnings
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))
warnings.simplefilter("ignore")

import labkit  # noqa: E402
from labkit import fit, fit_global, compare, rout, interpolate  # noqa: E402

def _default_nist() -> Path:
    try:
        return HERE.parents[2] / "benchmarks" / "fixtures" / "lab" / "nist" / "raw"
    except IndexError:  # copied into a container without the repo around it
        return HERE / "nist_raw"


NIST_RAW = Path(os.environ.get("NIST_RAW") or _default_nist())
TMP = Path(tempfile.mkdtemp(prefix="labkit-test-"))
RESULTS: list[tuple[str, bool, str]] = []


def check(name, ok, detail=""):
    RESULTS.append((name, bool(ok), detail))
    shown = "" if ok or detail is None or (isinstance(detail, str) and not detail) else f" - {detail}"
    print(f"[{'PASS' if ok else 'FAIL'}] {name}{shown}")
    return ok


def close(a, b, rtol):
    return abs(a - b) <= rtol * max(abs(b), 1e-300)


# ==============================================================================
# fit: NIST StRD
# ==============================================================================

def test_nist():
    from nist_models import MODELS, parse
    if not NIST_RAW.exists():
        return check("NIST certificates present", False, str(NIST_RAW))
    bad = []
    for name, f in MODELS.items():
        d = parse(NIST_RAW / f"{name}.dat")
        a = d["data"]
        y = a[:, 0]
        x = (a[:, 1], a[:, 2]) if name == "Nelson" else a[:, 1]
        if name == "Nelson":
            y = np.log(y)
        for s in ("start1", "start2"):
            r = fit(f, x, y, p0=d[s])
            got = [r.params[k] for k in r.fitted]
            for g, c in zip(got, d["cert"]):
                if not close(g, c, 1e-6):
                    bad.append(f"{name}/{s} param {g} vs {c}")
            se_tol = 1e-2 if name == "Lanczos1" else 1e-4   # Lanczos1: noise-free, SEs ~1e-12
            for k, c in zip(r.fitted, d["sd"]):
                if not close(r.se[k], c, se_tol):
                    bad.append(f"{name}/{s} se {r.se[k]} vs {c}")
            if not close(r.rss, d["rss"], 1e-5 if name != "Lanczos1" else 1e-1):
                bad.append(f"{name}/{s} rss {r.rss} vs {d['rss']}")
    check("NIST StRD: 27 problems x 2 starts, parameters 1e-6, SEs 1e-4, RSS", not bad, bad[:5])


# ==============================================================================
# fit: model library and conventions
# ==============================================================================

RNG = np.random.default_rng(7)


def test_decay_names_and_derived():
    t = np.linspace(0, 20, 16)
    y = (1000 - 100) * np.exp(-0.3 * t) + 100
    reps = np.column_stack([y + RNG.normal(0, 5, t.size) for _ in range(3)])
    r = fit("one_phase_decay", t, reps)
    ok = (close(r.params["Y0"], 1000, 0.01) and close(r.params["Plateau"], 100, 0.05)
          and close(r.params["Span"], r.params["Y0"] - r.params["Plateau"], 1e-12)
          and close(r.params["HalfLife"], math.log(2) / r.params["K"], 1e-12)
          and r.n == 48 and "VALUE" in r.definitions["Y0"])
    lo, hi = r.ci["HalfLife"]
    klo, khi = r.ci["K"]
    ok = ok and close(lo, math.log(2) / khi, 1e-9) and close(hi, math.log(2) / klo, 1e-9)
    check("one_phase_decay: Prism names, Y0 is the value, Span/HalfLife derived, all replicates fitted", ok, str(r))


def test_replicates_mean_vs_all():
    x = np.arange(8.0)
    reps = np.column_stack([2 * x + 1 + RNG.normal(0, 0.3, 8) for _ in range(3)])
    reps[2, 1] = np.nan
    a = fit("linear", x, reps)
    m = fit("linear", x, reps, replicates="mean")
    check("replicates: 'all' uses every point (NaN skipped), 'mean' the row means", a.n == 23 and m.n == 8, (a.n, m.n))


def test_linear_matches_linregress():
    from scipy import stats as sps
    x = np.linspace(0, 10, 12)
    y = 3 * x - 2 + RNG.normal(0, 0.5, 12)
    r = fit("linear", x, y)
    lr = sps.linregress(x, y)
    rp = fit("linear", x, y, ci="profile")
    ok = (close(r.params["Slope"], lr.slope, 1e-8) and close(r.se["Slope"], lr.stderr, 1e-6)
          and close(r.se["YIntercept"], lr.intercept_stderr, 1e-6)
          and all(close(rp.ci[k][i], r.ci[k][i], 1e-4) for k in ("Slope", "YIntercept") for i in (0, 1)))
    check("linear: estimates and SEs equal scipy linregress; profile CI equals Wald for a linear model", ok)


def test_dose_response_requires_x_scale():
    conc = np.array([1e-9, 1e-8, 1e-7, 1e-6, 1e-5, 1e-4])
    y = 5 + 95 / (1 + 10 ** ((-7 - np.log10(conc)) * 1.0))
    try:
        fit("4pl", conc, y)
        raised = False
    except ValueError as e:
        raised = "x_scale" in str(e)
    a = fit("4pl", conc, y, x_scale="linear")
    b = fit("4pl", np.log10(conc), y, x_scale="log")
    try:
        fit("4pl", np.r_[0, conc], np.r_[5, y], x_scale="linear")
        zero = False
    except ValueError:
        zero = True
    lo, hi = a.ci["EC50"]
    ok = (raised and zero and close(a.params["LogEC50"], -7, 1e-6) and close(b.params["LogEC50"], -7, 1e-6)
          and close(a.params["EC50"], 1e-7, 1e-5) and lo <= a.params["EC50"] <= hi)
    check("4pl: x_scale required; linear/log agree; EC50 = 10^LogEC50 with transformed CI; zero dose raises", ok)


def test_x_scale_guard():
    """x_scale='log' with raw concentrations raises (LABKIT-011-PLAN.md): in
    Phase 4 run 2 the model read 'log' as "fit on a log axis" and got Hill
    -3.1 and Bottom -49419 without an error."""
    def raises(f):
        try:
            f()
        except ValueError as e:
            return "x_scale='linear'" in str(e)
        return False

    def y_of(logx):
        return 5 + 95 / (1 + 10 ** ((-1 - logx) * 1.2))
    three = 100 / 3.0 ** np.arange(10)                       # 3x dilution, uM
    halflog = 10 ** np.arange(-9, -4.4, 0.5)                 # half-log series, M
    two_reps = np.repeat(50 / 2.0 ** np.arange(8), 3)        # 2x dilution in triplicate
    pos_log = np.linspace(0.1, 2.0, 9)                       # log10 values, all positive
    neg_log = np.log10(three)
    ok_raise = (raises(lambda: fit("4pl", three, y_of(np.log10(three)), x_scale="log"))
                and raises(lambda: fit("3pl", halflog, y_of(np.log10(halflog) + 6), x_scale="log"))
                and raises(lambda: fit("5pl", two_reps, y_of(np.log10(two_reps)), x_scale="log"))
                and raises(lambda: rout("4pl", three, y_of(np.log10(three)), x_scale="log"))
                and raises(lambda: fit_global("4pl", [(three, y_of(np.log10(three)))] * 2,
                                              shared=["HillSlope"], x_scale="log")))
    a = fit("4pl", pos_log, y_of(pos_log), x_scale="log")
    b = fit("4pl", neg_log, y_of(neg_log), x_scale="log")
    c = fit("4pl", three, y_of(np.log10(three)), x_scale="linear")
    rng = np.random.default_rng(3)
    spread = np.sort(10 ** rng.uniform(-1, 3.5, 12))         # positive, 4.5 decades, no series
    w = fit("4pl", spread, y_of(np.log10(spread)), x_scale="log")
    ok = (ok_raise and not any("x_scale" in m for m in a.warnings + b.warnings + c.warnings)
          and close(c.params["LogEC50"], -1, 1e-5)
          and any("3 decades" in m for m in w.warnings))
    check("x_scale='log' on a dilution series raises (fit, rout, fit_global); log10 input and "
          "x_scale='linear' pass; an unclear wide positive range only warns", ok)


def test_5pl_reports_logec50_next_to_logxb():
    x = np.linspace(-3, 3, 13)
    y = 2 + 90 / (1 + 10 ** ((0.2 - x) * 1.1)) ** 0.5
    r = fit("5pl", x, y, x_scale="log")
    rows = [ln.split()[0] for ln in str(r).splitlines()[2:] if ln.startswith("  ") and not ln.startswith("  WARNING")]
    i = rows.index("LogXb")
    text = str(r)
    check("5pl: print(r) shows LogEC50 right after LogXb, marked as the value to report",
          rows[i + 1] == "LogEC50" and "report THIS as logEC50" in text, rows)


def test_derived_values_match_the_curve():
    """Every derived quantity checked against the fitted curve itself, by a
    numerical root, never against its own formula: labkit 0.1.1's 5PL
    LogEC50 had a sign error that a formula-copy test passed."""
    from scipy.optimize import brentq
    ok, bad = True, []

    def halfway(r, lo_y, hi_y, a, b):
        return brentq(lambda v: float(r.predict(np.array([v]))[0]) - (lo_y + hi_y) / 2, a, b)

    for S, H in ((0.5, 1.1), (2.5, 0.8), (1.0, 1.3), (0.4, -1.2)):
        x = np.linspace(-4, 4, 33)
        y = 2 + 90 / (1 + 10 ** ((0.3 - x) * H)) ** S
        r = fit("5pl", x, y, x_scale="log")
        root = halfway(r, r.params["Bottom"], r.params["Top"], -6, 6)
        if not close(r.params["LogEC50"], root, 1e-6) or not close(r.params["EC50"], 10 ** root, 1e-5):
            ok = False; bad.append(("5pl", S, H, r.params["LogEC50"], root))
    x = np.linspace(-3, 3, 25)
    r4 = fit("4pl", x, 5 + 95 / (1 + 10 ** ((-0.7 - x) * 1.4)), x_scale="log")
    if not close(r4.params["LogEC50"], halfway(r4, r4.params["Bottom"], r4.params["Top"], -3, 3), 1e-6):
        ok = False; bad.append("4pl")
    t = np.linspace(0, 20, 30)
    rd = fit("one_phase_decay", t, 130 + 700 * np.exp(-0.2 * t))
    if not close(rd.params["HalfLife"], halfway(rd, rd.params["Plateau"], rd.params["Y0"], 0, 20), 1e-6) \
            or not close(rd.params["Span"], float(rd.predict(np.array([0.0]))[0]) - rd.params["Plateau"], 1e-9):
        ok = False; bad.append("decay1")
    ra = fit("one_phase_association", t, 20 + 300 * (1 - np.exp(-0.3 * t)))
    if not close(ra.params["HalfLife"], halfway(ra, ra.params["Y0"], ra.params["Plateau"], 0, 20), 1e-6):
        ok = False; bad.append("assoc1")
    rl = fit("linear", t, 3.0 * t - 12.0)
    if not close(rl.params["XIntercept"], brentq(lambda v: float(rl.predict(np.array([v]))[0]), -50, 50), 1e-9):
        ok = False; bad.append("linear")
    check("derived values (5PL LogEC50 for S above and below 1 and both slope signs, 4PL LogEC50, "
          "decay and association half-life and span, linear X intercept) equal numerical roots of the curve",
          ok, bad)


def test_ambiguous_fit_warns():
    x = np.linspace(-9, -7.5, 8)           # only the bottom of the curve
    y = 10 + 90 / (1 + 10 ** ((-5 - x) * 1.0)) + RNG.normal(0, 0.5, 8)
    r = fit("4pl", x, y, x_scale="log")
    w = " ".join(r.warnings)
    check("4pl on one plateau only: warns that parameters are not determined",
          any(k in w for k in ("dependency", "not determined", "singular", "constraint")), r.warnings)


def test_weights_relative():
    from scipy.optimize import curve_fit
    x = np.linspace(0.5, 20, 15)
    y = 50 * x / (4 + x) * (1 + RNG.normal(0, 0.05, 15))
    r = fit("michaelis_menten", x, y, weights="1/y^2")
    p, cov = curve_fit(lambda s, v, k: v * s / (k + s), x, y, p0=[50, 4], sigma=y, absolute_sigma=False)
    ok = close(r.params["Vmax"], p[0], 1e-6) and close(r.se["Km"], math.sqrt(cov[1, 1]), 1e-4)
    check("weights='1/y^2' equals curve_fit(sigma=y): estimates and SEs", ok, (r.params, p))


def test_two_phase_and_mm_and_competitive_and_morrison():
    t = np.linspace(0, 30, 40)
    y = 20 + 600 * np.exp(-1.2 * t) + 300 * np.exp(-0.08 * t)
    r2 = fit("two_phase_decay", t, y + RNG.normal(0, 1, t.size))
    ok2 = (close(r2.params["KFast"], 1.2, 0.05) and close(r2.params["KSlow"], 0.08, 0.05)
           and close(r2.params["SpanFast"], 600, 0.05) and close(r2.params["Plateau"], 20, 0.2))
    S = np.tile([1, 2, 5, 10, 20, 50.0], 3)
    I = np.repeat([0, 5, 20.0], 6)
    v = 100 * S / (8 * (1 + I / 4) + S)
    rc = fit("competitive_inhibition", (S, I), v * (1 + RNG.normal(0, 0.01, v.size)))
    okc = close(rc.params["Ki"], 4, 0.05) and close(rc.params["Km"], 8, 0.05)
    xi = np.array([0, 1, 2, 4, 8, 16, 32, 64.0])
    from labkit.fit import _f_morrison
    vi = _f_morrison(xi, 100, 3, 5)
    try:
        fit("morrison", xi, vi)
        need_et = False
    except ValueError as e:
        need_et = "Et" in str(e)
    rm = fit("morrison", xi, vi + RNG.normal(0, 0.3, xi.size), constants={"Et": 5})
    okm = need_et and close(rm.params["KiApp"], 3, 0.1)
    check("two_phase_decay, competitive_inhibition (x=(S, I)), morrison (Et required) recover parameters",
          ok2 and okc and okm, (r2.params, rc.params, rm.params))


def test_compare_and_aicc():
    x = np.linspace(0, 10, 30)
    y = 2 * x + 1 + 0.5 * x ** 2 + RNG.normal(0, 0.5, 30)
    lin = fit("linear", x, y)
    quad = fit(lambda x, a, b, c: a * x * x + b * x + c, x, y, p0=[0, 1, 0])
    c = compare(lin, quad)
    check("compare: F test and AICc prefer the true (quadratic) model", c["p"] < 1e-6 and c["aicc_prefers"] != "linear", c)


def test_rout_false_positive_rate():
    rng = np.random.default_rng(11)
    flagged = 0
    for _ in range(200):
        x = np.linspace(0, 10, 20)
        if rout("linear", x, 3 * x + 2 + rng.normal(0, 0.3, 20)).outliers:
            flagged += 1
    check("rout: Q=1% flags at most a few percent of clean datasets (200 simulated)", flagged / 200 <= 0.04,
          flagged / 200)


def test_rout():
    x = np.linspace(0, 10, 20)
    y = 3 * x + 2 + RNG.normal(0, 0.3, 20)
    clean = rout("linear", x, y)
    y2 = y.copy()
    y2[7] += 15
    dirty = rout("linear", x, y2)
    ok = clean.outliers == [] and dirty.outliers == [7] and close(dirty.params["Slope"], 3, 0.03)
    check("rout: no false outlier on clean data; finds a gross outlier and refits without it", ok,
          (clean.outliers, dirty.outliers))


def test_interpolate():
    conc = np.array([0, 5, 10, 20, 40, 80.0])
    reads = 0.05 + 0.01 * conc
    std = fit("linear", conc, np.column_stack([reads, reads + 0.002]))
    out = interpolate(std, {"u1": [0.30, 0.32], "u2": 0.55, "hi": 2.0, "lo": -1.0})
    ok = (close(out["u1"], (0.31 - 0.051) / 0.01, 1e-6) and close(out["u2"], (0.55 - 0.051) / 0.01, 1e-6)
          and out["hi"] is None and out["lo"] is None and len(out["warnings"]) == 2)
    check("interpolate: replicate readings averaged; outside the standards -> None with a warning", ok, out)


def test_fit_global():
    x = np.linspace(0, 10, 15)
    d1 = 5 * x / (2 + x) + RNG.normal(0, 0.02, 15)
    d2 = 9 * x / (2 + x) + RNG.normal(0, 0.02, 15)
    g = fit_global("michaelis_menten", [(x, d1), (x, d2)], shared=["Km"])
    ok = close(g["shared"]["Km"], 2, 0.03) and close(g["per_dataset"][0]["Vmax"], 5, 0.03) \
        and close(g["per_dataset"][1]["Vmax"], 9, 0.03)
    check("fit_global: one shared Km, Vmax per dataset", ok, g["shared"])


def test_to_dict_is_json():
    import json
    r = fit("one_phase_decay", np.linspace(0, 10, 10), 50 * np.exp(-0.4 * np.linspace(0, 10, 10)) + 5)
    d = r.to_dict()
    json.dumps(d)
    check("to_dict: JSON-serialisable with name, name_se, name_ci", "Y0_ci" in d and "HalfLife" in d)


# ==============================================================================
# readers
# ==============================================================================

def _w(name, data: bytes) -> Path:
    p = TMP / name
    p.write_bytes(data)
    return p


def test_read_german_and_utf16():
    rows = "Sample Name\tNucleic Acid(ng/uL)\tA260/A280\r\ns1\t78,7\t1,91\r\ns2\t1.272,3\t1,86\r\ns3\t5,0\t2,01\r\n"
    de = labkit.read(_w("nd_de.tsv", rows.encode("cp1252")))
    us = rows.replace(",", ".").replace("1.272.3", "1272.3")
    u16 = labkit.read(_w("nd_u16.tsv", "﻿".encode("utf-16-le")[:0] + us.encode("utf-16")))
    ok = (list(de["conc_ng_ul"]) == [78.7, 1272.3, 5.0] and de.attrs["decimal"] == ","
          and list(u16["conc_ng_ul"]) == [78.7, 1272.3, 5.0] and u16.attrs["encoding"].startswith("utf-16"))
    check("read: NanoDrop German decimal comma + thousands dot, and UTF-16 with BOM", ok, (de.head(), u16.head()))


def test_read_preamble_table():
    text = "User: X\r\nTest Name: FI_485_520\r\nDate: 03/11/2026\r\n\r\nWell,Content,Raw Data (485/520)\r\n" \
           + "".join(f"A0{i},Sample {i},{1000 * i}\r\n" for i in range(1, 6))
    df = labkit.read(_w("bmg.csv", text.encode()))
    ok = df.attrs["meta"].get("Test Name") == "FI_485_520" and list(df["Raw Data (485/520)"]) == [1000, 2000, 3000, 4000, 5000]
    check("read: preamble lines become attrs['meta'], the table below is parsed", ok, (df.attrs["meta"], df.head()))


def test_read_plate_over():
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Application: plate reader"])
    ws.append(["Measurement Wavelength", None, None, None, 450, "nm"])
    ws.append(["<>"] + list(range(1, 13)))
    for i, r in enumerate("ABCDEFGH"):
        row = [r] + [round(0.1 + 0.01 * (i * 12 + c), 4) for c in range(12)]
        if r == "B":
            row[4] = "OVER"
        ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    df = labkit.read(_w("plate.xlsx", buf.getvalue()))
    b4 = df[df.well == "B4"]["value"].iloc[0]
    ok = len(df) == 96 and np.isnan(b4) and "B4" in df.attrs["warnings"][0] and "OVER" in df.attrs["warnings"][0] \
        and close(df[df.well == "A1"]["value"].iloc[0], 0.1, 1e-9)
    check("read_plate: 96 wells long format; OVER is NaN AND reported", ok, df.attrs["warnings"])


def test_read_gen5():
    head = "Software Version\t3.11\r\nReader Type:\tSynergy H1\r\n\r\nRead 1:405\r\n\r\n"
    tbl = "Time\tT° Read 1:405\tA1\tA2\r\n" + "".join(
        f"0:{m:02d}:{s:02d}\t30.0\t{0.1 + 0.01 * k:.4f}\t{0.2:.4f}\r\n"
        for k, (m, s) in enumerate([(0, 0), (0, 30), (1, 0), (1, 30)]))
    df = labkit.read(_w("k.txt", (head + tbl).encode()))
    ok = list(df["time_min"]) == [0, 0.5, 1.0, 1.5] and "temperature_C" in df.columns and df.attrs["format"] == "gen5"
    check("read_gen5: H:MM:SS to minutes, temperature column named, wells as columns", ok, df.head())


def test_read_jasco():
    head = "TITLE\tx\r\nORIGIN\tJASCO\r\nLOCALE\t1031\r\nXUNITS\tNANOMETERS\r\nYUNITS\tCD [mdeg]\r\nY2UNITS\tHT [V]\r\nXYDATA\r\n"
    rows = "".join(f"{w},0000\t{-w / 10:.4f}".replace(".", ",") + f"\t{200 + (260 - w) * 10},00\r\n"
                   for w in range(260, 189, -10))
    df = labkit.read(_w("cd.txt", (head + rows).encode()))
    ok = list(df.columns) == ["wavelength_nm", "cd_mdeg", "ht_v"] and close(df["cd_mdeg"].iloc[0], -26.0, 1e-9)
    check("read_jasco: German locale, columns named from the unit fields", ok, df.head())


def test_binary_refused():
    ole = _w("plate.sda", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + bytes(500))
    pcrd = _w("run.pcrd", bytes(range(256)) * 4)
    msgs = []
    for p in (ole, pcrd):
        try:
            labkit.read(p)
            msgs.append(None)
        except labkit.UnsupportedFormat as e:
            msgs.append(str(e))
    ok = all(m and "export" in m for m in msgs) and "SoftMax" in msgs[0] and "CFX" in msgs[1]
    check("read: proprietary binaries raise UnsupportedFormat with what to export", ok, msgs)


# ==============================================================================
# NMR
# ==============================================================================

def _synthetic_bruker(path: Path, shifts_ppm, amps, *, grpdly=76):
    import nmrglue as ng
    sfo1, sw_h, td = 600.13, 12019.2307692, 16384
    o1_ppm = 4.7
    o1 = o1_ppm * sfo1
    t = np.arange(td) / sw_h
    fid = np.zeros(td, complex)
    for s, a in zip(shifts_ppm, amps):
        f = (s - o1_ppm) * sfo1
        fid += a * np.exp(2j * np.pi * f * t) * np.exp(-t / 0.3)
    rec = np.concatenate([np.zeros(grpdly, complex), fid[:-grpdly]])   # the digital filter's delay
    rec = np.round(rec * 2e6)
    udic = {"ndim": 1, 0: {"size": td, "complex": True, "encoding": "direct", "sw": sw_h, "obs": sfo1,
                           "car": o1, "label": "1H", "time": True, "freq": False}}
    dic = ng.bruker.create_dic(udic)
    dic["acqus"].update({"SW": sw_h / sfo1, "SW_h": sw_h, "SFO1": sfo1, "BF1": sfo1, "O1": o1,
                         "DTYPA": 0, "BYTORDA": 0, "GRPDLY": float(grpdly), "DECIM": 1680, "DSPFVS": 20,
                         "TD": 2 * td, "NUC1": "<1H>", "PULPROG": "<zg30>"})
    ng.bruker.write(str(path), dic, rec.astype(np.complex128), overwrite=True)
    return path


def test_bruker_chain():
    from labkit import nmr
    shifts, amps = [0.0, 1.20, 3.35, 7.10], [1.0, 3.0, 2.0, 1.0]
    d = _synthetic_bruker(TMP / "exp10", shifts, amps)
    import zipfile
    z = TMP / "exp.zip"
    with zipfile.ZipFile(z, "w") as zz:
        for f in d.iterdir():
            zz.write(f, f"sample/10/{f.name}")
    raw = nmr.read_bruker(z)
    spec = nmr.process_1d(raw, phase=(0.0, 0.0))
    spec = nmr.reference(spec, ppm=0.0)
    found = [p["ppm"] for p in nmr.peaks(spec, threshold=0.2 * spec.real.max())]
    pos_ok = len(found) == 4 and all(abs(a - b) < 0.003 for a, b in zip(found, sorted(shifts)))
    ints = nmr.integrate(spec, [(s - 0.08, s + 0.08) for s in shifts], relative_to=3)
    rel = [r["relative"] for r in ints]
    int_ok = all(close(r, a / amps[3], 0.03) for r, a in zip(rel, amps))
    par_ok = raw.params["dtype"] == "int32" and close(raw.params["SW_ppm"] * raw.params["SFO1"], raw.params["SW_h"], 1e-6)
    try:
        nmr.process_1d(raw)
        must_choose = False
    except ValueError:
        must_choose = True
    check("bruker: zip read with int32 from DTYPA, digital filter removed, peaks at the right ppm, "
          "integrals in ratio, phasing must be chosen",
          pos_ok and int_ok and par_ok and must_choose, (found, rel, raw.params["dtype"]))


def test_csp_refuses_swapped_h_n():
    """(15N, 1H) tuples raise (LABKIT-011-PLAN.md): in Phase 4 round 3 the
    model passed Sparky's w1/w2 order and got CSPs 2-7x too large silently."""
    from labkit import nmr
    hn = {"G10": (8.0, 120.0), "A11": (8.2, 121.0), "K12": (7.9, 118.0), "L13": (8.5, 125.0)}
    hn_b = {k: (h + 0.02, n + 0.3) for k, (h, n) in hn.items()}
    nh, nh_b = ({k: (n, h) for k, (h, n) in d.items()} for d in (hn, hn_b))
    try:
        nmr.csp(nh, nh_b, alpha=0.14, scheme="euclidean")
        raised = False
    except ValueError as e:
        raised = "swap" in str(e)
    good = nmr.csp(hn, hn_b, alpha=0.14, scheme="euclidean")
    check("csp: (15N, 1H) order raises and says to swap; (1H, 15N) works",
          raised and close(good["G10"], math.sqrt(0.02 ** 2 + (0.14 * 0.3) ** 2), 1e-9))


def test_csp_and_kd_and_relaxation():
    from labkit import nmr
    free = {"G10": (8.0, 120.0), "A11": (8.2, 121.0), "K12": (7.9, 118.0)}
    bound = {"G10": (8.1, 121.0), "A11": None, "K12": (7.9, 118.5)}
    try:
        nmr.csp(free, bound, alpha=0.14, scheme="?")
        need = False
    except ValueError:
        need = True
    e = nmr.csp(free, bound, alpha=0.14, scheme="euclidean")
    w = nmr.csp(free, bound, alpha=0.14, scheme="williamson")
    ok_csp = need and e["A11"] is None and close(e["G10"], math.sqrt(0.01 + 0.14 ** 2), 1e-9) \
        and close(w["G10"], e["G10"] / math.sqrt(2), 1e-9)
    L = np.array([0, 25, 50, 100, 200, 400, 800, 1600.0])
    P = 100.0
    from labkit.nmr import _binding_isotherm
    data = {r: list(_binding_isotherm(L, m, 150.0, P) + RNG.normal(0, 0.0005, L.size)) for r, m in
            {"G10": 0.12, "K12": 0.3, "V20": 0.08}.items()}
    k = nmr.kd_fit(L, data, protein_conc=P)
    ok_kd = close(k["Kd"], 150, 0.1) and k["Kd_ci"][0] < 150 < k["Kd_ci"][1] and close(k["per_residue"]["K12"]["CSPmax"], 0.3, 0.05)
    tt = np.array([0.01, 0.05, 0.1, 0.2, 0.4, 0.8])
    rel = nmr.relaxation_fit(tt, {"G10": list(1e6 * np.exp(-1.6 * tt)), "A11": [1.0, None, None, None, None, None]})
    ok_rel = close(rel["G10"]["R"], 1.6, 1e-6) and rel["A11"] is None
    h = nmr.hetnoe({"G10": 0.8, "A11": 0.5}, {"G10": 1.0, "A11": None})
    ok_h = close(h["G10"]["hetNOE"], 0.8, 1e-12) and h["A11"] is None
    check("nmr: csp (alpha/scheme required, missing kept missing), global Kd with depletion, relaxation, hetNOE",
          ok_csp and ok_kd and ok_rel and ok_h, (e, w, k["Kd"], rel["G10"]))


# ==============================================================================
# stats, cd, plot, guide
# ==============================================================================

def test_field_errors_name_the_fields():
    from labkit import nmr
    spec = nmr.Spectrum(ppm=np.linspace(0, 10, 5), real=np.zeros(5))
    r = fit("linear", [1, 2, 3, 4], [2, 4, 6.1, 8])
    msgs = []
    for obj, attr in ((spec, "ppmm"), (spec, "ppmin"), (r, "param")):
        try:
            getattr(obj, attr)
            msgs.append(None)
        except AttributeError as e:
            msgs.append(str(e))
    import copy
    import pickle
    copy.deepcopy(spec)
    pickle.loads(pickle.dumps(spec))
    check("field errors: a wrong attribute names the closest field and lists them all; copy/pickle work",
          msgs[0] and "did you mean 'ppm'" in msgs[0] and "real" in msgs[0]
          and msgs[1] and "ppm" in msgs[1] and msgs[2] and "did you mean 'params'" in msgs[2], msgs)


def test_peaks_on_raised_baseline():
    """Up to 0.1.2 the default threshold ignored the baseline: a spectrum
    sitting above zero (as processed Bruker data often does) gave hundreds
    of noise peaks, and the ripples beside strong lines counted too."""
    from labkit import nmr
    rng = np.random.default_rng(3)
    ppm = np.linspace(14, -5, 32768)
    y = 1.3e7 + rng.normal(0, 3e4, ppm.size)
    for c, h in ((1.205, 1.5e9), (3.652, 1.0e9), (7.261, 0.5e9)):
        w = 0.002
        y += h * w ** 2 / ((ppm - c) ** 2 + w ** 2)
        # truncation ripples: 0.2% side lobes every 0.02 ppm
        for k in (1, 2, 3):
            y += 0.002 * h / k * np.exp(-((ppm - c - 0.02 * k) / 0.003) ** 2)
            y += 0.002 * h / k * np.exp(-((ppm - c + 0.02 * k) / 0.003) ** 2)
    pk = nmr.peaks(nmr.Spectrum(ppm=ppm, real=y))
    weak = nmr.peaks(nmr.Spectrum(ppm=ppm, real=y), min_rel_height=0)
    check("peaks: baseline-relative threshold, ripples dropped, 3 lines found, snr from the baseline",
          [round(p["ppm"], 2) for p in pk] == [1.2, 3.65, 7.26] and pk[0]["snr"] > 1000
          and len(weak) > 3, [(round(p["ppm"], 3), round(p["snr"])) for p in pk][:8])


def test_read_sparky_order_and_keys():
    from labkit import nmr
    free = TMP / "free.list"
    bound = TMP / "bound.list"
    hdr = "      Assignment         w1         w2   Data Height\n\n"
    free.write_text(hdr + "".join(f"{a:>16} {n:10.3f} {h:10.3f} {1000:13d}\n" for a, n, h in (
        ("G2N-H", 113.084, 8.793), ("K12N-H", 121.500, 7.900), ("W48NE1-HE1", 129.1, 10.10),
        ("?-?", 118.0, 8.0))))
    bound.write_text(hdr + "".join(f"{a:>16} {n:10.3f} {h:10.3f} {1000:13d}\n" for a, n, h in (
        ("G2N-H", 113.584, 8.843), ("K12N-H", 121.500, 7.900), ("W48NE1-HE1", 129.1, 10.10))))
    f = nmr.read_sparky(free)
    b = nmr.read_sparky(bound)
    c = nmr.csp(f, b, alpha=0.14, scheme="euclidean")
    want = math.sqrt(0.05 ** 2 + (0.14 * 0.5) ** 2)
    check("read_sparky: (H, N) order from the values, 'G2N-H' -> 'G2', side chains kept, ?-? skipped",
          f["G2"] == (8.793, 113.084) and "K12" in f and "W48NE1-HE1" in f and len(f) == 3
          and f.warnings and close(c["G2"], want, 1e-9) and c["K12"] == 0.0, (dict(f), f.warnings, c))


def test_read_integrals():
    from labkit import nmr
    p = TMP / "100ms.txt"
    p.write_bytes("Object\tIntegral [abs]\t\xb4(F2) [ppm]\t\xb4(F1) [ppm]\t\n"
                  "Integral 1\t225800.00\t8.6025\t0.8739\t\n"
                  "Integral 2\t34764000.00\t8.6037\t1.1962\t\n".encode("latin-1"))
    df = nmr.read_integrals(p)
    check("read_integrals: TopSpin 2D list in file order, columns integral/f2_ppm/f1_ppm",
          list(df.columns) == ["integral", "f2_ppm", "f1_ppm"] and df["integral"].tolist() == [225800.0, 34764000.0]
          and df["f1_ppm"].tolist() == [0.8739, 1.1962], df)


def test_noe_buildup():
    from labkit import nmr
    t = np.array([0.1, 0.2, 0.3, 0.5])
    a0 = 4e9
    truth = {"p1": (0.02, 0.4), "p2": (0.004, 1.5), "p3": (0.08, 0.25)}
    rng = np.random.default_rng(11)
    cross = {k: list((a0 / 2) * (1 - np.exp(-2 * s * t)) * np.exp(-t / T) * (1 + rng.normal(0, 0.002, t.size)))
             for k, (s, T) in truth.items()}
    # still linear at the last mixing time: T is undetermined but must stay positive
    cross["lin"] = list(a0 * 0.0013 * t)
    r = nmr.noe_buildup(t, cross, a0)
    ok = all(close(r[k]["sigma"], s, 0.03) for k, (s, T) in truth.items())
    lin = r["lin"]
    try:
        nmr.noe_buildup(t * 1000, cross, a0)
        ms = False
    except ValueError:
        ms = True
    check("noe_buildup: sigma recovered per pair, keyed as given; T > 0 with a warning when no decay "
          "is resolved; milliseconds refused",
          ok and list(r) == list(cross) and lin["T"] > 0 and close(lin["sigma"], 0.0013, 0.05)
          and any("undetermined" in w for w in lin["warnings"]) and ms,
          {k: (v["sigma"], v["T"]) for k, v in r.items()})


def test_stats():
    import pandas as pd
    from labkit import stats
    from statsmodels.stats.multicomp import pairwise_tukeyhsd
    rows = []
    for a in ("wt", "ko"):
        for b in ("veh", "drug"):
            mu = {"wt": 10, "ko": 14}[a] + {"veh": 0, "drug": 3}[b] + (2 if (a, b) == ("ko", "drug") else 0)
            n = 4 if (a, b) != ("ko", "veh") else 6
            rows += [{"g": a, "t": b, "y": mu + RNG.normal(0, 1)} for _ in range(n)]
    df = pd.DataFrame(rows)
    t = stats.anova2(df, "y", "g", "t")
    ok_a = set(t.index) == {"g", "t", "g:t", "Residual"} and t.loc["Residual", "df"] == len(df) - 4
    one = df[df.t == "veh"].assign(grp=lambda d: d.g)
    ph = stats.posthoc(one, "y", "grp")
    ref = pairwise_tukeyhsd(one["y"], one["grp"])
    ok_p = close(float(ph["p_adj"].iloc[0]), float(ref.pvalues[0]), 1e-3)
    ok_s = [stats.asterisks(p) for p in (0.2, 0.05, 0.01, 0.001, 0.0001, 0.00001)] == ["ns", "*", "**", "***", "****", "****"]
    check("stats: Type III two-way table, Tukey on the pooled error = statsmodels for one-way, asterisks",
          ok_a and ok_p and ok_s, (t, ph))


def test_cd():
    from labkit import cd
    mre = float(cd.mean_residue_ellipticity(-28.0612, conc_mg_ml=0.2, path_cm=0.1, mw_da=14300, n_residues=129))
    ok = close(mre, -28.0612 * (14300 / 128) / (10 * 0.2 * 0.1), 1e-12)
    w = np.arange(260, 189, -1.0)
    ht = np.where(w >= 200, 300.0, 700.0)
    ok = ok and cd.ht_cutoff(w, ht, max_v=600) == 200.0
    check("cd: MRE = mdeg*MRW/(10*c*l) with MRW = MW/(N-1); HT cutoff", ok, mre)


def test_plot():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from PIL import Image
    from labkit.plot import save_figure, jitter
    fig, ax = plt.subplots()
    ax.plot([0, 1], [0, 1])
    ax.set_title("A long title that would need room")
    p = TMP / "f.png"
    info = save_figure(fig, str(p), width_mm=85, height_mm=60, dpi=300)
    w, h = Image.open(p).size
    ok = (w, h) == (info["width_px"], info["height_px"]) == (1003, 708)   # 85 mm = 1003.9 px, truncated
    ok = ok and np.array_equal(jitter(1, 5), jitter(1, 5)) and not np.array_equal(jitter(1, 5), jitter(2, 5) - 1)
    check("plot: save_figure is exactly 85x60 mm at 300 dpi; jitter is reproducible", ok, (w, h, info))


def test_guide():
    import contextlib
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        for t in ("overview", "fit", "read", "nmr", "stats", "cd", "plot"):
            labkit.guide(t)
    text = buf.getvalue()
    check("guide: every topic prints and names its functions",
          all(k in text for k in ("x_scale", "read_bruker", "anova2", "mean_residue_ellipticity", "save_figure",
                                   "read_sparky", "noe_buildup", "read_integrals", "spec.ppm")))


TESTS = [v for k, v in list(globals().items()) if k.startswith("test_") and callable(v)]

if __name__ == "__main__":
    for t in TESTS:
        try:
            t()
        except Exception:
            traceback.print_exc()
            check(t.__name__, False, "raised")
    failed = [r for r in RESULTS if not r[1]]
    print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} passed")
    sys.exit(1 if failed else 0)
