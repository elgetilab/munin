"""Figure 3: per-answer faithfulness, naive RAG (top row) against the harness
(bottom row), on three backbones.

A score is the fraction of an answer's claims that the judge (MiniCheck) finds
supported by that arm's own retrieved evidence. Only answers the judge could
score enter: a non-empty answer with captured context and at least one claim;
abstentions are scored like any other answer. Each column keeps the questions
both arms had scored, the set the scorecard's paired delta uses, and the
script checks its means against that delta. Bins: exactly 0, five right-closed
bins over (0, 1), exactly 1; scores are binned on the nearest fifth's exact
edge, so a score of 0.2 is in (0, .2] whatever its float rounding.

gpt-oss-20b is drawn hatched and lighter, as underpowered (the caption says
why): its naive RAG arm left 162 of 199 answers empty, so only 21 questions
are scored in both arms.

    python3 docs/paper-kit/figures/fig3_faithfulness_distribution.py          # pdf + png + svg + tiff
    python3 docs/paper-kit/figures/fig3_faithfulness_distribution.py --facts  # print the values
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import _style as S  # noqa: E402

SCORECARDS = HERE.parents[2] / "backend/benchmarks/scorecards"

# The faithfulness scorecards 05-RESULTS cites.
BACKBONES = {
    "Qwen3.6-35B-A3B": "2026-09-17_harness-ablation-faithfulness-qwen3.6-35b-a3b.json",
    "Qwen3.8-27B": "2026-09-16_harness-ablation-faithfulness-qwen38-recapture.json",
    "gpt-oss-20b": "2026-09-16_harness-ablation-faithfulness-gpt-oss-20b-recapture.json",
}
UNDERPOWERED = {"gpt-oss-20b"}
ARMS = {"rag": ("naive RAG", S.RULE, S.MUTED), "agentic": ("harness", S.BLUE, S.BLUE)}
BIN_LABELS = ["0", "(0,20]", "(20,40]", "(40,60]", "(60,80]", "(80,100)", "100"]
YMAX = 0.72


def bin_index(v: float) -> int:
    """0 for exactly 0, 6 for exactly 1, else the right-closed fifth it falls in.
    Rounding v*5 first puts 0.2, 0.4, ... on their bin's upper edge even when
    the float is a hair above it."""
    if v <= 0:
        return 0
    if v >= 1:
        return 6
    return min(max(math.ceil(round(v * 5, 9)), 1), 5)


def bin_position(v: float) -> float:
    """A score on the categorical x axis: bar k spans k - 0.5 to k + 0.5."""
    if v <= 0:
        return 0.0
    if v >= 1:
        return 6.0
    return 0.5 + v * 5


def read_values() -> dict:
    values = {}
    for name, filename in BACKBONES.items():
        card = json.loads((SCORECARDS / filename).read_text(encoding="utf-8"))
        per_q = card["per_question"]
        paired = sorted(set(per_q["rag"]) & set(per_q["agentic"]))
        stored = card["paired_agentic_minus_rag"]
        arms = {}
        for arm in ("rag", "agentic"):
            scores = [per_q[arm][q] for q in paired]
            counts = [0] * 7
            for v in scores:
                counts[bin_index(v)] += 1
            arms[arm] = {"mean": sum(scores) / len(scores),
                         "shares": [c / len(scores) for c in counts], "counts": counts}
        if len(paired) != stored["n"] or \
                abs(arms["agentic"]["mean"] - stored["mean_a"]) > 1e-9 or \
                abs(arms["rag"]["mean"] - stored["mean_b"]) > 1e-9:
            raise SystemExit(f"{filename}: paired n/means disagree with "
                             f"paired_agentic_minus_rag")
        values[name] = {"n": len(paired), "arms": arms, "delta": stored}
    return values


def _share(s: float) -> str:
    return "<1%" if s < 0.005 else f"{s:.0%}"


def build(values: dict):
    pct = 100                             # plotted in percent; the data stay fractions
    fig, axes = plt.subplots(2, 3, figsize=(S.FULL_WIDTH, 4.1), sharex=True, sharey=True)
    fig.subplots_adjust(left=0.065, right=0.995, top=0.945, bottom=0.115,
                        wspace=0.22, hspace=0.5)
    for col, (name, v) in enumerate(values.items()):
        weak = name in UNDERPOWERED
        for row, arm in enumerate(("rag", "agentic")):
            label, bar_colour, text_colour = ARMS[arm]
            a = v["arms"][arm]
            ax = axes[row, col]
            S.box_axes(ax, labelsize=5.8)
            ax.tick_params(labelleft=True, labelbottom=True)
            shares = [x * pct for x in a["shares"]]
            ax.bar(range(7), shares, width=0.8, color=bar_colour,
                   alpha=0.5 if weak else 1.0, hatch="////" if weak else None,
                   edgecolor=S.WHITE if weak else "none", linewidth=0, zorder=2)
            for k, s in enumerate(a["shares"]):
                if a["counts"][k]:
                    ax.text(k, s * pct + 1.5, _share(s), ha="center", va="bottom",
                            fontsize=5.4, color=S.INK, zorder=4,
                            bbox=dict(boxstyle="square,pad=0.1", fc=S.WHITE, ec="none"))
            ax.axvline(bin_position(a["mean"]), color=text_colour, ls=(0, (3, 2)),
                       lw=0.9, zorder=1)
            ax.text(0.97, 0.94, f"{label}\nmean {a['mean'] * pct:.0f}%",
                    transform=ax.transAxes, ha="right", va="top", fontsize=6.2,
                    color=text_colour, linespacing=1.15)
            ax.set_ylim(0, YMAX * pct)
            ax.set_yticks([0, 20, 40, 60])
            ax.set_xticks(range(7))
            ax.set_xticklabels(BIN_LABELS, fontsize=5.4, rotation=35, ha="right",
                               rotation_mode="anchor")
            ax.set_ylabel("Share of answers [%]", fontsize=6.2, fontweight="bold", labelpad=2)
            ax.set_xlabel("Claims supported [%]", fontsize=6.2, fontweight="bold", labelpad=2)
            ax.text(-0.2, 1.04, "abcdef"[row * 3 + col], transform=ax.transAxes,
                    fontsize=8, fontweight="bold", ha="left", va="bottom")
        axes[0, col].set_title(f"{name}  (paired n = {v['n']})", fontsize=6.8,
                               loc="left", fontweight="bold", pad=4)
    return fig


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", default=str(HERE / "fig3_faithfulness_distribution"),
                    help="output path without extension")
    ap.add_argument("--formats", default=S.FORMATS)
    ap.add_argument("--facts", action="store_true", help="print the values and exit")
    args = ap.parse_args()

    font = S.apply()
    values = read_values()
    if args.facts:
        for name, v in values.items():
            d = v["delta"]
            print(f"{name:16} n={v['n']:3}  rag {v['arms']['rag']['mean']:.3f}  "
                  f"harness {v['arms']['agentic']['mean']:.3f}  "
                  f"diff {d['mean_diff']:+.3f} [{d['ci_low']:+.3f}, {d['ci_high']:+.3f}]"
                  f" p={d['p_value_two_sided']}")
            for arm in ("rag", "agentic"):
                print(f"{'':16} {arm:8} bins {v['arms'][arm]['counts']}")
        return
    fig = build(values)
    for path in S.save(fig, args.out, args.formats):
        print(f"wrote {path}  (font: {font})")


if __name__ == "__main__":
    main()
