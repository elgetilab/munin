"""Instrument and table readers that report what they found and refuse to guess.

    from labkit import read
    df = read("export.txt")          # sniffs the format; df.attrs holds the rest
    df.attrs["meta"]                 # header key/values (instrument, wavelength, ...)
    df.attrs["units"]                # {column: unit} when the file says
    df.attrs["warnings"]             # e.g. wells that read OVER (saturated)
    df.attrs["non_numeric"]          # {column: {row: token}} cells that were not numbers

Every reader returns a pandas DataFrame. Cells that are not numbers in a
numeric column (OVER, ####, n/a) become NaN AND are listed in
attrs["non_numeric"] and attrs["warnings"]: never drop them silently, say so
in the answer. Proprietary binary files raise UnsupportedFormat with what to
export instead.
"""
from __future__ import annotations

import io
import re
from pathlib import Path
from typing import Any, Optional, Union

import numpy as np
import pandas as pd


class UnsupportedFormat(ValueError):
    """A file labkit cannot (or must not) parse: ask the user for an export."""


PathLike = Union[str, Path]

_BINARY_HINTS = {
    ".pcrd": "a Bio-Rad CFX run file: open it in CFX Maestro and export the results (Export > Export All Data Sheets, CSV)",
    ".sda": "a SoftMax Pro document: open it in SoftMax Pro and export the plate data as .txt or .xls",
    ".xpt": "a Gen5 experiment file: export the plate data from Gen5 as .txt or .xlsx",
    ".pzfx": "a Prism file: export the data table as CSV, or ask for a labkit .pzfx reader (not in this version)",
    ".prism": "a Prism file: export the data table as CSV",
    ".opj": "an Origin project: export the worksheet as CSV",
    ".opju": "an Origin project: export the worksheet as CSV",
}


# ==============================================================================
# Decoding and number parsing
# ==============================================================================

