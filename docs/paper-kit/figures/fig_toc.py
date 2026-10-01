"""Table-of-contents graphic (graphical abstract) for Digital Discovery.

RSC's format: at most 8 cm wide x 4 cm high, TIFF at 600 dpi, text limited to
labels. Two halves. Left: what the paper releases, the open-source Munin
harness and its agents, running inside the lab's own boundary, reading full
text from the lab's corpus and handing the model a compact result, with the
egress gate on the boundary. Right: what it buys, LitQA2 accuracy on the
production backbone, read through fig2_arms_by_backbone so the two figures
cannot disagree. The 1-2 sentence text that goes with it is in captions.txt.

    python3 docs/paper-kit/figures/fig_toc.py         # pdf + png + svg + tiff
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch, Polygon  # noqa: E402

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import _style as S  # noqa: E402
import fig2_arms_by_backbone as F2  # noqa: E402

W_CM, H_CM = 8.0, 4.0
BACKBONE = "Qwen3.8-27B"                 # the production backbone, the headline
BARS = [("bare model", "bare\nmodel"), ("naive RAG", "naive\nRAG"),
        ("Munin", "harness,\nfull")]     # (label, the matching fig2 arm)


def box(ax, x0, y0, x1, y1, *, fc=S.WHITE, ec=S.RULE, lw=0.6, r=0.08, ls="-", z=2):
    ax.add_patch(FancyBboxPatch((x0, y0), x1 - x0, y1 - y0,
                                boxstyle=f"round,pad=0,rounding_size={r}",
                                fc=fc, ec=ec, lw=lw, ls=ls, zorder=z))


def arrow(ax, p0, p1, *, color, lw=0.8, head=5, z=4):
    ax.add_patch(FancyArrowPatch(p0, p1, arrowstyle="-|>,head_length=0.4,head_width=0.2",
                                 mutation_scale=head, color=color, lw=lw,
                                 shrinkA=0, shrinkB=0, zorder=z))


def draw_lab(ax):
    """Left half: the released harness inside the lab boundary."""
    x0, x1, y0, y1 = 0.08, 4.35, 0.08, 3.92
    box(ax, x0, y0, x1, y1, fc=S.GREEN_FILL, ec=S.GREEN, lw=0.8, r=0, ls=(0, (3, 2)), z=0)
    ax.text(x0 + 0.14, y1 - 0.2, "your lab", fontsize=6.5, fontweight="bold",
            color=S.GREEN, ha="left", va="center")

    # the harness, with the open model inside it
    hx0, hx1, hy0, hy1 = 0.3, 4.1, 2.45, 3.4
    box(ax, hx0, hy0, hx1, hy1, fc=S.BLUE_FILL, ec=S.BLUE, lw=0.9)
    ax.text((hx0 + hx1) / 2, 3.08, "Munin harness", fontsize=7.2, fontweight="bold",
            color=S.BLUE, ha="center", va="center")
    ax.text((hx0 + hx1) / 2, 2.72, "open LLM + agents", fontsize=5.6,
            color=S.MUTED, ha="center", va="center")

    # the agents
    agents = [("search", 0.3, 1.45), ("source", 1.55, 2.7), ("compute", 2.85, 4.1)]
    ay0, ay1 = 1.5, 1.95
    for name, ax0, ax1 in agents:
        box(ax, ax0, ay0, ax1, ay1, ec=S.BLUE, lw=0.8)
        ax.text((ax0 + ax1) / 2, (ay0 + ay1) / 2, name, fontsize=5.8, fontweight="bold",
                color=S.BLUE, ha="center", va="center", family="monospace")
        # compact result, up to the harness
        cx = (ax0 + ax1) / 2
        arrow(ax, (cx, ay1), (cx, hy0), color=S.BLUE, lw=0.7, head=4)
    ax.text(2.1, (ay1 + hy0) / 2, "compact result", fontsize=5.0, color=S.BLUE,
            ha="center", va="center",
            bbox=dict(boxstyle="square,pad=0.1", fc=S.GREEN_FILL, ec="none"), zorder=5)

    # the corpus, read in full by search and source
    box(ax, 0.3, 0.3, 2.7, 0.85, ec=S.GREEN, lw=0.8)
    ax.text(1.5, 0.575, "corpus", fontsize=6.2, fontweight="bold", color=S.INK,
            ha="center", va="center")
    ax.add_patch(Polygon([(0.45, 0.85), (2.55, 0.85), (2.55, ay0), (0.45, ay0)],
                         closed=True, fc=S.GREEN, ec="none", alpha=0.28, zorder=1))
    ax.text(1.5, (0.85 + ay0) / 2, "full text", fontsize=5.6, fontweight="bold",
            color=S.GREEN, ha="center", va="center")

    # open source: what the paper releases
    box(ax, 2.88, 0.36, 4.1, 0.8, fc=S.INK, ec=S.INK, r=0.22, z=3)
    ax.text(3.49, 0.58, "open source", fontsize=5.5, fontweight="bold",
            color=S.WHITE, ha="center", va="center", zorder=4)

    # the egress gate, on the boundary
    gy0, gy1 = 1.05, 1.95
    ax.add_patch(FancyBboxPatch((x1 - 0.05, gy0), 0.1, gy1 - gy0,
                                boxstyle="round,pad=0,rounding_size=0.04",
                                fc=S.VERMILION, ec="none", zorder=3))
    ax.text(x1 + 0.17, (gy0 + gy1) / 2, "egress gate", fontsize=5.0, color=S.VERMILION,
            rotation=90, ha="center", va="center", fontweight="bold")


def draw_result(ax, acc: dict):
    """Right half: accuracy of the three set-ups on the production backbone."""
    lx, bx0, bx1 = 4.75, 6.0, 7.85      # label column, bar from 0 % to 100 %
    ax.text((lx + bx1) / 2, 3.65, "LitQA2 accuracy [%]", fontsize=6.5,
            fontweight="bold", ha="center", va="center")
    rows = [2.9, 2.05, 1.2]
    colours = [S.RULE, S.RULE, S.BLUE]
    for (label, _), y, c in zip(BARS, rows, colours):
        v = acc[label] * 100
        end = bx0 + (bx1 - bx0) * v / 100
        ax.add_patch(FancyBboxPatch((bx0, y - 0.27), end - bx0, 0.54,
                                    boxstyle="square,pad=0", fc=c, ec="none", zorder=2))
        bold = label == "Munin"
        ax.text(bx0 - 0.08, y, label, fontsize=6.4 if bold else 6.0,
                fontweight="bold" if bold else "normal",
                color=S.BLUE if bold else S.INK, ha="right", va="center")
        inside = v > 50
        ax.text(end - 0.06 if inside else end + 0.06, y, f"{v:.0f}",
                fontsize=6.4, fontweight="bold", ha="right" if inside else "left",
                va="center", color=S.WHITE if inside else S.INK, zorder=3)
    ax.plot([bx0, bx0], [rows[-1] - 0.42, rows[0] + 0.42], color=S.INK, lw=0.6, zorder=3)
    ax.text((lx + bx1) / 2, 0.42, f"{BACKBONE}, 199 questions", fontsize=5.0,
            color=S.MUTED, ha="center", va="center")


def build(acc: dict):
    fig = plt.figure(figsize=(W_CM * S.CM, H_CM * S.CM))
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(0, W_CM)
    ax.set_ylim(0, H_CM)
    ax.set_aspect("equal")
    ax.axis("off")
    draw_lab(ax)
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
