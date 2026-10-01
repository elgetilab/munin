"""Figure 1, the architecture: one panel, the trust boundaries with every path a
request can take through them.

The layout started as the Google Drawings sketch (archive/munin_fig1_v5.svg) and is
still placed in that drawing's own pixel space (1344 x 553, y down); the axes
map it onto the full text width and crop the empty band above y = TOP, which
makes the figure about 2.6 in tall. Strokes and type are scaled by the same
factor (PT pt per px); no label is set below 14 px (5.25 pt) at print size.

The labels it takes from the code are read, not typed here: routing profiles
from router.py and egress levels from provenance.py. The tool and agent counts
from the MCP registry are not drawn; --facts prints them for the caption. The
source files are parsed with `ast`, never imported, so this needs matplotlib
and nothing from the service's own dependencies. Earlier versions are in
archive/.

    python3 docs/paper-kit/figures/fig_architecture.py            # pdf + png + svg
    python3 docs/paper-kit/figures/fig_architecture.py --facts    # print what was read
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
from matplotlib.patches import Circle, FancyArrowPatch, FancyBboxPatch, Polygon  # noqa: E402

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import _style as S  # noqa: E402

REPO = HERE.parents[2]
RETRIEVAL = REPO / "backend/retrieval"
AGENTS = ("search", "source", "compute", "deep_research")

# The sketch's canvas, in its pixels; everything above TOP is cropped.
W, H, TOP = 1344.0, 552.958, 50.0
PT = S.FULL_WIDTH * 72 / W                # points per sketch pixel (0.375)
HEIGHT = S.FULL_WIDTH * (H - TOP) / W

DASH = (4, 3)                             # trust boundaries, in units of lw
HEAD = "-|>,head_length=0.4,head_width=0.15"
HEAD_SCALE = 5.5


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


# ------------------------------------------------------------- drawing ----

def px(v):
    """A stroke width or font size given in sketch pixels, in points."""
    return v * PT


def rect(ax, x0, y0, x1, y1, *, fc=S.WHITE, ec=S.RULE, lw=1.6, r=3.84, ls="-", z=2):
    ax.add_patch(FancyBboxPatch(
        (x0, y0), x1 - x0, y1 - y0, boxstyle=f"round,pad=0,rounding_size={r}",
        fc=fc, ec=ec, lw=px(lw), ls=ls, zorder=z))


def text(ax, x, y, s, *, size=14, color=S.INK, ha="center", weight="normal",
         style="normal", family=None, z=5):
    ax.text(x, y, s, ha=ha, va="center", fontsize=px(size), color=color,
            fontweight=weight, fontstyle=style, family=family, zorder=z)


def node(ax, box, title, body=None, *, title_y=None, body_y=None, title_color=S.INK,
         title_size=18, body_size=14, mono=False, **kw):
    """A box with a bold title and an optional muted line under it."""
    x0, y0, x1, y1 = box
    rect(ax, *box, **kw)
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    text(ax, cx, title_y if title_y is not None else (cy - 11 if body else cy), title,
         size=title_size, color=title_color, weight="bold",
         family="monospace" if mono else None)
    if body:
        text(ax, cx, body_y if body_y is not None else cy + 12, body, size=body_size,
             color=S.MUTED)


def head(ax, p0, p1, *, color=S.INK, lw=2.0, both=False, z=4):
    """A straight arrow from p0 to p1 (or an arrowhead alone, laid over a line)."""
    style = HEAD.replace("-|>", "<|-|>") if both else HEAD
    ax.add_patch(FancyArrowPatch(p0, p1, arrowstyle=style, mutation_scale=HEAD_SCALE,
                                 color=color, lw=px(lw), shrinkA=0, shrinkB=0, zorder=z))


def route(ax, pts, *, color=S.INK, lw=2.0, arrow=True, both=False, z=4):
    """A right-angled polyline through pts, with an arrowhead at pts[-1] (and at
    pts[0] too when both, for a single segment)."""
    xs, ys = zip(*pts)
    n = len(pts) if not arrow else len(pts) - 1
    if n > 1:
        ax.plot(xs[:n], ys[:n], color=color, lw=px(lw), zorder=z,
                solid_joinstyle="miter", solid_capstyle="butt")
    if arrow:
        head(ax, pts[-2], pts[-1], color=color, lw=lw, both=both, z=z)


def hop(ax, x, y, *, vertical=False, bg=S.GREEN_FILL, r=5):
    """Cut the line running under a crossing, so the crossing line reads as passing
    over it. Drawn between the two (z 3.5): the under line must sit at z <= 3."""
    seg = ([x, x], [y - r, y + r]) if vertical else ([x - r, x + r], [y, y])
    ax.plot(*seg, color=bg, lw=px(6), zorder=3.5, solid_capstyle="butt")


def mixed(ax, xs, ys, *, lw=2.4, z=4):
    """Green and vermilion dashes in turn: a line fed by the corpus and by egress."""
    for color, offset in ((S.GREEN, 0), (S.VERMILION, 3)):
        ax.plot(xs, ys, color=color, lw=px(lw), ls=(offset, (3, 3)), zorder=z,
                solid_capstyle="butt", dash_capstyle="butt")


def mixed_head(ax, x, tip, *, length=6.5, half=2.8, z=5):
    """An upward arrowhead split down the middle, green on the left, vermilion on
    the right, to end a mixed() line."""
    base = tip + length
    for color, side in ((S.GREEN, -1), (S.VERMILION, 1)):
        ax.add_patch(Polygon([(x, tip), (x + side * half, base), (x, base)],
                             closed=True, fc=color, ec="none", zorder=z))


def toggle(ax, x, y):
    """A switch in the on position."""
    rect(ax, x - 13, y - 6.5, x + 13, y + 6.5, ec=S.INK, lw=1.6, r=6.5, z=5)
    ax.add_patch(Circle((x + 6.5, y), 4.2, fc=S.INK, ec="none", zorder=6))


def draw(ax, f):
    off, oa, full = f["egress"]
    top = TOP + 8                         # zone tops, under the crop

    # ------------------------------------------------------------ zones --
    rect(ax, 8.0, top, 234.7, 229.4, fc=S.GREY_FILL, ec=S.MUTED, lw=2.4, r=0,
         ls=(0, DASH), z=0)
    text(ax, 23.5, 80, "Gateway", size=19.5, color=S.MUTED, ha="left", weight="bold")
    rect(ax, 357.3, top, 1122.7, 549.3, fc=S.GREEN_FILL, ec=S.GREEN, lw=2.4, r=0,
         ls=(0, DASH), z=0)
    text(ax, 373, 530, "Cluster", size=19.5, color=S.GREEN, ha="left", weight="bold")

    # reverse tunnel between them, around the two lanes that cross it
    rect(ax, 245.7, 96, 346.3, 196, ec=S.INK, lw=1.6, r=14, z=1)
    text(ax, 296, 112, "reverse SSH", size=14, weight="bold")
    text(ax, 296, 128, "tunnel", size=14, weight="bold")

    # ---------------------------------------------- gateway and user ----
    node(ax, (34.7, 106.7, 208.0, 194.7), "Proxy", "TLS, login, API keys",
         title_y=129.7, body_y=162.6, r=2)
    node(ax, (24.0, 296.0, 218.7, 381.3), "Researcher", "browser or API",
         title_y=324.6, body_y=348.8, r=2)
    route(ax, [(96.0, 296.0), (96.0, 197.7)], lw=1.87)
    route(ax, [(146.7, 194.7), (146.7, 293.0)], color=S.MUTED, lw=1.87)

    # ------------------------------------------------------- turn row ----
    node(ax, (402.7, 119.3, 584.0, 182.0), "Router", " | ".join(f["profiles"]),
         title_y=141.8, body_y=162.6, body_size=15, r=2.74)
    node(ax, (402.7, 191.5, 584.0, 234.2), "Bare LLM", r=1.86)
    node(ax, (622.7, 109.5, 881.3, 191.5), "Model harness", "core tools + tool_search",
         title_y=139, body_y=162, title_color=S.BLUE, body_size=15,
         fc=S.BLUE_FILL, ec=S.BLUE, lw=2.4, r=3.58)
    node(ax, (946.5, 134.0, 1114.5, 169.6), "Citation audit", r=1.55)

    # question in: proxy -> router, with a trunk down to the bare LLM
    trunk = 385
    route(ax, [(208.0, 150.7), (399.7, 150.7)])
    route(ax, [(trunk, 150.7), (trunk, 212.8), (399.7, 212.8)])
    route(ax, [(584.0, 150.6), (619.7, 150.6)])
    route(ax, [(881.3, 150.5), (943.5, 150.5)])

    # answers back: streamed from the harness, branching off before the audit
    ret_x, ret_y = 368, 84.5
    route(ax, [(906.3, 150.5), (906.3, ret_y), (ret_x, ret_y), (ret_x, 141.1),
               (212.0, 141.1)], color=S.MUTED)
    text(ax, 906, 74.5, "streamed answer", size=14, color=S.MUTED, ha="right",
         weight="bold")
    # the bare LLM streams back the same way
    route(ax, [(584.0, 222.0), (603, 222.0), (603, ret_y)], color=S.MUTED, arrow=False,
          z=3)
    head(ax, (603, 100), (603, ret_y + 2), color=S.MUTED)
    hop(ax, 603, 150.6, vertical=True)
    # the audit runs once the turn is over; what it flags lands on the saved answer
    route(ax, [(1100, 134.0), (1100, ret_y), (909.3, ret_y)], color=S.MUTED)
    text(ax, 914, 100, "after the turn:", size=14, color=S.MUTED, ha="left")
    text(ax, 914, 116, "warning on saved answer", size=14, color=S.MUTED, ha="left")

    # Three lanes run between the turn row and the agents row, top to bottom:
    # other tools (black), the agent bus (blue), the deep research toggle.
    lane_tools, lane_bus, lane_toggle = 244, 256, 268

    # deep research: a third branch off the trunk, past router and harness
    dr_x = 606.7
    route(ax, [(trunk, 212.8), (trunk, lane_toggle), (dr_x, lane_toggle), (dr_x, 293)],
          z=3)
    toggle(ax, 412, lane_toggle)
    text(ax, 412, 284, "toggle", size=14)

    # other tools -> harness
    route(ax, [(455, 295.9), (455, lane_tools), (645, lane_tools), (645, 194.5)])
    hop(ax, 455, lane_toggle)

    # agent bus: one call down, one compact result up
    ax.plot([700, 1042.7], [lane_bus, lane_bus], color=S.BLUE, lw=px(2.0), zorder=4)
    for x in (760.1, 900.0, 1042.7):
        route(ax, [(x, lane_bus), (x, 294.0)], color=S.BLUE, both=True)
    route(ax, [(700, 191.5), (700, lane_bus)], color=S.BLUE)
    route(ax, [(820, lane_bus), (820, 193.5)], color=S.BLUE)
    text(ax, 692, 222, "call", size=14, color=S.BLUE, ha="right")
    text(ax, 830, 210, "compact result + outcome", size=14, color=S.BLUE, ha="left")
    text(ax, 830, 227, "not_found | grounded | thin_evidence", size=14, color=S.BLUE,
         ha="left")

    # ------------------------------------------------------ agents row ----
    node(ax, (379.0, 295.9, 482.6, 381.2), "other tools", "citations,", title_y=318,
         body_y=345, title_size=15.5, fc=S.GREY_FILL)
    text(ax, 430.8, 362, "files", size=14, color=S.MUTED)
    rect(ax, 491.3, 278.8, 1115.1, 426.6, fc=S.BLUE_FILL, ec=S.BLUE, lw=1.6, r=8.19, z=1)
    text(ax, 504.7, 407, "Agents", size=19.5, color=S.BLUE, ha="left", weight="bold")
    agents = [((528.0, 685.3), "deep_research", "research reports"),
              ((698.7, 821.4), "compute", "runs code"),
              ((834.7, 965.3), "source", "reads papers"),
              ((978.7, 1106.7), "search", "grounded read")]
    for (x0, x1), name, line in agents:
        node(ax, (x0, 296.0, x1, 381.3), name, line, title_y=322.5, body_y=355,
             title_color=S.BLUE, title_size=15, mono=True, ec=S.BLUE, lw=2.13)
    # deep research works through search and source, under the row
    dr_lane = 400
    route(ax, [(650, 381.3), (650, dr_lane), (1010, dr_lane), (1010, 383.3)],
          color=S.BLUE, z=3)
    route(ax, [(880, dr_lane), (880, 383.3)], color=S.BLUE, z=3)
    # its report comes back as an artifact in the conversation, up the left side
    rep_y = 442
    route(ax, [(600, 381.3), (600, rep_y), (ret_x, rep_y), (ret_x, 141.1)],
          color=S.MUTED, arrow=False, z=3)
    head(ax, (ret_x, 190), (ret_x, 176), color=S.MUTED)
    hop(ax, ret_x, 150.7, vertical=True)
    text(ax, 378, 458, "report, saved as artifact", size=14, color=S.MUTED, ha="left")

    # ---------------------------------------------------- resources row ----
    node(ax, (698.7, 471.2, 821.4, 540.5), "Sandbox", "no network",
         title_y=492, body_y=516)
    route(ax, [(760.1, 381.3), (760.1, 468.3)])
    hop(ax, 760.1, dr_lane, bg=S.BLUE_FILL)
    node(ax, (834.7, 469.3, 1106.7, 538.7), "Corpus", "vector database, citation graph",
         title_y=490.6, body_y=514.8, ec=S.GREEN)

    # data lane: source and search read from the corpus or, past the gate, outside
    data, gate_x = 448, 1122.7
    mixed(ax, [930, gate_x], [data, data])
    for x in (930, 1075):
        mixed(ax, [x, x], [data, 388])
        mixed_head(ax, x, 382.3)
    hop(ax, 930, dr_lane, bg=S.BLUE_FILL)
    ax.plot([1000, 1000], [469.3, data], color=S.GREEN, lw=px(2.0), zorder=4)

    # --------------------------------------------------------- egress ----
    ex0, ex1, ecx = 1157.7, 1341.7, 1249.7
    rect(ax, ex0, top, ex1, 314.0, fc=S.GREY_FILL, ec=S.VERMILION, lw=1.6, r=8.03, z=1)
    text(ax, ecx, 77, "egress level", size=16, color=S.VERMILION, weight="bold")
    text(ax, ecx, 95, "set per request", size=14, color=S.VERMILION, style="italic")
    sch, web = (112.0, 200.0), (214.0, 302.0)
    node(ax, (1168.0, sch[0], 1336.0, sch[1]), "Scholarly", "APIs",
         title_y=sch[0] + 30, body_y=sch[0] + 53, fc=S.VERMILION_FILL, ec=S.VERMILION)
    text(ax, ecx, sch[0] + 73, f"open at {oa}, {full}", size=14, color=S.VERMILION)
    node(ax, (1168.0, web[0], 1336.0, web[1]), "Web", "search, fetch",
         title_y=web[0] + 30, body_y=web[0] + 53, fc=S.VERMILION_FILL, ec=S.VERMILION)
    text(ax, ecx, web[0] + 73, f"open at {full} only", size=14, color=S.VERMILION)

    # the gate sits on the cluster boundary; behind it the lane forks
    fork = 1145
    mids = [sum(sch) / 2, sum(web) / 2]
    ax.plot([gate_x, fork, fork], [data, data, mids[0]], color=S.VERMILION, lw=px(2.0),
            zorder=4, solid_joinstyle="miter")
    for y in mids:
        route(ax, [(fork, y), (1165, y)], color=S.VERMILION)
    rect(ax, gate_x - 4, data - 14, gate_x + 4, data + 14, fc=S.VERMILION,
         ec=S.VERMILION, lw=1.0, r=2, z=5)
    text(ax, ecx, 440, "egress gate", size=14, color=S.VERMILION, weight="bold")
    text(ax, ecx, 458, f"{off}: closed, nothing leaves", size=14, color=S.VERMILION)


def build(f):
    fig = plt.figure(figsize=(S.FULL_WIDTH, HEIGHT))
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(0, W)
    ax.set_ylim(H, TOP)                   # the sketch's y runs down
    ax.set_aspect("equal")
    ax.axis("off")
    draw(ax, f)
    return fig


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", default=str(HERE / "fig_architecture"),
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
