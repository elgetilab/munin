"""Table-of-contents graphic (graphical abstract) for Digital Discovery.

RSC's format: at most 8 cm wide x 4 cm high, TIFF at 600 dpi, text limited to
labels. Written for a lab head who has not read the paper, so it uses plain
words, not the paper's internal terms. Left: what we ship, Munin, the
open-source LLM harness, on the lab's own servers, taking a question, reading
the group's papers and answering with citations, with the web reachable only
if the lab allows it. Right: why it is worth installing, the share of LitQA2
questions answered correctly by the same open model alone and inside Munin
without and with the web (the egress-off and full-egress runs; the bars say
which, since the left half presents the web as optional), read through
fig2_arms_by_backbone so the two figures cannot disagree. The 1-2 sentence text that goes with it is in
captions.txt.

    python3 docs/paper-kit/figures/fig_toc.py         # pdf + png + svg + tiff
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.patches import (Arc, Circle, Ellipse, FancyArrowPatch,  # noqa: E402
                                FancyBboxPatch, PathPatch)
from matplotlib.path import Path as MPath  # noqa: E402

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import _style as S  # noqa: E402
import fig2_arms_by_backbone as F2  # noqa: E402

W_CM, H_CM = 8.0, 4.0
BACKBONE = "Qwen3.8-27B"                 # the production backbone, the headline
BARS = [("model alone", "bare\nmodel"),
        ("with Munin, no web", "harness,\negress off"),
        ("with Munin + web", "harness,\nfull")]  # (label, the matching fig2 arm)
SKY = "#56B4E9"                          # Okabe-Ito sky blue: Munin without the web
LAB_X1 = 4.68                            # right edge of the lab boundary
WEB_Y = 0.5                              # height of the lock and the web


def box(ax, x0, y0, x1, y1, *, fc=S.WHITE, ec=S.RULE, lw=0.6, r=0.08, ls="-", z=2):
    ax.add_patch(FancyBboxPatch((x0, y0), x1 - x0, y1 - y0,
                                boxstyle=f"round,pad=0,rounding_size={r}",
                                fc=fc, ec=ec, lw=lw, ls=ls, zorder=z))


def arrow(ax, p0, p1, *, color, lw=0.9, head=6, style="-|>", z=4):
    ax.add_patch(FancyArrowPatch(p0, p1, arrowstyle=f"{style},head_length=0.4,head_width=0.22",
                                 mutation_scale=head, color=color, lw=lw,
                                 shrinkA=0, shrinkB=0, zorder=z))


def bubble(ax, x0, y0, x1, y1, *, tail_x, ec, fc=S.WHITE):
    """A speech bubble with its tail at the bottom, drawn as one outline so
    the border runs into the tail without a seam."""
    r, L, C, M = 0.14, MPath.LINETO, MPath.CURVE3, MPath.MOVETO
    verts = [(x0 + r, y0), (tail_x - 0.08, y0), (tail_x - 0.04, y0 - 0.17),
             (tail_x + 0.08, y0), (x1 - r, y0), (x1, y0), (x1, y0 + r),
             (x1, y1 - r), (x1, y1), (x1 - r, y1), (x0 + r, y1), (x0, y1),
             (x0, y1 - r), (x0, y0 + r), (x0, y0), (x0 + r, y0), (x0 + r, y0)]
    codes = [M, L, L, L, L, C, C, L, C, C, L, C, C, L, C, C, MPath.CLOSEPOLY]
    ax.add_patch(PathPatch(MPath(verts, codes), fc=fc, ec=ec, lw=0.8,
                           joinstyle="miter", zorder=3))


def papers(ax, cx, cy, *, color):
    """A small stack of three pages."""
    w, h = 0.46, 0.58
    for i, d in enumerate((0.14, 0.07, 0.0)):
        x0, y0 = cx - w / 2 + d, cy - h / 2 + d
        ax.add_patch(FancyBboxPatch((x0, y0), w, h, boxstyle="square,pad=0",
                                    fc=S.WHITE, ec=color, lw=0.7, zorder=3 + i))
    for k in range(4):
        y = cy + h / 2 - 0.13 - k * 0.11
        ax.plot([cx - w / 2 + 0.08, cx + w / 2 - (0.16 if k == 3 else 0.08)], [y, y],
                color=color, lw=0.6, zorder=6)


def lock(ax, cx, cy, *, color, s=1.0):
    """A padlock centred on (cx, cy)."""
    bw, bh = 0.30 * s, 0.24 * s
    ax.add_patch(FancyBboxPatch((cx - bw / 2, cy - bh / 2), bw, bh,
                                boxstyle=f"round,pad=0,rounding_size={0.03 * s}",
                                fc=color, ec="none", zorder=6))
    ax.add_patch(Arc((cx, cy + bh / 2), 0.19 * s, 0.30 * s, theta1=0, theta2=180,
                     color=color, lw=1.1, zorder=6))
    ax.add_patch(Circle((cx, cy + 0.01 * s), 0.03 * s, fc=S.WHITE, ec="none", zorder=7))


def globe(ax, cx, cy, r, *, color):
    ax.add_patch(Circle((cx, cy), r, fc=S.WHITE, ec=color, lw=0.7, zorder=3))
    ax.add_patch(Ellipse((cx, cy), r, 2 * r, fc="none", ec=color, lw=0.6, zorder=4))
    ax.plot([cx - r, cx + r], [cy, cy], color=color, lw=0.6, zorder=4)
    ax.plot([cx, cx], [cy - r, cy + r], color=color, lw=0.6, zorder=4)


def draw_lab(ax):
    """Left: the open-source assistant on the lab's own servers."""
    x0, x1, y0, y1 = 0.08, LAB_X1, 0.08, 3.92
    box(ax, x0, y0, x1, y1, fc=S.GREEN_FILL, ec=S.GREEN, lw=0.9, r=0, ls=(0, (3, 2)), z=0)
    ax.text(x0 + 0.14, y1 - 0.22, "your lab's servers", fontsize=7.2, fontweight="bold",
            color=S.GREEN, ha="left", va="center")

    # question -> Munin -> cited answer
    row = 2.35
    bubble(ax, 0.22, row - 0.3, 1.24, row + 0.3, tail_x=0.47, ec=S.INK)
    ax.text(0.73, row, "your\nquestion", fontsize=6.5, ha="center", va="center",
            linespacing=1.0, zorder=5)

    mx0, mx1 = 1.52, 3.08
    box(ax, mx0, row - 0.52, mx1, row + 0.52, fc=S.BLUE, ec=S.BLUE, lw=0.9, r=0.12, z=3)
    ax.text((mx0 + mx1) / 2, row + 0.22, "Munin", fontsize=9, fontweight="bold",
            color=S.WHITE, ha="center", va="center", zorder=5)
    ax.text((mx0 + mx1) / 2, row - 0.2, "open-source\nLLM harness", fontsize=6.5,
            color=S.WHITE, ha="center", va="center", linespacing=1.05, zorder=5)

    bubble(ax, 3.36, row - 0.3, 4.5, row + 0.3, tail_x=4.24, ec=S.BLUE, fc=S.BLUE_FILL)
    ax.text(3.93, row + 0.09, "answer", fontsize=6.5, ha="center", va="center", zorder=5)
    ax.text(3.93, row - 0.15, "[1] [2]", fontsize=6.5, fontweight="bold", color=S.BLUE,
            ha="center", va="center", zorder=5)

    arrow(ax, (1.26, row), (mx0 - 0.04, row), color=S.INK)
    arrow(ax, (mx1 + 0.04, row), (3.34, row), color=S.BLUE)

    # the group's papers, read by Munin
    pc, px = 0.95, (mx0 + mx1) / 2
    papers(ax, px, pc, color=S.GREEN)
    arrow(ax, (px, pc + 0.48), (px, row - 0.56), color=S.GREEN, style="<|-|>")
    ax.text(px, 0.32, "your papers", fontsize=6.8, fontweight="bold", color=S.INK,
            ha="center", va="center")

    # the web: past a lock on the boundary, only if the lab allows it
    lock(ax, x1, WEB_Y, color=S.VERMILION)