def _decode(data: bytes) -> tuple[str, str]:
    if data.startswith(b"\xff\xfe") or data.startswith(b"\xfe\xff"):
        return data.decode("utf-16"), "utf-16"
    if data.startswith(b"\xef\xbb\xbf"):
        return data[3:].decode("utf-8"), "utf-8-sig"
    # UTF-16 without BOM: every other byte zero in ASCII text
    if len(data) > 8 and data[1::2].count(0) > 0.4 * (len(data) // 2):
        return data.decode("utf-16-le"), "utf-16-le"
    try:
        return data.decode("utf-8"), "utf-8"
    except UnicodeDecodeError:
        return data.decode("cp1252", errors="replace"), "cp1252"


def _looks_binary(data: bytes) -> bool:
    head = data[:4096]
    if head.startswith(b"\xd0\xcf\x11\xe0"):  # OLE2 compound document
        return True
    if not head:
        return False
    if head.startswith((b"\xff\xfe", b"\xfe\xff")):
        return False
    if head[1::2].count(0) > 0.4 * (len(head) // 2):
        return False  # UTF-16 text
    nontext = sum(1 for b in head if b < 9 or (13 < b < 32))
    return nontext > 0.05 * len(head)


_NUM_DOT = re.compile(r"^[+-]?(\d+(\.\d*)?|\.\d+)([eE][+-]?\d+)?$")
_NUM_COMMA = re.compile(r"^[+-]?(\d+(,\d*)?|,\d+)([eE][+-]?\d+)?$")
_NUM_COMMA_THOUSANDS = re.compile(r"^[+-]?\d{1,3}(\.\d{3})+(,\d+)?$")


def _to_float(tok: str, decimal: str) -> Optional[float]:
    t = tok.strip().replace("−", "-")
    if not t:
        return None
    if decimal == ",":
        if _NUM_COMMA_THOUSANDS.match(t):
            t = t.replace(".", "")
        if _NUM_COMMA.match(t):
            return float(t.replace(",", "."))
        if _NUM_DOT.match(t) and "." not in t:
            return float(t)
        return None
    if _NUM_DOT.match(t):
        return float(t)
    return None


def _guess_decimal(tokens: list[str], delimiter: str) -> str:
    comma = sum(1 for t in tokens if _NUM_COMMA.match(t.strip()) and "," in t)
    dot = sum(1 for t in tokens if _NUM_DOT.match(t.strip()) and "." in t)
    if delimiter == "," and comma:
        return "."
    return "," if comma > dot else "."


# ==============================================================================
# Generic delimited text with a preamble
# ==============================================================================

def _split(line: str, delim: str) -> list[str]:
    if delim == "whitespace":
        return line.split()
    return [c.strip().strip('"') for c in line.split(delim)]


def _find_table(lines: list[str]) -> tuple[int, str]:
    """(header line index, delimiter): the first line that starts a run of at
    least 3 following lines with the same field count (>= 2)."""
    best = None
    for delim in ("\t", ";", ",", "whitespace"):
        counts = [len(_split(l, delim)) if l.strip() else 0 for l in lines]
        for i in range(len(lines)):
            c = counts[i]
            if c < 2:
                continue
            run = 1
            for j in range(i + 1, len(lines)):
                if counts[j] == c:
                    run += 1
                else:
                    break
            if run >= 4 or (run >= 2 and i + run == len(lines)):
                score = (c * min(run, 50), -i)
                if best is None or score > best[0]:
                    best = (score, i, delim)
                break
    if best is None:
        raise UnsupportedFormat("no table found: no block of lines with a consistent number of columns")
    return best[1], best[2]


def _preamble_meta(lines: list[str]) -> dict:
    meta: dict[str, Any] = {}
    for l in lines:
        s = l.strip()
        if not s:
            continue
        if "\t" in s:
            k, _, v = s.partition("\t")
        elif ":" in s:
            k, _, v = s.partition(":")
        else:
            meta.setdefault("_lines", []).append(s)
            continue
        k, v = k.strip().rstrip(":"), v.strip()
        if k:
            meta[k] = v
    return meta


def _numeric_frame(header: list[str], rows: list[list[str]], decimal: str) -> pd.DataFrame:
    width = len(header)
    rows = [r + [""] * (width - len(r)) if len(r) < width else r[:width] for r in rows]
    cols: dict[str, list] = {}
    non_numeric: dict[str, dict] = {}
    names = []
    seen: dict[str, int] = {}
    for h in header:
        h = h or "column"
        if h in seen:
            seen[h] += 1
            h = f"{h}.{seen[h]}"
        else:
            seen[h] = 0
        names.append(h)
    for j, name in enumerate(names):
        raw = [r[j] for r in rows]
        vals = [_to_float(t, decimal) for t in raw]
        filled = [t for t in raw if t.strip()]
        nums = sum(v is not None for v in vals)
        if filled and nums >= 0.6 * len(filled):
            cols[name] = [np.nan if v is None else v for v in vals]
            bad = {i: t for i, (t, v) in enumerate(zip(raw, vals)) if v is None and t.strip()}
            if bad:
                non_numeric[name] = bad
        else:
            cols[name] = raw
    df = pd.DataFrame(cols, columns=names)
    df.attrs["non_numeric"] = non_numeric
    return df


def _finish(df: pd.DataFrame, *, fmt: str, encoding: str, meta: dict, units: Optional[dict] = None,
            decimal: str = ".", warnings: Optional[list] = None) -> pd.DataFrame:
    warn = list(warnings or [])
    nn = df.attrs.get("non_numeric", {})
    for col, cells in nn.items():
        tokens = sorted(set(cells.values()))
        warn.append(f"column {col!r}: {len(cells)} non-numeric cell(s) {tokens} read as NaN "
                    f"(rows {sorted(cells)[:10]}{'...' if len(cells) > 10 else ''}); report them, "
                    f"do not drop them silently")
    df.attrs.update({"format": fmt, "encoding": encoding, "decimal": decimal, "meta": meta,
                     "units": units or {}, "warnings": warn, "non_numeric": nn})
    return df


def read_table(path: PathLike, *, decimal: Optional[str] = None, delimiter: Optional[str] = None) -> pd.DataFrame:
    """Any delimited text table, with an instrument preamble before it.

    Detects encoding (UTF-8/16, BOM, cp1252), delimiter (tab ; , space) and
    decimal comma. Lines above the table become attrs["meta"].
    """
    data = Path(path).read_bytes()
    if _looks_binary(data):
        raise UnsupportedFormat(_binary_message(path))
    text, enc = _decode(data)
    return _table_from_text(text, enc, decimal=decimal, delimiter=delimiter)


def _table_from_text(text: str, enc: str, *, decimal=None, delimiter=None, fmt="table") -> pd.DataFrame:
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    if delimiter is None:
        hi, delim = _find_table(lines)
    else:
        delim = delimiter
        counts = [len(_split(l, delim)) for l in lines]
        hi = next(i for i, c in enumerate(counts) if c >= 2)
    header = _split(lines[hi], delim)
    rows = []
    for l in lines[hi + 1:]:
        if not l.strip():
            break
        f = _split(l, delim)
        if len(f) < 2 and len(header) >= 2:
            break
        rows.append(f)
    tokens = [t for r in rows for t in r]
    dec = decimal or _guess_decimal(tokens, delim)
    df = _numeric_frame(header, rows, dec)
    units = {}
    for c in df.columns:
        m = re.search(r"[\[(]([^\])]+)[\])]\s*$", str(c))
        if m:
            units[c] = m.group(1)
    return _finish(df, fmt=fmt, encoding=enc, meta=_preamble_meta(lines[:hi]), units=units, decimal=dec)


# ==============================================================================
# Plate grids (Tecan, generic 96/384 layouts) in xlsx or text
# ==============================================================================

_ROWS = "ABCDEFGHIJKLMNOP"


def _grid_from_rows(rows: list[list[Any]]) -> Optional[tuple[int, int, int]]:
    """Find a plate grid: a header row of 1..N and following rows labelled A, B, ...
    Returns (header row index, first column index, number of columns)."""
    for i, r in enumerate(rows):
        cells = [c for c in r]
        for j in range(len(cells)):
            nums = []
            for c in cells[j + 1:]:
                try:
                    nums.append(int(float(str(c).strip())))
                except (TypeError, ValueError):
                    break
            if len(nums) >= 3 and nums == list(range(1, len(nums) + 1)):
                nxt = rows[i + 1] if i + 1 < len(rows) else []
                if len(nxt) > j and str(nxt[j]).strip().upper() == "A":
                    return i, j, len(nums)
    return None


def read_plate(path: PathLike, *, sheet: Optional[str] = None) -> pd.DataFrame:
    """A plate-layout export (rows A-H/P, columns 1-12/24), xlsx or text.

    Returns long format: well ('A1'), row, col, value. Saturated or
    non-numeric wells (OVER, ####) are NaN and listed in attrs.
    """
    p = Path(path)
    data = p.read_bytes()
    if data[:2] == b"PK" and p.suffix.lower() in (".xlsx", ".xlsm"):
        import openpyxl
        wb = openpyxl.load_workbook(io.BytesIO(data), data_only=True, read_only=True)
        ws = wb[sheet] if sheet else wb.active
        rows = [list(r) for r in ws.iter_rows(values_only=True)]
        enc = "xlsx"
    else:
        if _looks_binary(data):
            raise UnsupportedFormat(_binary_message(path))
        text, enc = _decode(data)
        lines = text.replace("\r\n", "\n").split("\n")
        delim = "\t" if any("\t" in l for l in lines) else (";" if any(";" in l for l in lines) else ",")
        rows = [_split(l, delim) for l in lines]
    found = _grid_from_rows(rows)
    if not found:
        raise UnsupportedFormat("no plate grid (a 1..12 header row followed by rows A, B, ...) in this file; "
                                "use read_table() for a list-style export")
    hi, j, ncol = found
    meta = {}
    for r in rows[:hi]:
        vals = [c for c in r if c not in (None, "")]
        if len(vals) >= 2:
            meta[str(vals[0]).strip().rstrip(":")] = " ".join(str(v) for v in vals[1:])
        elif len(vals) == 1 and ":" in str(vals[0]):
            k, _, v = str(vals[0]).partition(":")
            meta[k.strip()] = v.strip()
    recs, non_num = [], {}
    tokens = []
    for r in rows[hi + 1:]:
        label = str(r[j]).strip().upper() if len(r) > j and r[j] is not None else ""
        if len(label) != 1 or label not in _ROWS:
            break
        for c in range(ncol):
            v = r[j + 1 + c] if len(r) > j + 1 + c else None
            tokens.append(str(v) if v is not None else "")
    dec = _guess_decimal(tokens, "\t")
    for r in rows[hi + 1:]:
        label = str(r[j]).strip().upper() if len(r) > j and r[j] is not None else ""
        if len(label) != 1 or label not in _ROWS:
            break
        for c in range(ncol):
            v = r[j + 1 + c] if len(r) > j + 1 + c else None
            well = f"{label}{c + 1}"
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                val = float(v)
            else:
                val = _to_float(str(v), dec) if v not in (None, "") else None
                if val is None and v not in (None, ""):
                    non_num[well] = str(v)
            recs.append({"well": well, "row": label, "col": c + 1,
                         "value": np.nan if val is None else val})
    df = pd.DataFrame(recs)
    warn = []
    if non_num:
        warn.append(f"{len(non_num)} well(s) are not numbers {sorted(set(non_num.values()))}: "
                    f"{', '.join(sorted(non_num, key=lambda w: (w[0], int(w[1:]))))}. OVER means the "
                    f"reading saturated the detector: report these wells, do not drop or impute them")
    df.attrs["non_numeric"] = {"value": non_num} if non_num else {}
    out = _finish(df, fmt="plate", encoding=enc, meta=meta, decimal=dec)
    out.attrs["warnings"] = warn  # the plate message replaces the generic one
    return out


# ==============================================================================
# Vendor formats
# ==============================================================================

def read_nanodrop(path: PathLike) -> pd.DataFrame:
    """NanoDrop export (any locale or encoding). Adds canonical columns:
    sample, conc_ng_ul, a260_280, a260_230 (originals kept)."""
    df = read_table(path)
    canon = {"sample name": "sample", "sample id": "sample", "nucleic acid(ng/ul)": "conc_ng_ul",
             "nucleic acid conc.": "conc_ng_ul", "conc.": "conc_ng_ul", "a260/a280": "a260_280",
             "260/280": "a260_280", "a260/a230": "a260_230", "260/230": "a260_230"}
    for c in list(df.columns):
        key = str(c).strip().lower()
        if key in canon and canon[key] not in df.columns:
            df[canon[key]] = df[c]
    if "conc_ng_ul" not in df.columns:
        df.attrs.setdefault("warnings", []).append("no concentration column recognised; check the column names")
    df.attrs["format"] = "nanodrop"
    df.attrs.setdefault("units", {})["conc_ng_ul"] = "ng/uL"
    return df


def _hms_to_min(t: str) -> float:
    parts = [float(p) for p in t.strip().split(":")]
    while len(parts) < 3:
        parts.insert(0, 0.0)
    h, m, s = parts
    return h * 60 + m + s / 60


def read_gen5(path: PathLike, *, block: Optional[str] = None) -> pd.DataFrame:
    """BioTek Gen5 text export (kinetic or endpoint).

    Kinetic: wide frame with time_min, temperature_C (if present) and one
    column per well. With several read blocks (e.g. 'Read 1:405' and
    'Read 2:600'), pick one with block=; attrs["blocks"] lists them.
    """
    data = Path(path).read_bytes()
    text, enc = _decode(data)
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    blocks: dict[str, int] = {}
    for i, l in enumerate(lines):
        if l.split("\t")[0].strip() == "Time" and i + 1 < len(lines):
            label = ""
            for k in range(i - 1, max(i - 4, -1), -1):
                if lines[k].strip():
                    label = lines[k].strip()
                    break
            blocks[label or f"block{len(blocks) + 1}"] = i
    if not blocks:
        df = read_table(path)
        df.attrs["format"] = "gen5"
        return df
    name = block or next(iter(blocks))
    if name not in blocks:
        raise ValueError(f"block {name!r} not found; blocks: {list(blocks)}")
    hi = blocks[name]
    header = [c.strip() for c in lines[hi].split("\t")]
    rows = []
    for l in lines[hi + 1:]:
        if not l.strip():
            break
        rows.append([c.strip() for c in l.split("\t")])
    times = [_hms_to_min(r[0]) for r in rows]
    df = _numeric_frame(header[1:], [r[1:] for r in rows], _guess_decimal([t for r in rows for t in r[1:]], "\t"))
    rename = {c: "temperature_C" for c in df.columns if str(c).startswith(("T°", "T°", "Temp"))}
    df = df.rename(columns=rename)
    df.insert(0, "time_min", times)
    meta = _preamble_meta(lines[:min(blocks.values())])
    out = _finish(df, fmt="gen5", encoding=enc, meta=meta, units={"time_min": "min", "temperature_C": "°C"})
    out.attrs["blocks"] = list(blocks)
    out.attrs["block"] = name
    return out


def read_jasco(path: PathLike) -> pd.DataFrame:
    """JASCO spectrum export (.txt/.csv with XYDATA), any locale.

    Columns are named from the header units: e.g. wavelength_nm, cd_mdeg,
    ht_v. attrs["meta"] keeps every header field.
    """
    text, enc = _decode(Path(path).read_bytes())
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    try:
        start = next(i for i, l in enumerate(lines) if l.strip().upper().startswith("XYDATA"))
    except StopIteration:
        raise UnsupportedFormat("no XYDATA section: not a JASCO export (use read_table)")
    meta = {}
    for l in lines[:start]:
        k, _, v = l.partition("\t") if "\t" in l else l.partition(",")
        if k.strip():
            meta[k.strip()] = v.strip()
    rows = []
    for l in lines[start + 1:]:
        if not l.strip() or not re.match(r"^\s*[-+\d.,]", l):
            break
        rows.append([c.strip() for c in re.split(r"\t|;", l) if c.strip() != ""])
    tokens = [t for r in rows for t in r]
    dec = "," if (meta.get("LOCALE") in ("1031", "1036", "1040", "1043", "1053") or
                  _guess_decimal(tokens, "\t") == ",") else "."

    def colname(unit_field: str, fallback: str) -> tuple[str, str]:
        u = meta.get(unit_field, "")
        m = re.match(r"\s*([^\[]*)\[(.*)\]", u)
        if m:
            q, unit = m.group(1).strip().lower() or fallback, m.group(2).strip()
            return f"{q}_{unit.lower().replace(' ', '')}".replace("__", "_"), unit
        if u.upper() == "NANOMETERS":
            return "wavelength_nm", "nm"
        return fallback, u
    names, units = [], {}
    for fld, fb in (("XUNITS", "x"), ("YUNITS", "y"), ("Y2UNITS", "y2"), ("Y3UNITS", "y3")):
        if fld == "XUNITS" or fld in meta:
            n, u = colname(fld, fb)
            names.append(n)
            units[n] = u
    width = max(len(r) for r in rows) if rows else 0
    names = (names + [f"y{k}" for k in range(len(names), width)])[:width]
    df = _numeric_frame(names, rows, dec)
    return _finish(df, fmt="jasco", encoding=enc, meta=meta, units=units, decimal=dec)


def read(path: PathLike, **kw) -> pd.DataFrame:
    """Sniff the format and dispatch: JASCO, Gen5, NanoDrop, plate grids,
    generic tables. Bruker NMR data: use labkit.nmr.read_bruker."""
    p = Path(path)
    data = p.read_bytes()
    suffix = p.suffix.lower()
    if suffix in (".dsc", ".dta", ".par", ".spc"):
        raise UnsupportedFormat(f"{p.name} is a Bruker EPR file: use labkit.epr.load(path) "
                                f"(see labkit.guide('epr'))")
    if p.is_dir() or suffix == ".zip":
        raise UnsupportedFormat("a directory or zip: for Bruker NMR use labkit.nmr.read_bruker(path)")
    if data[:2] == b"PK" and suffix in (".xlsx", ".xlsm"):
        try:
            return read_plate(p, **kw)
        except UnsupportedFormat:
            df = pd.read_excel(io.BytesIO(data))
            return _finish(df, fmt="xlsx", encoding="xlsx", meta={})
    if suffix in _BINARY_HINTS or _looks_binary(data):
        raise UnsupportedFormat(_binary_message(p))
    text, enc = _decode(data)
    head = text[:4000]
    if "XYDATA" in head.upper() and ("JASCO" in head.upper() or "YUNITS" in head.upper()):
        return read_jasco(p)
    if re.search(r"^Time\t", text, re.M) and ("Reader Type" in head or "Software Version" in head):
        return read_gen5(p, **kw)
    if "A260/A280" in head or "Nucleic Acid" in head:
        return read_nanodrop(p)
    lines = text.splitlines()
    if _grid_from_rows([re.split(r"\t|;|,", l) for l in lines[:200]]):
        return read_plate(p)
    return _table_from_text(text, enc, **kw)


def _binary_message(path: PathLike) -> str:
    suffix = Path(path).suffix.lower()
    hint = _BINARY_HINTS.get(suffix)
    base = f"{Path(path).name} is a binary file that labkit does not parse"
    if hint:
        return f"{base}: it is {hint}. Ask the user for that export rather than guessing at the bytes."
    return (f"{base} (proprietary or unknown format). Ask the user to export the data from the "
            f"instrument software as CSV, TXT or XLSX rather than guessing at the bytes.")
