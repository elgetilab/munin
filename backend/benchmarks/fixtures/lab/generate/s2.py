"""S2 instrument parsing: exports modelled on common lab instruments.

These are MIMICS written from the documented layouts (header blocks,
encodings, locales, plate grids), each built from a known truth. They test
reading and unit handling, not the vendor software. Real exports belong in
the held-out set (plan D5).
"""

from __future__ import annotations

import codecs
import io
import re
import zipfile
from datetime import datetime
from pathlib import Path

import numpy as np

from common import sig, write_case

ROWS = "ABCDEFGH"


def _normalize_zip(data: bytes) -> bytes:
    """Rewrite a zip with fixed timestamps and order, so xlsx and zip
    fixtures are byte-reproducible."""
    src = zipfile.ZipFile(io.BytesIO(data))
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as dst:
        for name in sorted(src.namelist()):
            info = zipfile.ZipInfo(name, date_time=(2026, 11, 3, 10, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            body = src.read(name)
            if name == "docProps/core.xml":   # openpyxl stamps the save time here
                body = re.sub(rb"(<dcterms:(?:created|modified)[^>]*>)[^<]*", rb"\g<1>2026-11-03T10:00:00Z", body)
            dst.writestr(info, body)
    return out.getvalue()


# -- NanoDrop ----------------------------------------------------------------

def _nanodrop_rows(rng):
    names = [f"pl{i:02d}_miniprep" for i in range(1, 9)]
    rows = []
    for i, n in enumerate(names):
        conc = float(rng.uniform(40, 600))
        r280 = float(rng.uniform(1.75, 1.95))
        r230 = float(rng.uniform(1.6, 2.3))
        a260 = conc / 50.0
        rows.append({"name": n, "conc": round(conc, 1), "a260": round(a260, 3),
                     "a280": round(a260 / r280, 3), "r280": round(r280, 2), "r230": round(r230, 2),
                     "time": f"{9 + i // 4:02d}:{(i * 7) % 60:02d}:{(i * 13) % 60:02d}"})
    return rows


def nanodrop(root: Path):
    rng = np.random.default_rng(20001)
    rows = _nanodrop_rows(rng)
    header = ["Sample Name", "Nucleic Acid(ng/uL)", "A260/A280", "A260/A230", "A260", "A280",
              "Sample Type", "Factor", "Date", "Time"]
    variants = {
        "utf8": dict(enc="utf8", dec=".", date="11/3/2026", nl="\n"),
        "utf16": dict(enc="utf16", dec=".", date="11/3/2026", nl="\r\n"),
        "dede": dict(enc="utf8", dec=",", date="03.11.2026", nl="\r\n"),
    }
    truth = {"concentration": {r["name"]: r["conc"] for r in rows},
             "ratio_260_280": {r["name"]: r["r280"] for r in rows}}
    for key, v in variants.items():
        lines = ["\t".join(header)]
        for r in rows:
            cells = [r["name"], f"{r['conc']:.1f}", f"{r['r280']:.2f}", f"{r['r230']:.2f}",
                     f"{r['a260']:.3f}", f"{r['a280']:.3f}", "DNA", "50.00", v["date"], r["time"]]
            if v["dec"] == ",":
                cells = [c.replace(".", ",") if i in (1, 2, 3, 4, 5, 7) else c for i, c in enumerate(cells)]
            lines.append("\t".join(cells))
        text = v["nl"].join(lines) + v["nl"]
        data = codecs.BOM_UTF16_LE + text.encode("utf-16-le") if v["enc"] == "utf16" else text.encode()
        fname = f"nanodrop_{key}.tsv"
        prompt = (f"{fname} is a NanoDrop export of plasmid minipreps. Report the nucleic acid "
                  "concentration in ng/µL and the A260/A280 ratio of every sample, keyed by sample name.")
        checks = [{"type": "numeric_map", "key": "concentration", "truth": "truth:concentration",
                   "rtol": 0.001, "atol": 0.05, "min_frac": 1.0},
                  {"type": "numeric_map", "key": "ratio_260_280", "truth": "truth:ratio_260_280",
                   "atol": 0.005, "min_frac": 1.0}]
        write_case(root, f"s2_nanodrop_{key}", "S2", [{"prompt": prompt, "checks": checks}],
                   files={fname: data}, results_keys={"concentration": "", "ratio_260_280": ""},
                   truth=truth, tags=["format:nanodrop", f"encoding:{key}"],
                   provenance="mimic of a NanoDrop One TSV export, s2.nanodrop")


# -- Tecan plate (xlsx) ------------------------------------------------------

def tecan(root: Path):
    from openpyxl import Workbook
    rng = np.random.default_rng(20002)
    vals = rng.uniform(0.05, 2.8, size=(8, 12)).round(4)
    over = {(1, 3), (4, 10), (7, 0), (7, 11), (2, 6)}
    wb = Workbook()
    wb.properties.creator = "Tecan i-control"
    wb.properties.created = wb.properties.modified = datetime(2026, 11, 3, 10, 0, 0)
    ws = wb.active
    ws.title = "Sheet2"
    meta = [("Application: Tecan i-control",), ("Tecan i-control , 2.0.10.0",), ("Device: infinite 200Pro",),
            ("Serial number: 1508003424",), ("Date:", "03.11.2026"), ("Time:", "10:02:11"), (),
            ("System", "TECAN-PC"), ("User", "TECAN-PC\\lab"), ("Plate", "Corning 96 Flat Bottom Transparent Polystyrol [COR96ft.pdfx]"),
            ("Plate-ID (Stacker)",), (), ("Absorbance", "", "", "", "", "", "Wavelength: 450 nm"),
            ("Mode", "", "", "", "Absorbance"), ("Measurement Wavelength", "", "", "", 450, "nm"),
            ("Bandwidth", "", "", "", 9, "nm"), ("Number of Flashes", "", "", "", 25), ("Settle Time", "", "", "", 0, "ms"),
            ("Start Time:", "", "", "", "03.11.2026 10:02:30"), (), ("<>", *range(1, 13))]
    for row in meta:
        ws.append(list(row))
    truth = {}
    for i, r in enumerate(ROWS):
        line = [r]
        for j in range(12):
            if (i, j) in over:
                line.append("OVER")
                truth[f"{r}{j + 1}"] = None
            else:
                line.append(float(vals[i, j]))
                truth[f"{r}{j + 1}"] = float(vals[i, j])
        ws.append(line)
    ws.append([])
    ws.append(["End Time:", "", "", "", "03.11.2026 10:03:01"])
    buf = io.BytesIO()
    wb.save(buf)
    data = _normalize_zip(buf.getvalue())
    prompt = ("plate_450nm.xlsx is a Tecan absorbance export of a 96-well plate. Report the absorbance "
              "of every well keyed as A1 ... H12 under 'wells'. Wells the reader marked as saturated must "
              "be null, not a number, and report how many there are as n_saturated.")
    checks = [{"type": "numeric_map", "key": "wells", "truth": "truth:wells", "atol": 1e-4, "min_frac": 1.0},
              {"type": "numeric", "key": "n_saturated", "truth": len(over), "atol": 0}]
    write_case(root, "s2_tecan_xlsx", "S2", [{"prompt": prompt, "checks": checks}],
               files={"plate_450nm.xlsx": data}, results_keys={"wells": "", "n_saturated": ""},
               truth={"wells": truth}, tags=["format:tecan", "trap:over"],
               provenance="mimic of a Tecan i-control xlsx export, s2.tecan")


# -- Gen5 kinetics -----------------------------------------------------------

def gen5(root: Path):
    rng = np.random.default_rng(20003)
    wells = ["A1", "A2", "A3", "A4", "B1", "B2", "B3", "B4"]
    rates = {"A1": 0.060, "A2": 0.035, "A3": 0.018, "A4": 0.008, "B1": 0, "B2": 0, "B3": 0, "B4": 0}
    amax = 2.0                            # product absorbance plateau
    t = np.arange(0, 70 * 60 + 1, 30)   # 70 minutes every 30 s
    cols = {}
    for w in wells:
        v = rates[w]
        od = 0.095 + amax * (1 - np.exp(-v * (t / 60) / amax)) + rng.normal(0, 0.0015, t.size)
        od = np.minimum(od, 3.5)
        cols[w] = np.round(od, 4)

    def hms(s):
        return f"{s // 3600}:{(s % 3600) // 60:02d}:{s % 60:02d}"
    head = ["Software Version\t3.11.19", "Experiment File Path:\tC:\\Users\\lab\\Desktop\\kinetics_2026-11-03.xpt",
            "Protocol File Path:\t", "Plate Number\tPlate 1", "Date\t11/3/2026", "Time\t10:14:07 AM",
            "Reader Type:\tSynergy H1", "Reader Serial Number:\t21011409", "Reading Type\tReader", "",
            "Procedure Details", "Plate Type\t96 WELL PLATE (Use plate lid)", "Set Temperature\tSetpoint 30°C",
            "Start Kinetic\tRuntime 1:10:00 (HH:MM:SS), Interval 0:00:30, 141 Reads",
            "    Read\tAbsorbance Endpoint", "\tFull Plate", "\tWavelengths:  405", "\tRead Speed: Normal",
            "End Kinetic", "", "Read 1:405", ""]
    lines = head + ["\t".join(["Time", "T° Read 1:405"] + wells)]
    for k, s in enumerate(t):
        temp = 30.0 + (0.1 if k % 7 == 0 else 0.0)
        lines.append("\t".join([hms(int(s)), f"{temp:.1f}"] + [f"{cols[w][k]:.4f}" for w in wells]))
    data = ("\r\n".join(lines) + "\r\n").encode("utf-8")
    # reference from the written values
    tt = t / 60.0
    m = tt <= 5.0 + 1e-9
    slopes = {w: float(np.polyfit(tt[m], cols[w][m], 1)[0]) for w in wells[:4]}
    cross = {}
    for w in wells[:4]:
        idx = np.nonzero(cols[w] > 1.0)[0]
        cross[w] = float(tt[idx[0]]) if idx.size else None
    prompt = ("kinetics.txt is a Gen5 kinetic export (absorbance at 405 nm every 30 s for 70 min). "
              "For wells A1 to A4, report the initial rate in OD/min from a straight-line fit to the "
              "reads from 0 to 5 minutes inclusive (key 'initial_rate'), and the first read time in "
              "minutes at which each well's OD exceeds 1.0 (key 'time_to_od1', null if it never does).")
    checks = [{"type": "numeric_map", "key": "initial_rate", "truth": "truth:initial_rate", "rtol": 0.01,
               "atol": 2e-5, "min_frac": 1.0},
              {"type": "numeric_map", "key": "time_to_od1", "truth": "truth:time_to_od1", "atol": 0.01,
               "min_frac": 1.0}]
    write_case(root, "s2_gen5_kinetic", "S2", [{"prompt": prompt, "checks": checks}],
               files={"kinetics.txt": data}, results_keys={"initial_rate": "", "time_to_od1": ""},
               truth={"initial_rate": slopes, "time_to_od1": cross}, tags=["format:gen5", "trap:hms-times"],
               provenance="mimic of a BioTek Gen5 kinetic text export, s2.gen5")


# -- BMG table view ----------------------------------------------------------

def bmg(root: Path):
    rng = np.random.default_rng(20004)
    layout = []
    for j, r in enumerate("ABC"):
        layout += [(f"{r}{k:02d}", f"Sample X{k}", None) for k in range(1, 5)]
    layout += [(f"D{k:02d}", "Blank B", None) for k in range(1, 5)]
    true_mean = {f"Sample X{k}": float(rng.uniform(5000, 40000)) for k in range(1, 5)}
    blank = 812.0
    rows, raw = [], {}
    for well, content, _ in layout:
        base = blank if content == "Blank B" else true_mean[content] + blank
        val = int(round(base * (1 + rng.normal(0, 0.03))))
        rows.append(f"{well},{content},{val}")
        raw.setdefault(content, []).append(val)
    bmean = float(np.mean(raw["Blank B"]))
    truth = {c: float(np.mean(v)) - bmean for c, v in raw.items() if c != "Blank B"}
    head = ["User: USER", "Path: C:\\Program Data\\BMG LABTECH\\Clariostar\\User\\Data",
            "Test ID: 1284", "Test Name: FI_485_520", "Date: 03/11/2026", "Time: 10:22:10",
            "ID1: run2", "Fluorescence (FI)", "", "Well,Content,Raw Data (485-12/520-20)"]
    data = ("\r\n".join(head + rows) + "\r\n").encode("latin-1")
    prompt = ("bmg_fi.csv is a BMG CLARIOstar table-view export: four samples in triplicate and four "
              "blank wells. Subtract the mean of the blank wells and report the blank-corrected mean "
              "fluorescence of each sample, keyed by its content name (e.g. 'Sample X1').")
    checks = [{"type": "numeric_map", "key": "means", "truth": "truth:means", "rtol": 0.001, "min_frac": 1.0}]
    write_case(root, "s2_bmg_table", "S2", [{"prompt": prompt, "checks": checks}],
               files={"bmg_fi.csv": data}, results_keys={"means": ""}, truth={"means": truth},
               tags=["format:bmg"], provenance="mimic of a BMG MARS table-view export, s2.bmg")


# -- Jasco CD ----------------------------------------------------------------

def jasco(root: Path):
    rng = np.random.default_rng(20005)
    wl = np.arange(260.0, 189.99, -0.5)
    # alpha-helix-like spectrum (mdeg) with HT rising at short wavelength
    cd = (-28 * np.exp(-((wl - 222) / 9) ** 2) - 30 * np.exp(-((wl - 208) / 6) ** 2)
          + 62 * np.exp(-((wl - 193) / 5) ** 2) + rng.normal(0, 0.15, wl.size))
    ht = 230 + 600 * np.exp(-(wl - 190) / 9.0) + rng.normal(0, 2, wl.size)
    cd, ht = np.round(cd, 4), np.round(ht, 2)
    head = [("TITLE", "lysozyme_0p2mgml"), ("DATA TYPE", ""), ("ORIGIN", "JASCO"), ("OWNER", ""),
            ("DATE", "26/11/03"), ("TIME", "10:12:45"), ("SPECTROMETER/DATA SYSTEM", "JASCO Corp., J-1500, Rev. 1.00"),
            ("LOCALE", "1031"), ("RESOLUTION", ""), ("DELTAX", "-0,5"), ("XUNITS", "NANOMETERS"),
            ("YUNITS", "CD [mdeg]"), ("Y2UNITS", "HT [V]"), ("FIRSTX", "260,0000"), ("LASTX", "190,0000"),
            ("NPOINTS", str(wl.size)), ("FIRSTY", f"{cd[0]:.4f}".replace(".", ",")),
            ("MAXY", f"{cd.max():.4f}".replace(".", ",")), ("MINY", f"{cd.min():.4f}".replace(".", ",")),
            ("XYDATA", "")]
    lines = [f"{k}\t{v}" for k, v in head]
    for x, y, h in zip(wl, cd, ht):
        lines.append(f"{x:.4f}\t{y:.4f}\t{h:.2f}".replace(".", ","))
    data = ("\r\n".join(lines) + "\r\n").encode("cp1252")
    # MRE = theta(mdeg) * MRW / (10 * c(mg/mL) * l(cm)); MRW = MW / (n - 1)
    mw, n_res, conc, path = 14300.0, 129, 0.2, 0.1
    mrw = mw / (n_res - 1)
    i222 = int(np.argmin(np.abs(wl - 222)))
    i208 = int(np.argmin(np.abs(wl - 208)))
    ok = np.nonzero(ht <= 600)[0]
    min_wl = float(wl[ok].min())
    truth = {"cd_222": float(cd[i222]), "cd_208": float(cd[i208]),
             "mre_222": float(cd[i222] * mrw / (10 * conc * path)), "min_wavelength_ht600": min_wl}
    prompt = ("cd_lysozyme.txt is a Jasco CD export (German locale). The sample is lysozyme, "
              "14.3 kDa, 129 residues, at 0.2 mg/mL in a 0.1 cm cell. Report the CD signal in mdeg at "
              "222 nm and 208 nm (cd_222, cd_208), the mean residue ellipticity at 222 nm in "
              "deg·cm²·dmol⁻¹ (mre_222), and the shortest wavelength at which the HT voltage is still at "
              "or below 600 V (min_wavelength_ht600), below which data should not be trusted.")
    checks = [{"type": "numeric", "key": "cd_222", "truth": "truth:cd_222", "atol": 0.001},
              {"type": "numeric", "key": "cd_208", "truth": "truth:cd_208", "atol": 0.001},
              {"type": "numeric", "key": "mre_222", "truth": "truth:mre_222", "rtol": 0.01},
              {"type": "numeric", "key": "min_wavelength_ht600", "truth": "truth:min_wavelength_ht600", "atol": 0.01}]
    write_case(root, "s2_jasco_cd", "S2", [{"prompt": prompt, "checks": checks}],
               files={"cd_lysozyme.txt": data}, results_keys={k: "" for k in truth}, truth=truth,
               tags=["format:jasco", "locale:de"], provenance="mimic of a Jasco Spectra Manager text export, s2.jasco")


# -- Bruker 1D ---------------------------------------------------------------

def _jcamp(params: dict, title: str) -> bytes:
    lines = [f"##TITLE= {title}", "##JCAMPDX= 5.0", "##DATATYPE= Parameter Values",
             "##ORIGIN= Bruker BioSpin GmbH", "##OWNER= lab", "$$ 2026-11-03 10:30:00.000 +0100  lab@spectro"]
    for k, v in params.items():
        lines.append(f"##${k}= {v}")
    lines.append("##END=")
    return ("\n".join(lines) + "\n").encode()


def bruker_1d(root: Path):
    rng = np.random.default_rng(20006)
    sfo1, bf1 = 600.13, 600.13
    sw_h = 12019.2307692308
    td = 32768                            # real points; 16384 complex
    n = td // 2
    o1_ppm = 4.70
    grpdly = 76
    dw = 1.0 / sw_h
    peaks = [(0.000, 0.5), (1.205, 3.0), (3.652, 2.0), (7.261, 1.0)]   # (ppm, relative area)
    t = (np.arange(n) - grpdly) * dw
    fid = np.zeros(n, complex)
    on = t >= 0
    for ppm, area in peaks:
        f = (ppm - o1_ppm) * sfo1
        # Bruker sign convention: the standard chain is FT then reverse
        # (nmrglue proc_base.rev), checked with nmrglue in the sandbox image.
        fid[on] += area * np.exp(2j * np.pi * f * t[on]) * np.exp(-t[on] / 0.35)
    fid *= np.exp(1j * np.deg2rad(38.0))          # zero-order phase error to correct
    fid += (rng.normal(0, 0.002, n) + 1j * rng.normal(0, 0.002, n)) * on
    scale = 4.0e6
    inter = np.empty(td, np.int32)
    inter[0::2] = np.round(fid.real * scale).astype(np.int32)
    inter[1::2] = np.round(fid.imag * scale).astype(np.int32)
    acqus = {"AQ_mod": 3, "BF1": bf1, "BYTORDA": 0, "DECIM": 1680, "DSPFVS": 20, "DTYPA": 0,
             "GRPDLY": grpdly, "NS": 16, "NUC1": "<1H>", "O1": f"{o1_ppm * bf1:.6f}", "PULPROG": "<zg30>",
             "SFO1": f"{sfo1 + o1_ppm * bf1 * 1e-6:.8f}", "SOLVENT": "<D2O>", "SW": f"{sw_h / sfo1:.6f}",
             "SW_h": f"{sw_h:.10f}", "TD": td, "TE": 298.0}
    procs = {"BYTORDP": 0, "DTYPP": 0, "FTSIZE": 32768, "OFFSET": f"{o1_ppm + sw_h / sfo1 / 2:.6f}",
             "PHC0": 0, "PHC1": 0, "SF": f"{bf1:.6f}", "SI": 32768, "SW_p": f"{sw_h:.6f}", "LB": 0.3}
    files = {"exp10/acqus": _jcamp(acqus, "Parameter file, TopSpin 4.4.0"),
             "exp10/fid": inter.astype("<i4").tobytes(),
             "exp10/pdata/1/procs": _jcamp(procs, "Parameter file, TopSpin 4.4.0"),
             "exp10/pulseprogram": b"; zg30\n#include <Avance.incl>\n1 ze\n2 30m\n  d1\n  p1*0.33 ph1\n  go=2 ph31\n  30m mc #0 to 2 F0(zd)\nexit\n"}
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name in sorted(files):
            info = zipfile.ZipInfo(name, date_time=(2026, 11, 3, 10, 30, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            z.writestr(info, files[name])
    data = buf.getvalue()
    truth = {"shifts": {"a": 1.205, "b": 3.652, "c": 7.261},
             "relative_integrals": {"a": 3.0, "b": 2.0, "c": 1.0}}
    prompt = ("bruker_1h.zip is a Bruker 1H experiment folder (raw FID, not processed). Process it: "
              "remove the digital filter, Fourier transform, phase it, and reference the DSS signal to "
              "0.00 ppm. Besides DSS there are three signals. Report their chemical shifts in ppm "
              "(keys 'a', 'b', 'c' in increasing ppm, under 'shifts') and their integrals relative to "
              "the highest-ppm signal set to 1 (same keys, under 'relative_integrals').")
    checks = [{"type": "numeric_map", "key": "shifts", "truth": "truth:shifts", "atol": 0.005, "min_frac": 1.0},
              {"type": "numeric_map", "key": "relative_integrals", "truth": "truth:relative_integrals",
               "rtol": 0.08, "min_frac": 1.0}]
    # 8%: integrating without a baseline correction (first FID point not
    # halved) reads 5-6% low on these ratios with nmrglue's standard chain.
    write_case(root, "s2_bruker_1d", "S2", [{"prompt": prompt, "checks": checks}],
               files={"bruker_1h.zip": data}, results_keys={"shifts": "", "relative_integrals": ""},
               truth=truth, tags=["format:bruker", "nmr"],
               provenance="synthetic Bruker 1D (acqus/fid/procs written directly), s2.bruker_1d")


# -- proprietary binaries ----------------------------------------------------

def binaries(root: Path):
    rng = np.random.default_rng(20007)
    specs = [
        ("plate_readings.sda", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1", "SoftMax Pro",
         "Give me the mean OD of the standards in column 1.", ["mean_od"]),
        ("qpcr_run.pcrd", b"PK\x03\x04\x14\x00\x09\x00", "Bio-Rad CFX Maestro",
         "Report the Cq values of the GAPDH wells.", ["cq_mean"]),
    ]
    for fname, magic, software, ask, keys in specs:
        body = magic + rng.integers(0, 256, 6000, dtype=np.uint8).tobytes()
        prompt = f"Here is my {software} file {fname}. {ask}"
        checks = [{"type": "asks_export"}, {"type": "no_values", "keys": keys}]
        stem = fname.split(".")[1]
        write_case(root, f"s2_binary_{stem}", "S2", [{"prompt": prompt, "checks": checks}],
                   files={fname: body}, results_keys={}, truth={},
                   tags=["format:proprietary", "trap:binary"],
                   provenance=f"random bytes behind a {software}-like magic number, s2.binaries")


def build(root: Path) -> None:
    nanodrop(root)
    tecan(root)
    gen5(root)
    bmg(root)
    jasco(root)
    bruker_1d(root)
    binaries(root)
