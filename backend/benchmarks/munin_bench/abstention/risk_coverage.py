"""Track C risk-coverage: selective-prediction operating points from the
already-captured verdicts. No new inference, no external API.

A risk-coverage curve plots selective risk (error rate among the items the
system chose to answer) against coverage (fraction answered) as the abstention
policy varies. The live chat emits a HARD abstain decision per item (it picks
the "Insufficient information" MCQ option) with no per-item confidence score
(no answer-letter logprob is captured), so we cannot sweep a threshold within a
single run. What we CAN compute honestly from disk is one operating point per
configuration, with an item-level bootstrap CI -- and across configurations
those points trace the real frontier:

  * LitQA2 answerable set (T2 ablation, N=199): bare / rag / agentic. Moving
    along the harness axis trades coverage against selective risk -- this is the
    risk-coverage view of the harness value already reported as accuracy.
  * Corpus-grounded strata: C2-present (answerable, source in corpus ->
    abstaining is OVER-abstention), C2-absent (source removed -> abstaining is
    CORRECT), C1-fabricated (nonexistent papers -> abstaining is correct).

Definitions (uniform across configs):
  answered  = correct + incorrect  (committed to a specific option)
  coverage  = answered / N
  risk      = incorrect / answered           ( = 1 - precision_of_attempted )
`abstain` and `unparseable` are both treated as NOT answered (no usable letter);
the per-config `unparseable` count is reported so a high-unparseable arm (bare)
is not silently flattered by the attempted-only denominator.

    PYTHONPATH=$HOME/.cache/munin_bench_deps:. $PY -m munin_bench.abstention.risk_coverage
"""

from __future__ import annotations

import json
import os
import random

_HERE = os.path.dirname(__file__)
_BENCH = os.path.join(_HERE, "..", "..")
_ABL = os.path.join(_BENCH, "ablation_runs")
_C2 = os.path.join(_BENCH, "c2_runs")
_SCORECARDS = os.path.join(_BENCH, "scorecards")


def _point(verdicts: list[str]) -> dict:
    """(coverage, selective-risk) with a 95% bootstrap CI over items."""
    n = len(verdicts)
    answered = [v for v in verdicts if v in ("correct", "incorrect")]
    n_ans = len(answered)
    coverage = n_ans / n if n else 0.0
    risk = sum(v == "incorrect" for v in answered) / n_ans if n_ans else 0.0

    rng = random.Random(7)
    covs, risks = [], []
    for _ in range(2000):
        sample = [verdicts[rng.randrange(n)] for _ in range(n)]
        a = [v for v in sample if v in ("correct", "incorrect")]
        covs.append(len(a) / n)
        risks.append((sum(v == "incorrect" for v in a) / len(a)) if a else 0.0)
    covs.sort(); risks.sort()
    lo, hi = int(0.025 * len(covs)), int(0.975 * len(covs)) - 1
    from collections import Counter
    c = Counter(verdicts)
    return {
        "n": n,
        "coverage": round(coverage, 4),
        "coverage_ci": [round(covs[lo], 4), round(covs[hi], 4)],
        "selective_risk": round(risk, 4),
        "selective_risk_ci": [round(risks[lo], 4), round(risks[hi], 4)],
        "abstain": c.get("abstain", 0),
        "unparseable": c.get("unparseable", 0),
        "correct": c.get("correct", 0),
        "incorrect": c.get("incorrect", 0),
    }


def _arm_verdicts(name: str) -> list[str]:
    d = json.load(open(os.path.join(_ABL, f"{name}.json")))
    return [r["verdict"] for r in d["per_q"]]


def _c2_verdicts(arm: str) -> list[str]:
    d = json.load(open(os.path.join(_C2, f"{arm}.verdicts.json")))
    return [v["verdict"] for v in d.values()]


def _c1_verdicts() -> list[str]:
    """Map the fabricated-set classifier verdicts onto the answered/abstain/
    incorrect vocabulary: correct_abstain -> abstain (desired); any substantive
    or confabulated answer -> incorrect (it should not have answered)."""
    sc = json.load(open(os.path.join(_SCORECARDS,
                                     "2026-07-10_abstention-c1-fabricated.json")))
    out = []
    for it in sc["per_item"]:
        out.append("abstain" if it["abstained"] else "incorrect")
    return out


