"""Architecture figure: (a) the deployment boundary, (b) one turn through the
harness.

Every number that exists in the code is read from the code, not typed here:
tool counts from the MCP registry, router thresholds from router.py, the
tool-call cap from chat_service.py, egress levels from provenance.py, and the
backbone from its profile in backend/config/models/. The source files are
parsed with `ast`, never imported, so this needs matplotlib and nothing from
the service's own dependencies. Facts that live outside the repo (hardware,
corpus size) are in HARDWARE below.

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
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch  # noqa: E402

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import _style as S  # noqa: E402

REPO = HERE.parents[2]
RETRIEVAL = REPO / "backend/retrieval"
PRODUCTION_PROFILE = REPO / "backend/config/models/qwen3.8-27b.env"
AGENTS = ("search", "source", "compute", "deep_research")

# Not in the repo as code. Source: docs/paper-kit/01-SYSTEM.md and 03-CORPUS.md.
HARDWARE = {
    "gpus": "2× RTX 5090",
    "vps": "Hetzner CAX21",
    "corpus": "68k papers",
}


# ---------------------------------------------------------------- facts ----

def _module(path: Path) -> ast.Module:
    return ast.parse(path.read_text(), filename=str(path))


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


def _env_default(node: ast.expr) -> str:
    """The default in int(os.getenv("X", "30")) or os.getenv("X", "30")."""
    while isinstance(node, ast.Call) and not (
        isinstance(node.func, ast.Attribute) and node.func.attr == "getenv"
    ):
        node = node.args[0]
    return ast.literal_eval(node.args[1])


def read_facts() -> dict:
    router = _module(RETRIEVAL / "router.py")
    schemas = _module(RETRIEVAL / "mcp/schemas.py")
    chat = _module(RETRIEVAL / "chat_service.py")
    prov = _module(RETRIEVAL / "provenance.py")

    tools_node = _assigned(schemas, "MCP_TOOLS")
    tools = {k.value for k in tools_node.keys if isinstance(k, ast.Constant)}
    core = _string_set(_assigned(schemas, "CORE_TOOLS"))
    missing = [a for a in AGENTS if a not in tools]
    if missing:
        raise SystemExit(f"agents missing from MCP_TOOLS: {missing}")

    profile = dict(
        line.split("=", 1) for line in PRODUCTION_PROFILE.read_text().splitlines()
        if "=" in line and not line.lstrip().startswith("#")
    )
    return {
        "n_tools": len(tools),
        "n_agents": len(AGENTS),
        "n_plain": len(tools) - len(AGENTS),
        "n_core": len(core),
        "n_deferred": len(tools - core),
        "knn_k": ast.literal_eval(_assigned(router, "KNN_K")),
        "margin": ast.literal_eval(_assigned(router, "MARGIN_THRESHOLD")),
        "ood": ast.literal_eval(_assigned(router, "OOD_SIM_THRESHOLD")),
        "profiles": ast.literal_eval(_assigned(router, "PROFILES")),
        "default_profile": ast.literal_eval(_assigned(router, "DEFAULT_PROFILE")),
        "max_tool_calls": int(_env_default(_assigned(chat, "MAX_TOOL_CALLS_PER_MESSAGE"))),
        "egress": tuple(ast.literal_eval(_assigned(prov, n))
                        for n in ("EGRESS_OFF", "EGRESS_OA_ONLY", "EGRESS_FULL")),
        "model": profile["MODEL_ID"].split("/")[-1].replace("-AWQ-INT4", ""),
        "ctx_k": int(profile["VLLM_MAX_MODEL_LEN"]) // 1024,
        **HARDWARE,
    }


# ----------------------------------------------------------- primitives ----

def box(ax, x, y, w, h, title=None, lines=(), *, fc=S.WHITE, ec=S.RULE, lw=0.6,
        title_color=S.INK, size=6.2, title_size=7.0, radius=0.05, align="center",
        z=2):
    ax.add_patch(FancyBboxPatch(
        (x, y), w, h, boxstyle=f"round,pad=0,rounding_size={radius}",
        fc=fc, ec=ec, lw=lw, zorder=z))
    n = len(lines) + (1 if title else 0)
    gap = 0.118
    top = y + h / 2 + (n - 1) * gap / 2
    tx = x + w / 2 if align == "center" else x + 0.06
    k = 0
    if title:
        ax.text(tx, top, title, ha=align, va="center", fontsize=title_size,
                fontweight="bold", color=title_color, zorder=z + 1)
        k = 1
    for i, line in enumerate(lines):
        ax.text(tx, top - (k + i) * gap, line, ha=align, va="center",
                fontsize=size, color=S.MUTED if title else S.INK, zorder=z + 1)


def zone(ax, x, y, w, h, label, sub, *, fc, ec):
    ax.add_patch(FancyBboxPatch(
        (x, y), w, h, boxstyle="round,pad=0,rounding_size=0.08",
        fc=fc, ec=ec, lw=0.9, zorder=0))
    ax.text(x + 0.08, y + h - 0.1, label, ha="left", va="top", fontsize=7.6,
            fontweight="bold", color=ec)
    ax.text(x + w - 0.08, y + h - 0.1, sub, ha="right", va="top", fontsize=6.0,
            color=S.MUTED)


def arrow(ax, p0, p1, *, color=S.INK, lw=0.8, ls="-", style="-|>", rad=0.0,
          z=4, head=5):
    ax.add_patch(FancyArrowPatch(
        p0, p1, arrowstyle=style, mutation_scale=head, color=color, lw=lw,
        linestyle=ls, shrinkA=0, shrinkB=0, zorder=z,
        connectionstyle=f"arc3,rad={rad}"))


def label(ax, x, y, text, *, size=6.0, color=S.MUTED, ha="center", va="center",
          weight="normal", style="normal", z=5, bg=None):
    ax.text(x, y, text, ha=ha, va=va, fontsize=size, color=color,
            fontweight=weight, fontstyle=style, zorder=z,
            bbox=None if bg is None else dict(fc=bg, ec="none", pad=0.6))


# -------------------------------------------------------------- panel a ----

def panel_a(ax, f, y0):
    """Deployment boundary. y0 is the bottom of the panel's zones."""
    H = 2.2
    top = y0 + H
    ax.text(0.02, top + 0.26, "a", fontsize=10, fontweight="bold", va="center")
    ax.text(0.2, top + 0.26, "Deployment boundary", fontsize=8,
            fontweight="bold", va="center")
    r1, r2, rh = y0 + 1.26, y0 + 0.6, 0.48      # two rows of boxes, shared by zones

    # Users
    label(ax, 0.42, top - 0.17, "Users", size=7.2, color=S.INK, weight="bold")
    box(ax, 0.04, r1, 0.74, rh, "Browser", ["chat UI"])
    box(ax, 0.04, r2, 0.74, rh, "API client", ["OpenAI-compat."])

    # VPS: Caddy fronts everything; the gateway is the one path to the cluster.
    vx, vw = 0.9, 1.84
    zone(ax, vx, y0, vw, H, "VPS \u00b7 rented", f["vps"], fc=S.GREY_FILL, ec=S.MUTED)
    cw_ = (vw - 0.24) / 2
    c_x, g_x = vx + 0.08, vx + 0.16 + cw_
    box(ax, c_x, r1, cw_, rh, "Caddy", ["TLS, forward-auth", "subdomain routing"], size=5.8)
    box(ax, g_x, r1, cw_, rh, "api-gateway", ["API keys, quotas", "usage log"], size=5.8)
    box(ax, c_x, r2, cw_, rh, "munin-auth", ["email OTP", "session cookie"], size=5.8)
    box(ax, g_x, r2, cw_, rh, "uploads", ["tusd, hook-service", "static UIs"], size=5.8)
    label(ax, vx + vw / 2, y0 + 0.3, "holds accounts, keys, uploads;", size=5.5, style="italic")
    label(ax, vx + vw / 2, y0 + 0.16, "chat messages pass through", size=5.5, style="italic")
    arrow(ax, (0.78, r1 + rh / 2), (c_x, r1 + rh / 2))
    arrow(ax, (0.78, r2 + rh / 2), (c_x, r1 + 0.1), rad=-0.12)
    arrow(ax, (c_x + cw_, r1 + rh / 2), (g_x, r1 + rh / 2), head=4)
    arrow(ax, (c_x + cw_ / 2, r1), (c_x + cw_ / 2, r2 + rh), style="<|-|>", head=4)
    arrow(ax, (c_x + cw_ * 0.85, r1), (g_x + 0.12, r2 + rh), head=4, rad=0.0)

    # Cluster
    cx, cw = 3.7, 2.24
    zone(ax, cx, y0, cw, H, "Cluster \u00b7 on-premise", "SLURM", fc=S.GREEN_FILL, ec=S.GREEN)
    ix, iw = cx + 0.08, cw - 0.16
    half = (iw - 0.08) / 2
    box(ax, ix, r1, iw, rh, "Retrieval API = the harness",
        [f"router \u00b7 {f['n_agents']} agents \u00b7 {f['n_tools']} MCP tools \u00b7 audits"],
        fc=S.BLUE_FILL, ec=S.BLUE, lw=0.9, title_color=S.BLUE, size=5.9)
    box(ax, ix, r2, half, rh, "vLLM", [f["model"], f"{f['gpus']}, TP=2"], size=5.8)
    box(ax, ix + half + 0.08, r2, half, rh, "Qdrant \u00b7 Neo4j",
        [f"{f['corpus']}, BGE-large", "citation graph"], size=5.8)
    box(ax, ix, y0 + 0.24, half, 0.26, None, ["ingest: GROBID"], size=5.7)
    box(ax, ix + half + 0.08, y0 + 0.24, half, 0.26, None, ["sandbox, no network"], size=5.7)
    label(ax, cx + cw / 2, y0 + 0.1, "holds corpus, weights, chats, traces",
          size=5.5, style="italic")
    for bx in (ix + half / 2, ix + half * 1.5 + 0.08):
        arrow(ax, (bx, r1), (bx, r2 + rh), style="<|-|>", head=4)

    # Tunnel: requests flow in, but the connection is dialled out.
    ty = r1 + rh / 2
    x_from, x_to = g_x + cw_, ix
    for dy in (0.055, -0.055):
        ax.plot([x_from, x_to], [ty + dy] * 2, color=S.INK, lw=0.5, zorder=3)
    arrow(ax, (x_from + 0.04, ty), (x_to - 0.01, ty), lw=0.7, head=4)
    mid = (vx + vw + cx) / 2
    label(ax, mid, ty + 0.36, "reverse SSH", size=6.0, color=S.INK, weight="bold")
    label(ax, mid, ty + 0.24, "tunnel", size=6.0, color=S.INK, weight="bold")
    label(ax, mid, ty + 0.12, "requests + identity", size=5.3)
    arrow(ax, (cx - 0.04, ty - 0.2), (vx + vw + 0.04, ty - 0.2), ls=(0, (2, 1.5)),
          lw=0.6, head=4, color=S.MUTED)
    label(ax, mid, ty - 0.31, "dialled out", size=5.3)
    label(ax, mid, ty - 0.42, "by the cluster", size=5.3)
    label(ax, mid, ty - 0.58, "no inbound", size=5.6, color=S.GREEN, weight="bold")
    label(ax, mid, ty - 0.69, "path", size=5.6, color=S.GREEN, weight="bold")
    label(ax, mid, ty - 0.84, "no credentials", size=5.3)
    label(ax, mid, ty - 0.95, "cross", size=5.3)

    # Egress gate and the outside world
    gate_x = cx + cw + 0.13
    ex = gate_x + 0.14
    ew = 7.0 - ex - 0.02
    ax.add_patch(FancyBboxPatch((gate_x, y0 + 0.5), 0.06, 1.34,
                                boxstyle="round,pad=0,rounding_size=0.03",
                                fc=S.VERMILION, ec="none", zorder=3))
    label(ax, gate_x + 0.03, y0 + 0.38, "egress", size=5.8, color=S.VERMILION, weight="bold")
    label(ax, ex + ew / 2, top - 0.17, "Outside", size=7.2, color=S.INK, weight="bold")
    box(ax, ex, r1, ew, rh, "Scholarly", ["Semantic Scholar", "Unpaywall, Crossref"],
        fc=S.VERMILION_FILL, ec=S.VERMILION, size=5.4, title_size=6.6)
    box(ax, ex, r2, ew, rh, "Open web", ["search, fetch"],
        fc=S.VERMILION_FILL, ec=S.VERMILION, size=5.4, title_size=6.6)
    off, oa, full = f["egress"]
    arrow(ax, (ix + iw, r1 + rh * 0.62), (ex, r1 + rh * 0.62), color=S.VERMILION, head=4)
    ax.plot([ix + iw, gate_x + 0.03], [r1 + rh * 0.3] * 2, color=S.VERMILION, lw=0.8, zorder=4)
    arrow(ax, (gate_x + 0.03, r1 + rh * 0.3), (ex, r2 + rh * 0.6), color=S.VERMILION, head=4)
    label(ax, ex + ew / 2, r1 - 0.07, f"{oa} or {full}", size=5.3, color=S.VERMILION)
    label(ax, ex + ew / 2, r2 - 0.07, f"{full} only", size=5.3, color=S.VERMILION)
    label(ax, ex + ew / 2, y0 + 0.3, f"{off}: corpus only", size=5.3, color=S.VERMILION)
    label(ax, ex + ew / 2, y0 + 0.16, "set per request,", size=5.1, style="italic")
    label(ax, ex + ew / 2, y0 + 0.04, "not by the model", size=5.1, style="italic")