def draw_web(ax):
    ly = WEB_Y
    gx = LAB_X1 + 0.62
    ax.plot([LAB_X1 + 0.2, gx - 0.24], [ly, ly], color=S.VERMILION, lw=0.9,
            ls=(0, (2, 1.5)), zorder=2)
    globe(ax, gx, ly, 0.2, color=S.VERMILION)
    ax.text(gx + 0.3, ly + 0.11, "web", fontsize=6.8, fontweight="bold",
            color=S.VERMILION, ha="left", va="center")
    ax.text(gx + 0.3, ly - 0.15, "if you allow it", fontsize=6.5,
            color=S.VERMILION, ha="left", va="center")


def draw_result(ax, acc: dict):
    """Right: correct answers by the same open model, alone and inside Munin."""
    bx0, bx1 = 4.95, 7.15                 # bar from 0 % to 100 %
    ax.text(bx0, 3.68, "Correct answers", fontsize=7.5, fontweight="bold",
            ha="left", va="center")
    ax.text(bx0, 3.4, "199 literature questions,", fontsize=6.5, color=S.MUTED,
            ha="left", va="center")
    ax.text(bx0, 3.17, "same open model", fontsize=6.5, color=S.MUTED,
            ha="left", va="center")
    rows = [2.52, 1.85, 1.18]
    for (label, _), y, c in zip(BARS, rows, [S.RULE, SKY, S.BLUE]):
        v = acc[label] * 100
        end = bx0 + (bx1 - bx0) * v / 100
        munin = label.startswith("with Munin")
        ax.text(bx0, y + 0.29, label, fontsize=6.8, fontweight="bold" if munin else "normal",
                color=S.BLUE if munin else S.INK, ha="left", va="center")
        ax.add_patch(FancyBboxPatch((bx0, y - 0.17), end - bx0, 0.34,
                                    boxstyle="square,pad=0", fc=c, ec="none", zorder=2))
        ax.text(end + 0.06, y, f"{v:.0f}%", fontsize=7.5, fontweight="bold",
                color=S.BLUE if munin else S.INK, ha="left", va="center", zorder=3)


def build(acc: dict):
    fig = plt.figure(figsize=(W_CM * S.CM, H_CM * S.CM))
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(0, W_CM)
    ax.set_ylim(0, H_CM)
    ax.set_aspect("equal")
    ax.axis("off")
    draw_lab(ax)
    draw_web(ax)
    draw_result(ax, acc)
    return fig


def read_accuracy() -> dict:
    values = F2.read_values()[BACKBONE]
    arms = [a[0] for a in F2.ARMS]
    return {label: values[arms.index(arm)]["acc"] for label, arm in BARS}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", default=str(HERE / "fig_toc"),
                    help="output path without extension")
    ap.add_argument("--formats", default=S.FORMATS)
    args = ap.parse_args()

    font = S.apply()
    fig = build(read_accuracy())
    for path in S.save(fig, args.out, args.formats):
        print(f"wrote {path}  (font: {font})")


if __name__ == "__main__":
    main()