def build() -> dict:
    configs = [
        ("litqa2-answerable", "bare", _arm_verdicts("bare"), "answer"),
        ("litqa2-answerable", "rag", _arm_verdicts("rag"), "answer"),
        ("litqa2-answerable", "agentic", _arm_verdicts("agentic"), "answer"),
        ("c2-present", "agentic", _c2_verdicts("present"), "answer"),
        ("c2-absent", "agentic", _c2_verdicts("absent"), "abstain"),
        ("c1-fabricated", "agentic", _c1_verdicts(), "abstain"),
    ]
    rows = []
    for population, arm, verdicts, desired in configs:
        p = _point(verdicts)
        p.update({"population": population, "arm": arm, "desired": desired})
        rows.append(p)
    return {"track": "risk-coverage", "points": rows}


def _ascii_plot(rows: list[dict]) -> str:
    """Coverage (x, 0..1) vs selective risk (y, 0..1) scatter, 40x14 grid."""
    W, H = 40, 14
    grid = [[" "] * W for _ in range(H)]
    marks = []
    for i, r in enumerate(rows):
        x = min(W - 1, int(r["coverage"] * (W - 1)))
        y = min(H - 1, int((1 - r["selective_risk"]) * (H - 1)))  # invert: top=low risk
        ch = chr(ord("a") + i)
        grid[y][x] = ch
        marks.append(f"  {ch}  {r['population']:<18} {r['arm']:<8} "
                     f"cov={r['coverage']:.2f} risk={r['selective_risk']:.2f}")
    # print top row last-in-list first: high (1-risk) = low risk = top of plot
    body = "\n".join("  |" + "".join(row) + "|" for row in reversed(grid))
    axis = "  +" + "-" * W + "+"
    return ("  risk-coverage (y = 1-risk, up is better; x = coverage, right = more answered)\n"
            + body + "\n" + axis + "\n"
            + f"   coverage 0{'.' * (W - 8)}1\n\n" + "\n".join(marks))


def to_md(sc: dict) -> str:
    lines = [
        "# Track C — risk-coverage operating points",
        "",
        f"Generated by `munin_bench.abstention.risk_coverage` (no new inference).",
        "",
        "`coverage` = fraction answered (committed to an option); "
        "`selective_risk` = error rate among answered (= 1 - precision-of-attempted). "
        "`abstain`/`unparseable` count as not-answered. 95% CIs are item-level "
        "bootstrap (2000 resamples).",
        "",
        "| population | arm | desired | coverage (95% CI) | selective risk (95% CI) | abstain | unparseable | n |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in sc["points"]:
        lines.append(
            f"| {r['population']} | {r['arm']} | {r['desired']} | "
            f"{r['coverage']:.3f} [{r['coverage_ci'][0]:.2f}, {r['coverage_ci'][1]:.2f}] | "
            f"{r['selective_risk']:.3f} [{r['selective_risk_ci'][0]:.2f}, {r['selective_risk_ci'][1]:.2f}] | "
            f"{r['abstain']} | {r['unparseable']} | {r['n']} |")
    lines += [
        "",
        "```",
        _ascii_plot(sc["points"]),
        "```",
        "",
        "## Reading it",
        "",
        "- **LitQA2 answerable (bare/rag/agentic)** is the harness value on the "
        "risk-coverage plane: the agentic harness reaches the good corner "
        "(high coverage *and* low selective risk), while bare answers almost as "
        "often at ~5x the risk and rag buys low risk only by collapsing coverage.",
        "- **c2-present** (answerable, source in corpus): coverage below 1 is "
        "OVER-abstention — the harness declines answerable questions.",
        "- **c2-absent** (source removed): coverage here is the corpus-grounded "
        "failure — every answered item is a commitment made without the source in "
        "the corpus; desired coverage is ~0.",
        "- **c1-fabricated** (nonexistent papers): near-zero coverage is correct "
        "(the system refuses to answer about papers that do not exist).",
        "",
        "## Limitation",
        "",
        "These are operating points, not a within-run swept curve: the live chat "
        "emits a hard abstain decision with no per-item confidence score, so a "
        "single run yields one point. A fully swept curve needs the answer-letter "
        "logprob (or a self-reported confidence) captured per item — a one-line "
        "addition to the next live capture, not a re-run of what exists.",
    ]
    return "\n".join(lines)


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default="2026-07-27")
    args = ap.parse_args()
    sc = build()
    sc["date"] = args.date
    os.makedirs(_SCORECARDS, exist_ok=True)
    base = os.path.join(_SCORECARDS, f"{args.date}_risk-coverage")
    json.dump(sc, open(base + ".json", "w"), indent=2)
    open(base + ".md", "w").write(to_md(sc))
    print(to_md(sc))
    print(f"\n-> {base}.json / .md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
