"""Shared look for the paper figures: fonts, sizes, colours.

Sans-serif throughout. Arial and Liberation Sans share metrics, so a figure
laid out with either renders to the same geometry; DejaVu Sans is the last
resort and is wider, so text may crowd its boxes (a warning says so).
"""

from __future__ import annotations

import warnings

import matplotlib as mpl
from matplotlib import font_manager

# Widths in inches: the RSC (Digital Discovery) double column, 17.1 cm, and
# single column, 8.3 cm.
CM = 1 / 2.54
FULL_WIDTH = 17.1 * CM
COLUMN_WIDTH = 8.3 * CM
FORMATS = "pdf,png,svg,tiff"           # RSC takes TIFF at 600 dpi or more
TIFF_DPI = 600

FONT_CANDIDATES = ["Arial", "Liberation Sans", "Helvetica", "DejaVu Sans"]

# Okabe-Ito accents (colour-blind safe), each with a pale fill for boxes.
INK = "#1F2328"
MUTED = "#57606A"
RULE = "#8C959F"
BLUE = "#0072B2"        # the harness
BLUE_FILL = "#E4EFF8"
VERMILION = "#D55E00"   # the egress boundary
VERMILION_FILL = "#FBEBDF"
GREEN = "#009E73"       # on-premise
GREEN_FILL = "#E6F4EF"
GREY_FILL = "#F1F3F5"   # rented / neutral
WHITE = "#FFFFFF"


def _pick_font() -> str:
    available = {f.name for f in font_manager.fontManager.ttflist}
    for name in FONT_CANDIDATES:
        if name in available:
            if name == "DejaVu Sans":
                warnings.warn(
                    "Neither Arial nor Liberation Sans is installed; falling back "
                    "to DejaVu Sans, which is wider, so labels may crowd their "
                    "boxes. Install fonts-liberation for the reference layout."
                )
            return name
    return "sans-serif"


def box_axes(ax, *, labelsize: float = 6.0) -> None:
    """The house plot frame: a full box, ticks inward on all four sides."""
    for side in ("top", "right", "bottom", "left"):
        ax.spines[side].set_visible(True)
        ax.spines[side].set_linewidth(0.6)
        ax.spines[side].set_color(INK)
    ax.tick_params(which="both", direction="in", top=True, right=True, length=2.5,
                   width=0.6, color=INK, labelsize=labelsize)


def save(fig, out: str, formats: str = FORMATS) -> list[str]:
    """Write fig to out.<ext> for each format, without timestamps, so a re-run
    is byte-identical when nothing it reads has changed."""
    written = []
    for ext in formats.split(","):
        kw = {}
        if ext == "pdf":
            kw["metadata"] = {"CreationDate": None}
        elif ext == "svg":
            kw["metadata"] = {"Date": None}
        elif ext == "tiff":
            kw["dpi"] = TIFF_DPI
            kw["pil_kwargs"] = {"compression": "tiff_lzw"}
        path = f"{out}.{ext}"
        fig.savefig(path, **kw)
        written.append(path)
    return written


def apply() -> str:
    """Set rcParams for publication output. Returns the font in use."""
    font = _pick_font()
    mpl.rcParams.update({
        "font.family": "sans-serif",
        "font.sans-serif": [font],
        "font.size": 7,
        "text.color": INK,
        "axes.edgecolor": INK,
        # TrueType in PDF/PS so text stays text (selectable, editable, and
        # accepted by publishers that reject Type 3 fonts).
        "pdf.fonttype": 42,
        "ps.fonttype": 42,
        "svg.fonttype": "none",
        # Fixed salt: otherwise SVG element ids are random and every re-run
        # is a diff. Ships with matplotlib, so identical on every machine.
        "svg.hashsalt": "munin",
        "font.monospace": ["DejaVu Sans Mono"],
        "savefig.dpi": 300,
        "savefig.facecolor": WHITE,
    })
    return font
