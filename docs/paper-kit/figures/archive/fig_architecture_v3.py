"""Architecture figure v3: the C4 container diagram with panel (a)'s trust
story merged in.

The containers, cylinders and labelled relationships are fig_c4_containers';
what it could not show is added from fig_architecture panel (a): the two trust
boundaries (rented gateway, on-premise cluster), the reverse tunnel as a pipe
with who opens it (1) and what travels back through it (2), the refused
inbound connection, and the egress gate with the level each outside system
needs. The egress levels are read from provenance.py; the technologies are
typed in, as in the C4 script.

    python3 docs/paper-kit/figures/archive/fig_architecture_v3.py         # pdf + png + svg
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.patches import Circle, FancyArrowPatch, FancyBboxPatch  # noqa: E402

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))     # _style
import _style as S  # noqa: E402
import fig_architecture_v1 as A  # noqa: E402
import fig_c4_containers as C  # noqa: E402

HEIGHT = 5.75


def draw(ax, f):
    W, H = 1.25, 0.72
    _, oa, full = f["egress"]

    # Person
    px, py = 0.95, 5.0
    ax.add_patch(Circle((px, py + 0.52), 0.13, fc=C.PERSON, ec="none"))
    ax.add_patch(FancyBboxPatch((px - 0.55, py - 0.28), 1.1, 0.66,
                                boxstyle="round,pad=0,rounding_size=0.14", fc=C.PERSON, ec="none"))
    C.text3(ax, px, py + 0.05, "Researcher", "[Person]", "asks questions,\nuploads documents")

    # System boundary, then the two trust boundaries inside it
    ax.add_patch(FancyBboxPatch((0.1, 0.1), 5.62, 4.45, boxstyle="round,pad=0,rounding_size=0.05",
                                fc="none", ec="#6B6B6B", lw=0.8, ls=(0, (5, 3))))
    ax.text(0.2, 0.2, "Munin", fontsize=7, fontweight="bold", color=C.INK, va="bottom")
    ax.text(0.2 + 0.43, 0.2, "[Software System]", fontsize=5.5, color=C.INK, va="bottom")
    A.zone(ax, 2.45, 3.35, 1.7, 1.1, "Gateway", fc=S.GREY_FILL, ec=S.MUTED)
    cz = (1.05, 0.3, 4.45, 2.47)                 # cluster zone x, y, w, h
    A.zone(ax, *cz, "Cluster", fc=S.GREEN_FILL, ec=S.GREEN)

    chat = C.box(ax, 0.3, 3.5, W, H, "Chat UI", "[Container: React SPA]",
                 "chat, projects,\nartifacts")
    gw = C.box(ax, 2.67, 3.45, W, H, "Gateway", "[Container: Caddy, Python]",
               "TLS, login,\nAPI keys, quotas")
    har = C.box(ax, 2.5, 1.85, 1.6, H, "Harness API", "[Container: Python, FastAPI]",
                "router, agents,\nMCP tools")
    llm = C.box(ax, 1.17, 0.45, 1.0, H, "LLM server", "[Container: vLLM]",
                "OpenAI-compatible\ninference")
    vec = C.db(ax, 2.29, 0.45, 1.0, H, "Vector store", "[Container: Qdrant]",
               "paper and\ndocument chunks")
    gra = C.db(ax, 3.41, 0.45, 1.0, H, "Citation graph", "[Container: Neo4j]",
               "papers, authors,\ncitations")
    sbx = C.box(ax, 4.53, 0.45, 0.89, H, "Sandbox", "[Container: Jupyter]", "no network")
    sch = C.box(ax, 5.85, 2.5, 1.1, H, "Scholarly APIs", "[Software System]",
                "Semantic Scholar,\nUnpaywall", fc=C.EXT)
    web = C.box(ax, 5.85, 1.35, 1.1, H, "Web search", "[Software System]",
                "Brave, SearXNG", fc=C.EXT)

    def top(b, f=0.5): return (b[0] + b[2] * f, b[1] + b[3])
    def bot(b, f=0.5): return (b[0] + b[2] * f, b[1])
    def left(b, f=0.5): return (b[0], b[1] + b[3] * f)
    def right(b, f=0.5): return (b[0] + b[2], b[1] + b[3] * f)

    C.rel(ax, (px, py - 0.28), top(chat), "Uses", "HTTPS", ly=4.39)
    C.rel(ax, (px + 0.55, py + 0.05), top(gw, 0.75), "Calls the API", "OpenAI-compat. HTTPS",
          lx=2.55, ly=4.72)
    C.rel(ax, right(chat), left(gw), "Makes API calls", "JSON/HTTPS", lx=2.06)
    C.rel(ax, bot(har, 0.12), top(llm), "Generates with", "HTTP")
    C.rel(ax, bot(har, 0.37), top(vec), "Searches", "HTTP")
    C.rel(ax, bot(har, 0.63), top(gra), "Queries", "Bolt")
    C.rel(ax, bot(har, 0.88), top(sbx), "Runs code in", "HTTP")

    # Reverse tunnel between gateway and harness: (1) the cluster dials out,
    # (2) requests and the user's identity come back in through it.
    tx, y_top, y_bot = top(har)[0], gw[1], har[1] + har[3]
    ax.add_patch(FancyBboxPatch((tx - 0.065, y_bot), 0.13, y_top - y_bot,
                                boxstyle="round,pad=0,rounding_size=0.065",
                                fc=S.WHITE, ec=S.INK, lw=0.6, zorder=3))
    A.arrow(ax, (tx, y_top - 0.05), (tx, y_bot + 0.03), lw=0.8, head=4)
    A.arrow(ax, (tx - 0.17, y_bot + 0.02), (tx - 0.17, y_top - 0.04), lw=0.8, head=4,
            color=S.GREEN)
    ym = 2.93
    A.label(ax, tx + 0.14, 3.17, "reverse SSH tunnel", size=6.2, color=S.INK,
            weight="bold", ha="left")
    A.badge(ax, tx + 0.2, ym, 2)
    A.label(ax, tx + 0.29, ym, "requests + identity in", size=5.8, ha="left")
    A.badge(ax, tx - 0.3, ym, 1)
    A.label(ax, tx - 0.39, ym, "cluster dials out", size=5.8, ha="right")

    yr = 2.05
    A.arrow(ax, (0.25, yr), (cz[0] - 0.1, yr), lw=0.6, head=4, color=S.RULE)
    A.cross(ax, cz[0] - 0.02, yr)
    A.label(ax, 0.6, yr - 0.13, "inbound refused", size=5.8, color=S.VERMILION)

    # Egress gate on the cluster's edge; each outside system needs a level.
    gx = cz[0] + cz[2] + 0.07
    ax.add_patch(FancyBboxPatch((gx, 1.45), 0.06, 1.7, boxstyle="round,pad=0,rounding_size=0.03",
                                fc=S.VERMILION, ec="none", zorder=4))
    A.label(ax, gx + 0.03, 3.25, "egress", size=6.0, color=S.VERMILION, weight="bold")
    C.rel(ax, right(har, 0.75), left(sch, 0.5), "Looks up", "HTTPS", lx=4.95, ly=2.6,
          color=S.VERMILION)
    C.rel(ax, right(har, 0.25), left(web, 0.5), "Searches", "HTTPS", lx=4.95, ly=1.88,
          color=S.VERMILION)
    A.label(ax, sch[0] + sch[2] / 2, sch[1] - 0.09, f"{oa}, {full}", size=5.4,
            color=S.VERMILION)
    A.label(ax, web[0] + web[2] / 2, web[1] - 0.09, full, size=5.4, color=S.VERMILION)

    # Key
    kx, ky = 3.35, 5.55
    ax.text(kx, ky, "Key", fontsize=6.5, fontweight="bold", color=C.INK, va="center")
    col = [kx + 0.35, kx + 1.35, kx + 2.4]

    def swatch(x, y, **kw):
        ax.add_patch(FancyBboxPatch((x, y - 0.06), 0.18, 0.12,
                                    boxstyle="round,pad=0,rounding_size=0.02", **kw))

    def key_text(x, y, t):
        ax.text(x + 0.24, y, t, fontsize=5.6, color=C.INK, va="center")

    for x, c, t in zip(col, (C.PERSON, C.CONT, C.EXT), ("person", "container", "external system")):
        swatch(x, ky, fc=c, ec="none")
        key_text(x, ky, t)
    y = ky - 0.22
    swatch(col[0], y, fc="none", ec="#6B6B6B", lw=0.7, ls=(0, (3, 1.5)))
    key_text(col[0], y, "system boundary")
    swatch(col[1], y, fc=S.GREEN_FILL, ec=S.GREEN, lw=0.8, ls=(0, (3, 1.5)))
    key_text(col[1], y, "trust boundary")
    ax.add_patch(FancyBboxPatch((col[2] + 0.07, y - 0.06), 0.05, 0.12,
                                boxstyle="round,pad=0,rounding_size=0.02", fc=S.VERMILION,
                                ec="none"))
    key_text(col[2], y, "egress gate  [level]")
    y = ky - 0.44
    ax.add_patch(FancyArrowPatch((col[0], y), (col[0] + 0.2, y), arrowstyle="-|>",
                                 mutation_scale=6, color="#707070", lw=0.7, ls=(0, (3, 2))))
    key_text(col[0], y, "relationship  [protocol]")
    A.arrow(ax, (col[2], y), (col[2] + 0.2, y), lw=0.8, head=4, color=S.GREEN)
    key_text(col[2], y, "connection opened")


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
    ap.add_argument("--out", default=str(HERE / "fig_architecture_v3"),
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
