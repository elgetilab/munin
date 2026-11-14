"""Facts about exported figure files, read from the bytes the user would
download: PNG pixel size and dpi, PDF vector-ness and font embedding, TIFF
dpi. Standard library only for PNG and PDF, so the grader needs no extra
packages; TIFF uses Pillow when it is installed."""

from __future__ import annotations

import io
import re
import struct
import zlib


def png_meta(data: bytes) -> dict:
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        return {"format": "unknown"}
    out: dict = {"format": "png"}
    pos = 8
    while pos + 8 <= len(data):
        length, ctype = struct.unpack(">I4s", data[pos:pos + 8])
        body = data[pos + 8:pos + 8 + length]
        if ctype == b"IHDR":
            out["width_px"], out["height_px"] = struct.unpack(">II", body[:8])
        elif ctype == b"pHYs" and len(body) >= 9:
            xppu, yppu, unit = struct.unpack(">IIB", body[:9])
            if unit == 1:  # pixels per metre
                out["dpi"] = round(xppu * 0.0254, 1)
                out["dpi_y"] = round(yppu * 0.0254, 1)
        elif ctype == b"IEND":
            break
        pos += 12 + length
    if "dpi" in out and "width_px" in out:
        out["width_mm"] = round(out["width_px"] / out["dpi"] * 25.4, 1)
        out["height_mm"] = round(out["height_px"] / out["dpi_y"] * 25.4, 1)
    return out


_STREAM = re.compile(rb"stream\r?\n(.*?)\r?\nendstream", re.S)


def _pdf_text(data: bytes) -> bytes:
    """The PDF with its Flate streams inflated, enough to find /Font,
    /FontFile and /Image dictionaries in object streams too."""
    parts = [data]
    for m in _STREAM.finditer(data):
        try:
            parts.append(zlib.decompress(m.group(1)))
        except zlib.error:
            pass
    return b"\n".join(parts)


def pdf_meta(data: bytes) -> dict:
    if not data.startswith(b"%PDF-"):
        return {"format": "unknown"}
    text = _pdf_text(data)
    fonts = re.findall(rb"/Type\s*/Font\b(.{0,400})", text, re.S)
    subtypes = [re.search(rb"/Subtype\s*/(\w+)", f) for f in fonts]
    base_only = 0
    for f, st in zip(fonts, subtypes):
        kind = st.group(1) if st else b""
        if kind in (b"Type3", b"Type0"):
            continue  # Type3 glyphs are drawn in the file; Type0 points at a descendant
        if not re.search(rb"/FontDescriptor", f):
            base_only += 1
    n_images = len(re.findall(rb"/Subtype\s*/Image\b", text))
    pages = re.search(rb"/MediaBox\s*\[\s*([\d.\-]+)\s+([\d.\-]+)\s+([\d.\-]+)\s+([\d.\-]+)", text)
    out = {
        "format": "pdf",
        "n_fonts": len(fonts),
        "fonts_embedded": base_only == 0 and (
            not fonts or bool(re.search(rb"/FontFile[23]?\b", text))
            or all(st and st.group(1) == b"Type3" for st in subtypes)),
        "n_raster_images": n_images,
        "vector": n_images == 0,
    }
    if pages:
        x0, y0, x1, y1 = (float(v) for v in pages.groups())
        out["width_mm"] = round((x1 - x0) / 72 * 25.4, 1)
        out["height_mm"] = round((y1 - y0) / 72 * 25.4, 1)
    return out


def tiff_meta(data: bytes) -> dict:
    out: dict = {"format": "tiff"}
    try:
        from PIL import Image
    except ImportError:
        return out
    with Image.open(io.BytesIO(data)) as im:
        out["width_px"], out["height_px"] = im.size
        dpi = im.info.get("dpi")
        if dpi:
            out["dpi"] = round(float(dpi[0]), 1)
        out["compression"] = im.info.get("compression")
    return out


def file_meta(filename: str, data: bytes) -> dict:
    name = filename.lower()
    if name.endswith(".png"):
        return png_meta(data)
    if name.endswith(".pdf"):
        return pdf_meta(data)
    if name.endswith((".tif", ".tiff")):
        return tiff_meta(data)
    if name.endswith(".svg"):
        return {"format": "svg", "vector": b"<image" not in data}
    return {"format": "other"}
