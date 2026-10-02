"""Architecture figure v2, for comparison: panel (a) of fig_architecture beside
the C4 container diagram of fig_c4_containers.

Both are drawn at their own scale, full width each, so the figure is two text
widths wide: a side-by-side view for choosing between them, not a layout for
the paper. The drawing code is the two scripts' own, so this cannot drift from
either.

    python3 docs/paper-kit/figures/archive/fig_architecture_v2.py         # pdf + png + svg
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))     # _style
import _style as S  # noqa: E402
import fig_architecture_v1 as A  # noqa: E402
import fig_c4_containers as C  # noqa: E402

GAP = 0.3                    # between the two panels, in inches
HEAD = 0.36                  # room above the C4 diagram for its heading
HEIGHT = C.HEIGHT + HEAD


def build(f):
    width = 2 * S.FULL_WIDTH + GAP
    fig = plt.figure(figsize=(width, HEIGHT))
    axes = []
    for i in range(2):
        ax = fig.add_axes([i * (S.FULL_WIDTH + GAP) / width, 0, S.FULL_WIDTH / width, 1])
        ax.set_xlim(0, S.FULL_WIDTH)
        ax.set_ylim(0, HEIGHT)
        ax.set_aspect("equal")
        ax.axis("off")
        axes.append(ax)

    # Panel (a) puts its heading 1.74 in above y0; align it with the C4 heading.
    head_y = C.HEIGHT + HEAD / 2
    A.panel_a(axes[0], f, y0=head_y - 1.74)
    C.draw(axes[1])
    A.heading(axes[1], head_y, "b", "Containers (C4 level 2)")

    divider = (S.FULL_WIDTH + GAP / 2) / width
    fig.add_artist(plt.Line2D([divider] * 2, [0.03, 0.97], color=S.RULE, lw=0.5,
                              ls=(0, (2, 2))))
    return fig


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", default=str(HERE / "fig_architecture_v2"),
                    help="output path without extension")
    ap.add_argument("--formats", default="pdf,png,svg")
    args = ap.parse_args()

    font = S.apply()
    fig = build(A.read_facts())
    for ext in args.formats.split(","):
        fig.savefig(f"{args.out}.{ext}", metadata={"CreationDate": None}
                    if ext == "pdf" else ({"Date": None} if ext == "svg" else None))
        print(f"wrote {args.out}.{ext}  (font: {font})")


if __name__ == "__main__":
    main()
