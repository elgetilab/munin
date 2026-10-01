"""Architecture figure v4: one turn drawn inside the deployment boundaries.

One panel instead of two. The turn enters through the rented gateway and the
reverse tunnel, is routed, runs in the model loop, is audited and streams back
out the same way. What the figure argues is the harness's shape, not the
topology: the agents read full text in their own context and hand the loop
only a compact result that states its guarantee (thick flow in, thin flow
out), and the choices that are not the model's to make are marked where they
are made (routing before the first model call, the egress level per request,
the audit after the answer, no network in the sandbox).

Labels read from the code as in fig_architecture: routing profiles from
router.py, egress levels from provenance.py. No counts, models or hardware.

    python3 docs/paper-kit/figures/archive/fig_architecture_v4.py         # pdf + png + svg
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.patches import FancyBboxPatch, Polygon  # noqa: E402

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))     # _style
import _style as S  # noqa: E402
import fig_architecture_v1 as A  # noqa: E402

HEIGHT = 2.88
HARNESS = dict(fc=S.BLUE_FILL, ec=S.BLUE, lw=0.9, title_color=S.BLUE)


def agent(ax, x, y, w, h, name, line, *, is_agent=True):
    A.box(ax, x, y, w, h, None, (), fc=S.WHITE if is_agent else S.GREY_FILL,
          ec=S.BLUE if is_agent else S.RULE, lw=0.8 if is_agent else 0.6)
    ax.text(x + w / 2, y + h - 0.14, name, fontsize=6.4, fontweight="bold",
            ha="center", va="center", color=S.BLUE if is_agent else S.INK, zorder=4,
            family="monospace" if is_agent else None)
    ax.text(x + w / 2, y + 0.13, line, fontsize=5.8, ha="center", va="center",
            color=S.MUTED, zorder=4)


def note(ax, x, y, text, *, ha="center", color=S.MUTED, size=5.6):
    """An italic annotation: who decides, or what never happens."""
    A.label(ax, x, y, text, size=size, color=color, ha=ha, style="italic")


def band(ax, x0, x1, y0, y1, *, w0, w1, color, alpha=0.55):
    """A tapered flow from a wide source edge (y0) to a narrow target edge (y1)."""
    ax.add_patch(Polygon([(x0 - w0 / 2, y0), (x0 + w0 / 2, y0),
                          (x1 + w1 / 2, y1), (x1 - w1 / 2, y1)],
                         closed=True, fc=color, ec="none", alpha=alpha, zorder=1.5))


def draw(ax, f):
    off, oa, full = f["egress"]

    top = HEIGHT - 0.04
    r1, rh = 1.86, 0.46                       # turn row
    mid1 = r1 + rh / 2
    r2, ah = 0.9, 0.44                        # agents row
    r3, bh = 0.08, 0.36                       # resources row
    bus = 1.57

    # ------------------------------------------------------------ zones --
    # Rented gateway: the only public entry point
    A.zone(ax, 0.04, r1 - 0.12, 1.18, top - (r1 - 0.12), "Rented gateway",
           fc=S.GREY_FILL, ec=S.MUTED)
    A.box(ax, 0.12, r1, 1.02, rh, "Proxy", ["TLS · login · API keys"])
    # Researcher, outside everything
    A.box(ax, 0.12, r2, 1.02, ah, "Researcher", ["browser or API"])
    A.arrow(ax, (0.5, r2 + ah), (0.5, r1), head=4, lw=0.7)
    A.arrow(ax, (0.76, r1), (0.76, r2 + ah), head=4, lw=0.7, color=S.MUTED)

    # On-premise cluster
    cx0, cx1 = 1.86, 5.85
    A.zone(ax, cx0, 0.02, cx1 - cx0, top - 0.02, "On-premise cluster",
           fc=S.GREEN_FILL, ec=S.GREEN)
    note(ax, cx1 - 0.08, top - 0.1, "corpus, weights, chats and traces stay here",
         ha="right", color=S.GREEN)

    # ------------------------------------------------- reverse tunnel ----
    px0, px1 = 1.12, cx0 + 0.1
    lane_in, lane_out = mid1 - 0.06, mid1 + 0.06
    ax.add_patch(FancyBboxPatch((px0, mid1 - 0.12), px1 - px0, 0.24,
                                boxstyle="round,pad=0,rounding_size=0.1",
                                fc=S.WHITE, ec=S.INK, lw=0.6, zorder=3))
    tx = (1.22 + cx0) / 2
    A.label(ax, tx, mid1 + 0.21, "reverse tunnel", size=5.4, color=S.INK, weight="bold")
    A.label(ax, tx, mid1 - 0.2, "dialled from", size=5.4)
    A.label(ax, tx, mid1 - 0.29, "the cluster", size=5.4)

    # ------------------------------------------------------- turn row ----
    rx, rw = 2.08, 0.95
    lx, lw_ = 3.25, 1.35
    ax_, aw = 4.82, 0.95
    A.box(ax, rx, r1, rw, rh, "Router", [" | ".join(f["profiles"])])
    A.box(ax, lx, r1, lw_, rh, "Model loop", ["core tools + tool_search"], **HARNESS)
    A.box(ax, ax_, r1, aw, rh, "Citation audit", ["flags, never rewrites"])
    note(ax, rx + rw / 2, r1 + rh + 0.08, "before the first model call")
    note(ax, ax_ + aw * 0.4, r1 + rh + 0.08, "after the answer")

    # question in (lower lane) ...
    A.arrow(ax, (px0 + 0.04, lane_in), (rx, lane_in), head=4, lw=0.8, z=4)
    A.arrow(ax, (rx + rw, mid1), (lx, mid1), head=4)
    A.arrow(ax, (lx + lw_, mid1), (ax_, mid1), head=4)
    # ... answer streamed back out (upper lane), over the top of the row
    ry = r1 + rh + 0.22
    xr, xd = ax_ + aw * 0.9, px1 + 0.06
    ax.plot([xr, xr, xd, xd], [r1 + rh, ry, ry, lane_out],
            color=S.MUTED, lw=0.7, zorder=4, solid_joinstyle="round")
    A.arrow(ax, (xd, lane_out), (px0 + 0.04, lane_out), head=4, lw=0.7,
            color=S.MUTED, z=4)
    A.label(ax, lx + lw_ / 2, ry + 0.08, "answer, streamed", size=5.6)

    # ------------------------------------------------------ agents row ----
    gap = 0.07
    specs = [(0.6, "more tools", "graph, files", False),
             (0.64, "compute", "runs code", True),
             (0.82, "deep_research", "long reports", True),
             (0.68, "source", "reads papers", True),
             (0.67, "search", "finds, ranks", True)]
    x, pos = rx, {}
    for w, name, line, is_agent in specs:
        agent(ax, x, r2, w, ah, name, line, is_agent=is_agent)
        pos[name] = (x, w)
        x += w + gap

    # One bus between the loop and the tools. Down: a call. Up: a compact result.
    first = pos["more tools"][0] + pos["more tools"][1] / 2
    last = pos["search"][0] + pos["search"][1] / 2
    ax.plot([first, last], [bus, bus], color=S.BLUE, lw=0.8, zorder=3)
    for name, (x, w) in pos.items():
        A.arrow(ax, (x + w / 2, bus), (x + w / 2, r2 + ah), color=S.BLUE,
                style="<|-|>", head=3.5, lw=0.6)
    xc, xu = lx + lw_ * 0.3, lx + lw_ * 0.7
    A.arrow(ax, (xc, r1), (xc, bus), color=S.BLUE, head=4, lw=0.7)
    A.arrow(ax, (xu, bus), (xu, r1), color=S.BLUE, head=4, lw=0.7)
    A.label(ax, xc - 0.05, (bus + r1) / 2, "call", size=5.6, color=S.BLUE, ha="right")
    A.label(ax, xu + 0.05, (bus + r1) / 2, "compact result + guarantee", size=5.6,
            color=S.BLUE, ha="left")

    # --------------------------------------------------- resources row ----
    sx, sw = pos["compute"]
    llm_x, llm_w = pos["deep_research"]
    kx = pos["source"][0]
    kw = pos["search"][0] + pos["search"][1] - kx
    small = dict(size=5.8, title_size=6.6)
    A.box(ax, sx, r3, sw, bh, "Sandbox", ["no network"], **small)
    A.box(ax, llm_x, r3, llm_w, bh, "LLM", ["loop and agents"], **small)
    A.box(ax, kx, r3, kw, bh, "Corpus", ["vectors · citation graph"], ec=S.GREEN, **small)
    for x, w in ((sx, sw), (llm_x, llm_w)):
        A.arrow(ax, (x + w / 2, r2), (x + w / 2, r3 + bh), head=3.5, lw=0.6,
                style="<|-|>", color=S.RULE)
    # Full text flows into the agents, and stops there.
    ax.add_patch(FancyBboxPatch((kx + 0.04, r3 + bh), kw - 0.08, r2 - (r3 + bh),
                                boxstyle="square,pad=0", fc=S.GREEN, ec="none",
                                alpha=0.22, zorder=1.5))
    ym = (r3 + bh + r2) / 2
    A.label(ax, kx + kw / 2, ym + 0.06, "full text", size=6.2, color=S.GREEN,
            weight="bold")
    note(ax, kx + kw / 2, ym - 0.07, "stays in the agent", color=S.GREEN, size=5.8)

    # --------------------------------------------------------- egress ----
    gate_x = cx1 + 0.08
    ex = gate_x + 0.16
    ew = S.FULL_WIDTH - ex - 0.04
    sch_y, web_y = r1 - 0.04, r2 - 0.14
    ax.add_patch(FancyBboxPatch((gate_x, web_y), 0.06, sch_y + rh - web_y,
                                boxstyle="round,pad=0,rounding_size=0.03",
                                fc=S.VERMILION, ec="none", zorder=3))
    A.box(ax, ex, sch_y, ew, rh, "Scholarly", ["APIs"], fc=S.VERMILION_FILL,
          ec=S.VERMILION, **small)
    A.box(ax, ex, web_y, ew, rh, "Web", ["search, fetch"], fc=S.VERMILION_FILL,
          ec=S.VERMILION, **small)
    A.label(ax, ex + ew / 2, sch_y - 0.09, f"{oa}, {full}", size=5.4, color=S.VERMILION)
    A.label(ax, ex + ew / 2, web_y - 0.09, full, size=5.4, color=S.VERMILION)
    sy = r2 + ah / 2
    ax.plot([kx + kw, gate_x + 0.03], [sy, sy], color=S.VERMILION, lw=0.8, zorder=2.5)
    A.arrow(ax, (gate_x + 0.03, sy), (ex, sch_y + rh / 2), color=S.VERMILION, head=4)
    A.arrow(ax, (gate_x + 0.03, sy), (ex, web_y + rh / 2), color=S.VERMILION, head=4)
    gx = (gate_x + S.FULL_WIDTH) / 2
    A.label(ax, gx, top - 0.1, "egress level", size=6.0, color=S.VERMILION, weight="bold")
    note(ax, gx, top - 0.21, "set per request,", color=S.VERMILION)
    note(ax, gx, top - 0.3, "not by the model", color=S.VERMILION)


def build(f):
    fig = plt.figure(figsize=(S.FULL_WIDTH, HEIGHT))
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(0, S.FULL_WIDTH)
    ax.set_ylim(0, HEIGHT)
    ax.set_aspect("equal")
    ax.axis("off")
    draw(ax, f)
    return fig


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", default=str(HERE / "fig_architecture_v4"),
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
