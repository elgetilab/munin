"""Figure 2: LitQA2 accuracy of the four arms on the three backbones.

Reads the committed harness-ablation scorecards; no number is typed here except
PaperQA2's published accuracy, which is quoted, not re-measured. Accuracy is
correct / all 199 questions (abstain and unparseable count as not correct), the
same 199 on every point; the bars are 95 % Wilson intervals from the verdict
counts. Points, not lines: the arms are separate set-ups, not a scale.

The naive RAG and bare-model arms come from each backbone's full-egress
scorecard; the egress-off scorecard carries copies of them, which the script
checks are identical, and contributes only its harness arm.

    python3 docs/paper-kit/figures/fig2_arms_by_backbone.py          # pdf + png + svg + tiff
    python3 docs/paper-kit/figures/fig2_arms_by_backbone.py --facts  # print the values
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
N_QUESTIONS = 199

# Per backbone: full-egress scorecard, egress-off scorecard (the files 05-RESULTS cites).
BACKBONES = {
    "Qwen3.6-35B-A3B": ("2026-09-17_harness-ablation-qwen3.6-35b-a3b.json",
                        "2026-09-17_harness-ablation-agentic-egressoff-qwen3.6-35b-a3b.json"),
    "Qwen3.8-27B": ("2026-08-26_harness-ablation.json",
                    "2026-09-15_harness-ablation-agentic-egressoff.json"),
    "gpt-oss-20b": ("2026-09-16_harness-ablation-gpt-oss-20b.json",
                    "2026-09-17_harness-ablation-agentic-egressoff-gpt-oss-20b.json"),
}
# Okabe-Ito: the two Qwen backbones in blues (one lab), gpt-oss in orange and
# hollow (another lab).
COLOURS = {"Qwen3.6-35B-A3B": "#0072B2", "Qwen3.8-27B": "#56B4E9", "gpt-oss-20b": "#E69F00"}
HOLLOW = {"gpt-oss-20b"}

# (label, scorecard, arm key) in plotting order.
ARMS = [("bare\nmodel", "full", "bare"),
        ("naive\nRAG", "full", "rag"),
        ("harness,\negress off", "off", "agentic"),
        ("harness,\nfull", "full", "agentic")]

PAPERQA2_ACCURACY = 0.660  # Skarlinski et al. 2024, published; trained on LitQA2


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    p = k / n
    centre = (p + z * z / (2 * n)) / (1 + z * z / n)
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return centre - half, centre + half


def _arm(card: dict, key: str, where: str) -> dict:
    arm = card["per_arm"][key]
    n = sum(arm["verdicts"].values())
    if card["n_paired"] != N_QUESTIONS or n != N_QUESTIONS:
        raise SystemExit(f"{where} {key}: n_paired {card['n_paired']}, verdicts {n}, "
                         f"expected {N_QUESTIONS}")
    k = arm["verdicts"]["correct"]
    if abs(k / n - arm["accuracy"]) > 1e-9:
        raise SystemExit(f"{where} {key}: accuracy {arm['accuracy']} != {k}/{n}")
    empty = arm.get("empty_kinds", {}).get("no_final_message", 0)
    return {"acc": k / n, "ci": wilson(k, n), "correct": k, "empty": empty}


def read_values() -> dict:
    values = {}
    for name, (full_file, off_file) in BACKBONES.items():
        cards = {"full": json.loads((SCORECARDS / full_file).read_text(encoding="utf-8")),
                 "off": json.loads((SCORECARDS / off_file).read_text(encoding="utf-8"))}
        for key in ("rag", "bare"):          # the off file's copies must not drift
            if cards["off"]["per_arm"][key]["verdicts"] != cards["full"]["per_arm"][key]["verdicts"]:
                raise SystemExit(f"{off_file}: {key} differs from {full_file}")
        values[name] = [_arm(cards[src], key, f"{name} {src}") for _, src, key in ARMS]
    return values


def build(values: dict):
    pct = 100                             # plotted in percent; the data stay fractions
    fig = plt.figure(figsize=(S.COLUMN_WIDTH, 2.6))
    ax = fig.add_axes([0.13, 0.215, 0.84, 0.765])
    S.box_axes(ax, labelsize=6.2)

    ax.axhline(PAPERQA2_ACCURACY * pct, color=S.RULE, ls=(0, (1, 1.5)), lw=0.8, zorder=1)
    ax.text(-0.42, PAPERQA2_ACCURACY * pct + 1.2,
            f"PaperQA2, published ({PAPERQA2_ACCURACY * pct:.1f}%):\n"
            "trained on LitQA2, not re-measured",
            ha="left", va="bottom", fontsize=5.6, color=S.MUTED, linespacing=1.15)

    offsets = dict(zip(BACKBONES, (-0.2, 0.0, 0.2)))
    for name, arms in values.items():
        c = COLOURS[name]
        for i, v in enumerate(arms):
            x = i + offsets[name]
            lo, hi = v["ci"]
            ax.plot([x, x], [lo * pct, hi * pct], color=c, lw=0.9, solid_capstyle="butt", zorder=2)
            ax.plot(x, v["acc"] * pct, "o", ms=3.8, mew=0.9, color=c, zorder=3,
                    mfc=S.WHITE if name in HOLLOW else c,
                    label=name if i == 0 else None)

    # gpt-oss on naive RAG mostly returned nothing: say so where the point is.
    rag = values["gpt-oss-20b"][1]
    if rag["empty"]:
        ax.annotate(f"{rag['empty']}/{N_QUESTIONS} empty,\nscored wrong",
                    xy=(1 + offsets["gpt-oss-20b"], rag["acc"] * pct),
                    xytext=(1.3, 3.5), fontsize=5.6, color=S.MUTED, ha="left",
                    va="bottom", linespacing=1.15,
                    arrowprops=dict(arrowstyle="-", color=S.RULE, lw=0.5,
                                    shrinkA=1, shrinkB=2.5))

    ax.set_xlim(-0.5, len(ARMS) - 0.5)
    ax.set_xticks(range(len(ARMS)))
    ax.set_xticklabels([a[0] for a in ARMS], linespacing=1.1)
    ax.set_ylim(0, 100)
    ax.set_yticks(range(0, 101, 20))
    ax.set_ylabel("Accuracy [%]", fontsize=6.6, fontweight="bold")
    ax.set_xlabel("Configuration", fontsize=6.6, fontweight="bold", labelpad=3)
    ax.legend(loc="upper left", frameon=False, fontsize=6.0, handletextpad=0.3,
              borderaxespad=0.2, labelspacing=0.3)
    return fig


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", default=str(HERE / "fig2_arms_by_backbone"),
                    help="output path without extension")
    ap.add_argument("--formats", default=S.FORMATS)
    ap.add_argument("--facts", action="store_true", help="print the values and exit")
    args = ap.parse_args()

    font = S.apply()
    values = read_values()
    if args.facts:
        for name, arms in values.items():
            for (label, _, _), v in zip(ARMS, arms):
                lo, hi = v["ci"]
                print(f"{name:16} {label.replace(chr(10), ' '):20} {v['acc']:.3f}"
                      f"  [{lo:.3f}, {hi:.3f}]  empty {v['empty']}")
        return
    fig = build(values)
    for path in S.save(fig, args.out, args.formats):
        print(f"wrote {path}  (font: {font})")


if __name__ == "__main__":
    main()
