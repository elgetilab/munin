"""Architecture figure: (a) the deployment boundary, (b) one turn through the
harness.

Drawn as the general pattern, not one installation: no model, GPU, hosting
product or tool count appears, so the figure stays true when those change. The
labels it takes from the code are read, not typed here: routing profiles from
router.py and egress levels from provenance.py. The tool and agent counts from
the MCP registry are not drawn; --facts prints them for the caption. The
source files are parsed with `ast`, never imported, so this needs matplotlib
and nothing from the service's own dependencies.

    python3 docs/paper-kit/figures/archive/fig_architecture_v1.py            # pdf + png + svg
    python3 docs/paper-kit/figures/archive/fig_architecture_v1.py --facts    # print what was read
"""

from __future__ import annotations

import argparse
import ast
import json
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

REPO = HERE.parents[3]
RETRIEVAL = REPO / "backend/retrieval"
AGENTS = ("search", "source", "compute", "deep_research")


# ---------------------------------------------------------------- facts ----

def _module(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _assigned(tree: ast.Module, name: str) -> ast.expr:
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id == name:
                    return node.value
    raise KeyError(f"{name} not assigned at module level")


def _string_set(node: ast.expr) -> set[str]:
    """frozenset({...}), set literal, or tuple of string constants."""
    if isinstance(node, ast.Call):
        node = node.args[0]
    return {e.value for e in node.elts if isinstance(e, ast.Constant)}


def read_facts() -> dict:
    router = _module(RETRIEVAL / "router.py")
    schemas = _module(RETRIEVAL / "mcp/schemas.py")
    prov = _module(RETRIEVAL / "provenance.py")

    tools_node = _assigned(schemas, "MCP_TOOLS")
    tools = {k.value for k in tools_node.keys if isinstance(k, ast.Constant)}
    core = _string_set(_assigned(schemas, "CORE_TOOLS"))
    missing = [a for a in AGENTS if a not in tools]
    if missing:
        raise SystemExit(f"agents missing from MCP_TOOLS: {missing}")

    return {
        "n_tools": len(tools),
        "n_agents": len(AGENTS),
        "n_plain": len(tools) - len(AGENTS),
        "n_core": len(core),
        "n_deferred": len(tools - core),
        "profiles": ast.literal_eval(_assigned(router, "PROFILES")),
        "egress": tuple(ast.literal_eval(_assigned(prov, n))
                        for n in ("EGRESS_OFF", "EGRESS_OA_ONLY", "EGRESS_FULL")),
    }


# ----------------------------------------------------------- primitives ----

def box(ax, x, y, w, h, title=None, lines=(), *, fc=S.WHITE, ec=S.RULE, lw=0.6,
        title_color=S.INK, size=6.2, title_size=7.0, radius=0.05, z=2):
    ax.add_patch(FancyBboxPatch(
        (x, y), w, h, boxstyle=f"round,pad=0,rounding_size={radius}",
        fc=fc, ec=ec, lw=lw, zorder=z))
    n = len(lines) + (1 if title else 0)
    gap = 0.125
    top = y + h / 2 + (n - 1) * gap / 2
    k = 0
    if title:
        ax.text(x + w / 2, top, title, ha="center", va="center", fontsize=title_size,
                fontweight="bold", color=title_color, zorder=z + 1)
        k = 1
    for i, line in enumerate(lines):
        ax.text(x + w / 2, top - (k + i) * gap, line, ha="center", va="center",
                fontsize=size, color=S.MUTED if title else S.INK, zorder=z + 1)


def zone(ax, x, y, w, h, text, *, fc, ec):
    """A trust boundary: dashed outline, as in data-flow diagrams."""
    ax.add_patch(FancyBboxPatch(
        (x, y), w, h, boxstyle="round,pad=0,rounding_size=0.08",
        fc=fc, ec=ec, lw=0.9, ls=(0, (4, 2)), zorder=0))
    ax.text(x + 0.08, y + h - 0.1, text, ha="left", va="top", fontsize=7.4,
            fontweight="bold", color=ec)


def badge(ax, x, y, n):
    """A numbered step marker."""
    ax.add_patch(Circle((x, y), 0.055, fc=S.INK, ec="none", zorder=6))
    ax.text(x, y - 0.004, str(n), ha="center", va="center", fontsize=5.2,
            fontweight="bold", color=S.WHITE, zorder=7)


def cross(ax, x, y, r=0.05, color=S.VERMILION):
    for dx in (r, -r):
        ax.plot([x - dx, x + dx], [y - r, y + r], color=color, lw=1.3, zorder=6,
                solid_capstyle="round")


def arrow(ax, p0, p1, *, color=S.INK, lw=0.8, ls="-", style="-|>", rad=0.0,
          z=4, head=5):
    ax.add_patch(FancyArrowPatch(
        p0, p1, arrowstyle=style, mutation_scale=head, color=color, lw=lw,
        linestyle=ls, shrinkA=0, shrinkB=0, zorder=z,
        connectionstyle=f"arc3,rad={rad}"))


def label(ax, x, y, text, *, size=6.0, color=S.MUTED, ha="center", va="center",
          weight="normal", style="normal", z=5):
    ax.text(x, y, text, ha=ha, va=va, fontsize=size, color=color,
            fontweight=weight, fontstyle=style, zorder=z)


def heading(ax, y, letter, text):
    ax.text(0.02, y, letter, fontsize=10, fontweight="bold", va="center")
    ax.text(0.2, y, text, fontsize=8, fontweight="bold", va="center")


# -------------------------------------------------------------- panel a ----

def panel_a(ax, f, y0):
    """Deployment boundary. y0 is the bottom of the panel's zones."""
    H = 1.5
    heading(ax, y0 + H + 0.24, "a", "Deployment boundary")
    rh = 0.42
    r1, r2 = y0 + 0.8, y0 + 0.16            # main row, lower row
    mid1 = r1 + rh / 2

    # Users
    box(ax, 0.04, r1, 0.76, rh, "Users", ["browser, API"])

    # Gateway: the only public entry point. One box, so its zone spans one row.
    vx, vw = 0.94, 1.5
    zone(ax, vx, r1 - 0.1, vw, y0 + H - (r1 - 0.1), "Gateway",
         fc=S.GREY_FILL, ec=S.MUTED)
    gx, gw = vx + 0.08, vw - 0.16
    box(ax, gx, r1, gw, rh, "Proxy", ["TLS, login, API keys"])
    arrow(ax, (0.8, mid1), (gx, mid1))

    # Cluster
    cx, cw = 3.5, 2.24
    zone(ax, cx, y0, cw, H, "Cluster", fc=S.GREEN_FILL, ec=S.GREEN)
    ix, iw = cx + 0.08, cw - 0.16
    box(ax, ix, r1, iw, rh, "Harness",
        ["router · agents · tools"],
        fc=S.BLUE_FILL, ec=S.BLUE, lw=0.9, title_color=S.BLUE)
    sw = (iw - 0.16) / 3
    for i, (name, line) in enumerate([("LLM", "inference"),
                                      ("Corpus", "vectors, graph"),
                                      ("Sandbox", "no network")]):
        bx = ix + i * (sw + 0.08)
        box(ax, bx, r2, sw, rh, name, [line])
        arrow(ax, (bx + sw / 2, r1), (bx + sw / 2, r2 + rh), head=4)

    # Reverse tunnel, drawn as a pipe through the boundary. (1) the cluster opens
    # the connection outward; (2) requests travel back in through it; a direct
    # inbound connection is refused.
    x_from, x_to = gx + gw, ix
    mid = (x_from + x_to) / 2
    ax.add_patch(FancyBboxPatch((x_from, mid1 - 0.065), x_to - x_from, 0.13,
                                boxstyle="round,pad=0,rounding_size=0.065",
                                fc=S.WHITE, ec=S.INK, lw=0.6, zorder=3))
    arrow(ax, (x_from + 0.06, mid1), (x_to - 0.03, mid1), lw=0.8, head=4)
    label(ax, mid, mid1 + 0.38, "reverse SSH tunnel", size=6.4, color=S.INK,
          weight="bold")
    badge(ax, x_from + 0.14, mid1 + 0.18, 2)
    label(ax, x_from + 0.23, mid1 + 0.18, "requests + identity in", size=5.8, ha="left")
    arrow(ax, (x_to - 0.04, mid1 - 0.17), (x_from + 0.06, mid1 - 0.17), lw=0.6,
          ls=(0, (2, 1.5)), head=4, color=S.MUTED)
    badge(ax, x_from + 0.14, mid1 - 0.3, 1)
    label(ax, x_from + 0.23, mid1 - 0.3, "cluster dials out", size=5.8, ha="left")
    yb = r2 + rh / 2
    arrow(ax, (x_from, yb), (cx - 0.1, yb), lw=0.6, head=4, color=S.RULE)
    cross(ax, cx - 0.02, yb)
    label(ax, mid - 0.04, yb - 0.13, "inbound refused", size=5.8, color=S.VERMILION)

    # Key, in the free corner under Users
    kx, ky = 0.06, r1 - 0.28
    rows = [
        ("request", lambda y: arrow(ax, (kx, y), (kx + 0.22, y), head=4)),
        ("connection opened", lambda y: arrow(ax, (kx, y), (kx + 0.22, y), lw=0.6,
                                               ls=(0, (2, 1.5)), head=4, color=S.MUTED)),
        ("trust boundary", lambda y: ax.add_patch(FancyBboxPatch(
            (kx, y - 0.045), 0.22, 0.09, boxstyle="round,pad=0,rounding_size=0.02",
            fc="none", ec=S.MUTED, lw=0.8, ls=(0, (3, 1.5)), zorder=3))),
        ("egress gate", lambda y: ax.add_patch(FancyBboxPatch(
            (kx + 0.08, y - 0.06), 0.05, 0.12, boxstyle="round,pad=0,rounding_size=0.02",
            fc=S.VERMILION, ec="none", zorder=3))),
    ]
    for i, (text, draw) in enumerate(rows):
        y = ky - i * 0.15
        draw(y)
        label(ax, kx + 0.28, y, text, size=5.6, ha="left")

    # Egress gate and the outside world
    gate_x = cx + cw + 0.16
    ex = gate_x + 0.18
    ew = S.FULL_WIDTH - ex - 0.04
    ax.add_patch(FancyBboxPatch((gate_x, r2), 0.06, r1 + rh - r2,
                                boxstyle="round,pad=0,rounding_size=0.03",
                                fc=S.VERMILION, ec="none", zorder=3))
    label(ax, gate_x + 0.03, r2 - 0.08, "egress", size=6.0, color=S.VERMILION,
          weight="bold")
    box(ax, ex, r1, ew, rh, "Scholarly", ["APIs"], fc=S.VERMILION_FILL, ec=S.VERMILION)
    box(ax, ex, r2, ew, rh, "Web", ["search, fetch"], fc=S.VERMILION_FILL, ec=S.VERMILION)
    off, oa, full = f["egress"]
    arrow(ax, (ix + iw, mid1), (ex, mid1), color=S.VERMILION, head=4)
    ax.plot([ix + iw, gate_x + 0.03], [r1 + 0.1] * 2, color=S.VERMILION, lw=0.8, zorder=4)
    arrow(ax, (gate_x + 0.03, r1 + 0.1), (ex, r2 + rh / 2), color=S.VERMILION, head=4)
    label(ax, ex + ew / 2, r1 - 0.08, f"{oa}, {full}", size=5.4, color=S.VERMILION)
    label(ax, ex + ew / 2, r2 - 0.08, full, size=5.4, color=S.VERMILION)


# -------------------------------------------------------------- panel b ----

def panel_b(ax, f, y0):
    """One turn. y0 is the bottom of the panel."""
    row_y, row_h = y0 + 1.06, 0.44
    heading(ax, row_y + row_h + 0.26, "b", "One turn through the harness")

    # Main row, left to right
    gap = 0.18
    steps = [
        (0.9, "Question", [], {}),
        (1.3, "Router", [" | ".join(f["profiles"])], {}),
        (1.9, "Model loop", ["calls tools as needed"],
         dict(fc=S.BLUE_FILL, ec=S.BLUE, lw=0.9, title_color=S.BLUE)),
        (1.2, "Citation audit", ["flags, never rewrites"], {}),
        (0.9, "Answer", ["streamed"], {}),
    ]
    x, spans = 0.04, []
    for w, title, lines, kw in steps:
        box(ax, x, row_y, w, row_h, title, lines, **kw)
        spans.append((x, w))
        x += w + gap
    cy = row_y + row_h / 2
    for (a, aw), (b, _) in zip(spans, spans[1:]):
        arrow(ax, (a + aw, cy), (b, cy))

    # Tool layer, reached from the model loop over one bus
    lx, lw_ = spans[2]
    tool_y, tool_h = y0 + 0.04, 0.5
    bus_y = tool_y + tool_h + 0.16
    ax.plot([0.5, 6.5], [bus_y, bus_y], color=S.BLUE, lw=0.8, zorder=3)
    arrow(ax, (lx + lw_ * 0.4, bus_y), (lx + lw_ * 0.4, row_y), color=S.BLUE, head=5)
    arrow(ax, (lx + lw_ * 0.6, row_y), (lx + lw_ * 0.6, bus_y), color=S.BLUE, head=5)
    label(ax, lx + lw_ * 0.4 - 0.05, (bus_y + row_y) / 2, "compact result", size=5.8,
          ha="right", color=S.BLUE)
    label(ax, lx + lw_ * 0.6 + 0.05, (bus_y + row_y) / 2, "call", size=5.8,
          ha="left", color=S.BLUE)

    tools = [
        (1.9, "search", None),
        (1.2, "source", "reads full text"),
        (1.2, "compute", "runs code"),
        (1.3, "deep_research", "long reports"),
        (1.08, "more tools", "graph, files, memory"),
    ]
    x = 0.04
    for w, name, line in tools:
        agent = name in AGENTS
        box(ax, x, tool_y, w, tool_h, None, (), fc=S.WHITE if agent else S.GREY_FILL,
            ec=S.BLUE if agent else S.RULE, lw=0.8 if agent else 0.6)
        ax.text(x + w / 2, tool_y + tool_h - 0.15, name, fontsize=7, fontweight="bold",
                ha="center", va="center", color=S.BLUE if agent else S.INK, zorder=4,
                family="monospace" if agent else None)
        if line:
            ax.text(x + w / 2, tool_y + 0.15, line, fontsize=6.2, ha="center",
                    va="center", color=S.MUTED, zorder=4)
        arrow(ax, (x + w / 2, bus_y), (x + w / 2, tool_y + tool_h), color=S.BLUE,
              style="<|-|>", head=4, lw=0.6)
        x += w + 0.06

    # search widens corpus -> scholarly -> web; the bar marks the egress gate.
    chips = [("corpus", S.WHITE, S.GREEN, 0.48),
             ("scholarly", S.VERMILION_FILL, S.VERMILION, 0.58),
             ("web", S.VERMILION_FILL, S.VERMILION, 0.36)]
    step = 0.1
    x = 0.04 + (1.9 - sum(c[3] for c in chips) - step * 2) / 2
    sy = tool_y + 0.07
    for i, (name, fc, ec, w) in enumerate(chips):
        box(ax, x, sy, w, 0.17, None, [name], fc=fc, ec=ec, size=5.6, radius=0.03, z=5)
        if i < len(chips) - 1:
            arrow(ax, (x + w + 0.012, sy + 0.085), (x + w + step - 0.012, sy + 0.085),
                  head=3.5, lw=0.6, z=6)
        if i == 0:
            gx_ = x + w + step / 2
            ax.plot([gx_] * 2, [sy - 0.03, sy + 0.2], color=S.VERMILION, lw=1.4,
                    zorder=7, solid_capstyle="butt")
        x += w + step


# ----------------------------------------------------------------- main ----

def build(f):
    width, height = S.FULL_WIDTH, 4.08
    fig = plt.figure(figsize=(width, height))
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(0, width)
    ax.set_ylim(0, height)
    ax.set_aspect("equal")
    ax.axis("off")
    panel_a(ax, f, y0=2.1)
    panel_b(ax, f, y0=0.04)
    return fig


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", default=str(HERE / "fig_architecture_v1"),
                    help="output path without extension")
    ap.add_argument("--formats", default="pdf,png,svg")
    ap.add_argument("--facts", action="store_true", help="print the facts read and exit")
    args = ap.parse_args()

    font = S.apply()
    facts = read_facts()
    if args.facts:
        print(json.dumps(facts, indent=2))
        return
    fig = build(facts)
    for ext in args.formats.split(","):
        fig.savefig(f"{args.out}.{ext}", metadata={"CreationDate": None}
                    if ext == "pdf" else ({"Date": None} if ext == "svg" else None))
        print(f"wrote {args.out}.{ext}  (font: {font})")


if __name__ == "__main__":
    main()
