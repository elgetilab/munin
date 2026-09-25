"""C4 container diagram (level 2) of Munin, in classic C4 notation.

A candidate alternative to panel (a) of fig_architecture: it shows what exists
and how the parts connect (typed boxes, one-way relationships labelled with a
verb and a protocol, a system boundary and a key), where panel (a) shows the
trust boundary and who opens which connection. Not in the paper yet.

Unlike fig_architecture this names the technologies, as C4 does; they are
typed in below, not read from the code.

    python3 docs/paper-kit/figures/fig_c4_containers.py            # pdf + png + svg
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.patches import (Circle, Ellipse, FancyArrowPatch,  # noqa: E402
                                FancyBboxPatch, Rectangle)

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import _style as S  # noqa: E402

# Classic C4 palette
PERSON, CONT, EXT, INK = "#08427B", "#438DD5", "#8A8A8A", "#333333"


def text3(ax, x, y, name, kind, desc, color="white"):
    ax.text(x, y + 0.17, name, ha="center", va="center", fontsize=7, fontweight="bold", color=color)
    ax.text(x, y + 0.03, kind, ha="center", va="center", fontsize=5.3, color=color)
    ax.text(x, y - 0.15, desc, ha="center", va="center", fontsize=5.5, color=color,
            linespacing=1.15)


def box(ax, x, y, w, h, name, kind, desc, fc=CONT):
    ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0,rounding_size=0.06",
                                fc=fc, ec="#2E6295" if fc == CONT else "#6B6B6B", lw=0.7))
    text3(ax, x + w / 2, y + h / 2, name, kind, desc)
    return (x, y, w, h)


def db(ax, x, y, w, h, name, kind, desc):
    e = 0.14
    ax.add_patch(Rectangle((x, y + e / 2), w, h - e, fc=CONT, ec="none"))
    ax.add_patch(Ellipse((x + w / 2, y + e / 2), w, e, fc=CONT, ec="#2E6295", lw=0.7))
    ax.plot([x, x], [y + e / 2, y + h - e / 2], color="#2E6295", lw=0.7)
    ax.plot([x + w, x + w], [y + e / 2, y + h - e / 2], color="#2E6295", lw=0.7)
    ax.add_patch(Ellipse((x + w / 2, y + h - e / 2), w, e, fc="#5A9FE0", ec="#2E6295", lw=0.7))
    text3(ax, x + w / 2, y + h / 2 - 0.04, name, kind, desc)
    return (x, y, w, h)


def rel(ax, p0, p1, lab, tech, lx=None, ly=None, rad=0.0):
    ax.add_patch(FancyArrowPatch(p0, p1, arrowstyle="-|>", mutation_scale=6, color="#707070",
                                 lw=0.7, ls=(0, (3, 2)), shrinkA=1, shrinkB=1,
                                 connectionstyle=f"arc3,rad={rad}", zorder=1))
    lx = (p0[0] + p1[0]) / 2 if lx is None else lx
    ly = (p0[1] + p1[1]) / 2 if ly is None else ly
    ax.text(lx, ly, f"{lab}\n[{tech}]", ha="center", va="center", fontsize=5.3, color=INK,
            linespacing=1.1, bbox=dict(fc="white", ec="none", pad=0.4), zorder=3)


def build():
    """Draw the diagram; returns the figure."""
    fig = plt.figure(figsize=(S.FULL_WIDTH, 4.7))
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(0, S.FULL_WIDTH)
    ax.set_ylim(0, 4.7)
    ax.set_aspect("equal")
    ax.axis("off")

    # Person
    px, py = 0.95, 3.85
    ax.add_patch(Circle((px, py + 0.52), 0.13, fc=PERSON, ec="none"))
    ax.add_patch(FancyBboxPatch((px - 0.55, py - 0.28), 1.1, 0.66,
                                boxstyle="round,pad=0,rounding_size=0.14", fc=PERSON, ec="none"))
    text3(ax, px, py + 0.05, "Researcher", "[Person]", "asks questions,\nuploads documents")

    # System boundary
    ax.add_patch(FancyBboxPatch((0.1, 0.1), 5.45, 3.2, boxstyle="round,pad=0,rounding_size=0.05",
                                fc="none", ec="#6B6B6B", lw=0.8, ls=(0, (5, 3))))
    ax.text(0.2, 0.2, "Munin", fontsize=7, fontweight="bold", color=INK, va="bottom")
    ax.text(0.2 + 0.43, 0.2, "[Software System]", fontsize=5.5, color=INK, va="bottom")

    W, H = 1.25, 0.72
    chat = box(ax, 0.3, 2.3, W, H, "Chat UI", "[Container: React SPA]", "chat, projects,\nartifacts")
    gw = box(ax, 2.1, 2.3, W, H, "Gateway", "[Container: Caddy, Python]", "TLS, login,\nAPI keys, quotas")
    har = box(ax, 3.95, 2.3, 1.4, H, "Harness API", "[Container: Python, FastAPI]",
              "router, agents,\nMCP tools")
    W2 = 1.0
    llm = box(ax, 1.35, 0.55, W2, H, "LLM server", "[Container: vLLM]", "OpenAI-compatible\ninference")
    vec = db(ax, 2.5, 0.55, W2, H, "Vector store", "[Container: Qdrant]", "paper and\ndocument chunks")
    gra = db(ax, 3.65, 0.55, W2, H, "Citation graph", "[Container: Neo4j]", "papers, authors,\ncitations")
    sbx = box(ax, 4.8, 0.55, 0.65, H, "Sandbox", "[Container:\nJupyter]", "")

    sch = box(ax, 5.8, 2.3, 1.12, H, "Scholarly APIs", "[Software System]",
              "Semantic Scholar,\nUnpaywall", fc=EXT)
    web = box(ax, 5.8, 1.25, 1.12, H, "Web search", "[Software System]", "Brave, SearXNG", fc=EXT)

    def top(b, f=0.5): return (b[0] + b[2] * f, b[1] + b[3])
    def bot(b, f=0.5): return (b[0] + b[2] * f, b[1])
    def left(b, f=0.5): return (b[0], b[1] + b[3] * f)
    def right(b, f=0.5): return (b[0] + b[2], b[1] + b[3] * f)

    rel(ax, (px, py - 0.28), top(chat), "Uses", "HTTPS")
    rel(ax, (px + 0.55, py + 0.05), top(gw, 0.4), "Calls the API", "OpenAI-compat. HTTPS",
        lx=2.3, ly=3.72)
    rel(ax, right(chat), left(gw), "Makes API calls", "JSON/HTTPS")
    rel(ax, right(gw), left(har), "Forwards requests", "reverse SSH tunnel")
    rel(ax, bot(har, 0.12), top(llm), "Generates with", "HTTP")
    rel(ax, bot(har, 0.3), top(vec), "Searches", "HTTP")
    rel(ax, bot(har, 0.5), top(gra), "Queries", "Bolt")
    rel(ax, bot(har, 0.85), top(sbx), "Runs code in", "HTTP")
    rel(ax, right(har, 0.6), left(sch, 0.6), "", "HTTPS", lx=5.58, ly=2.9)
    rel(ax, right(har, 0.25), left(web), "", "HTTPS", lx=5.58, ly=1.72)

    # Key
    kx, ky = 3.5, 4.45
    ax.text(kx, ky, "Key", fontsize=6.5, fontweight="bold", color=INK, va="center")
    items = [(PERSON, "person"), (CONT, "container"), (EXT, "external system")]
    for i, (c, t) in enumerate(items):
        x = kx + 0.35 + i * 0.95
        ax.add_patch(FancyBboxPatch((x, ky - 0.06), 0.18, 0.12,
                                    boxstyle="round,pad=0,rounding_size=0.02", fc=c, ec="none"))
        ax.text(x + 0.24, ky, t, fontsize=5.6, color=INK, va="center")
    ax.add_patch(FancyBboxPatch((kx + 0.35, ky - 0.3), 0.18, 0.12,
                                boxstyle="round,pad=0,rounding_size=0.02", fc="none", ec="#6B6B6B",
                                lw=0.7, ls=(0, (3, 1.5))))
    ax.text(kx + 0.59, ky - 0.24, "system boundary", fontsize=5.6, color=INK, va="center")
    ax.add_patch(FancyArrowPatch((kx + 1.3, ky - 0.24), (kx + 1.55, ky - 0.24), arrowstyle="-|>",
                                 mutation_scale=6, color="#707070", lw=0.7, ls=(0, (3, 2))))
    ax.text(kx + 1.6, ky - 0.24, "relationship  [protocol]", fontsize=5.6, color=INK, va="center")
    return fig


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", default=str(HERE / "fig_c4_containers"),
                    help="output path without extension")
    ap.add_argument("--formats", default="pdf,png,svg")
    args = ap.parse_args()

    font = S.apply()
    fig = build()
    for ext in args.formats.split(","):
        fig.savefig(f"{args.out}.{ext}", metadata={"CreationDate": None}
                    if ext == "pdf" else ({"Date": None} if ext == "svg" else None))
        print(f"wrote {args.out}.{ext}  (font: {font})")


if __name__ == "__main__":
    main()