# -------------------------------------------------------------- panel b ----

def panel_b(ax, f, y0):
    """One turn. y0 is the bottom of the panel."""
    row_y, row_h = y0 + 1.5, 0.7
    top = row_y + row_h
    ax.text(0.02, top + 0.26, "b", fontsize=10, fontweight="bold", va="center")
    ax.text(0.2, top + 0.26, "One turn through the harness", fontsize=8,
            fontweight="bold", va="center")

    # Main row, left to right
    xs = [(0.04, 0.62), (0.78, 1.36), (2.26, 0.8), (3.18, 1.74), (5.04, 0.96), (6.12, 0.84)]
    (ux, uw), (rx, rw), (px, pw), (lx, lw_), (ax_, aw), (sx, sw) = xs
    box(ax, ux, row_y, uw, row_h, "User turn", ["message", "+ pinned", "persona"])
    box(ax, rx, row_y, rw, row_h, "Router", [
        "1  rules: slash commands",
        f"2  KNN vote, k={f['knn_k']}, margin {f['margin']:.2f}",
        f"3  fallback: pin, else {f['default_profile']}",
        "\u2192 " + " | ".join(f["profiles"]),
    ], align="left", size=5.7)
    box(ax, px, row_y, pw, row_h, "Prompt", ["base[pin]", "+ fragment", "[profile]"])
    box(ax, lx, row_y, lw_, row_h, "Outer model loop", [
        f"{f['model']} · {f['ctx_k']}k context",
        f"≤ {f['max_tool_calls']} tool calls per answer",
        f"sees {f['n_core']} core tools, {f['n_deferred']} more via tool_search",
    ], fc=S.BLUE_FILL, ec=S.BLUE, lw=0.9, title_color=S.BLUE, size=5.9)
    box(ax, ax_, row_y, aw, row_h, "Post-turn audit", ["phantom URL, paper,", "citation claim;",
                                                        "annotates only"], size=5.8)
    box(ax, sx, row_y, sw, row_h, "Stream", ["SSE, resumable", "always saved", "trace → eval"],
        size=5.8)
    cy = row_y + row_h / 2
    for (a, aw_), (b, _) in zip(xs, xs[1:]):
        arrow(ax, (a + aw_, cy), (b, cy))

    # Tool layer
    tool_y, tool_h = y0 + 0.2, 0.86
    bus_y = tool_y + tool_h + 0.2
    ax.plot([0.5, 6.5], [bus_y, bus_y], color=S.BLUE, lw=0.8, zorder=3)
    arrow(ax, (lx + lw_ * 0.4, bus_y), (lx + lw_ * 0.4, row_y), color=S.BLUE, head=5)
    arrow(ax, (lx + lw_ * 0.6, row_y), (lx + lw_ * 0.6, bus_y), color=S.BLUE, head=5)
    label(ax, lx + lw_ * 0.4 - 0.05, (bus_y + row_y) / 2, "envelope, handle", size=5.5,
          ha="right", color=S.BLUE)
    label(ax, lx + lw_ * 0.6 + 0.05, (bus_y + row_y) / 2, "call(args)", size=5.5,
          ha="left", color=S.BLUE)

    tools = [
        (0.04, 1.86, "search", "find and rank evidence", None),
        (2.0, 1.5, "source", "read 1..N documents", [
            "resolve → extract → 1 LLM call",
            "found · not_found · ambiguous",
            "extraction_failed · out_of_scope"]),
        (3.6, 1.18, "compute", "spec + data → code", [
            "lint → sandbox → verify",
            "verify_level: executed,", "compiled or parsed"]),
        (4.88, 1.08, "deep_research", "long-running", [
            "detached task", "plan → search →", "source → sections"]),
        (6.06, 0.9, f"{f['n_plain']} plain", "MCP tools", [
            "graph, artifacts,", "memory, sandbox,", "plans, web"]),
    ]
    for x, w, name, sub, lines in tools:
        agent = name in AGENTS
        box(ax, x, tool_y, w, tool_h, None, (), fc=S.WHITE if agent else S.GREY_FILL,
            ec=S.BLUE if agent else S.RULE, lw=0.8 if agent else 0.6)
        ax.text(x + 0.06, tool_y + tool_h - 0.1, name, fontsize=7, fontweight="bold",
                va="center", color=S.BLUE if agent else S.INK, zorder=4,
                family="monospace" if agent else None)
        ax.text(x + 0.06, tool_y + tool_h - 0.23, sub, fontsize=5.6, va="center",
                color=S.MUTED, zorder=4)
        for i, line in enumerate(lines or ()):
            ax.text(x + 0.06, tool_y + tool_h - 0.37 - i * 0.115, line, fontsize=5.6,
                    va="center", color=S.INK, zorder=4)
        arrow(ax, (x + w / 2, bus_y), (x + w / 2, tool_y + tool_h), color=S.BLUE,
              style="<|-|>", head=4, lw=0.6)
    label(ax, 0.04 + (6.06 - 0.04 - 0.08) / 2, tool_y - 0.11,
          f"{f['n_agents']} agents: each reasons in its own context and returns a compact, "
          "grounded envelope",
          size=5.5, style="italic")

    # The search ladder, drawn inside the search box under its title
    sy = tool_y + 0.24
    ax.text(0.1, tool_y + tool_h - 0.37, "ladder, widens only if short:", fontsize=5.6,
            va="center", color=S.INK, zorder=4)
    chips = [("corpus", S.WHITE, S.GREEN), ("Sem. Scholar", S.VERMILION_FILL, S.VERMILION),
             ("web", S.VERMILION_FILL, S.VERMILION)]
    x = 0.1
    for (name, fc, ec), w in zip(chips, (0.46, 0.66, 0.34)):
        box(ax, x, sy, w, 0.17, None, [name], fc=fc, ec=ec, size=5.5, radius=0.03, z=5)
        if name != "web":
            arrow(ax, (x + w + 0.01, sy + 0.085), (x + w + 0.075, sy + 0.085), head=3.5,
                  lw=0.6, z=6)
        x += w + 0.085
    gx_ = 0.1 + 0.46 + 0.0425
    ax.plot([gx_] * 2, [sy - 0.03, sy + 0.2], color=S.VERMILION, lw=1.4, zorder=7,
            solid_capstyle="butt")
    ax.text(0.1, tool_y + 0.1, "then chunk evidence; read=N via source", fontsize=5.6,
            va="center", color=S.INK, zorder=4)


# ----------------------------------------------------------------- main ----

def build(f):
    width, height = S.FULL_WIDTH, 5.66
    fig = plt.figure(figsize=(width, height))
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(0, width)
    ax.set_ylim(0, height)
    ax.set_aspect("equal")
    ax.axis("off")
    panel_a(ax, f, y0=2.98)
    panel_b(ax, f, y0=0.04)
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
