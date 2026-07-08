"""B2 - validate the MiniCheck judge against RAGTruth human labels.

Scores each RAGTruth response with MiniCheck (response support = MIN over its
sentence-level supports, matching "any unsupported claim -> hallucinated"), then
measures how well that separates grounded from hallucinated responses:

- **AUROC** of the grounded-score (min support) vs the grounded label
  (threshold-free ranking quality) - the headline.
- **Balanced accuracy** at a 0.5 support threshold (predict hallucinated when
  min support < 0.5) - the operating-point number.

Reported overall and per task_type (QA is our closest analog). Writes a
committed validation scorecard. Gate: if AUROC is poor (< ~0.70), escalate to
the 7B MiniCheck variant before building anything downstream.

    MUNIN_BENCH_ENTAILMENT_DEVICE=cpu \
    /opt/munin/services/pipeline/venv/bin/python \
        -m munin_bench.faithfulness.validate_ragtruth --n-per-cell 20
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import time

from .minicheck import MiniCheck
from . import ragtruth

GATE_AUROC = 0.70  # below this on QA -> recommend escalating to MiniCheck-7B


def auroc(scores: list[float], labels: list[int]) -> float | None:
    """AUROC with positive class = 1 (grounded). Rank-based (Mann-Whitney U);
    tie-aware. O(n^2), fine for ~100s of examples."""
    pos = [s for s, l in zip(scores, labels) if l == 1]
    neg = [s for s, l in zip(scores, labels) if l == 0]
    if not pos or not neg:
        return None
    wins = 0.0
    for p in pos:
        for n in neg:
            wins += 1.0 if p > n else (0.5 if p == n else 0.0)
    return wins / (len(pos) * len(neg))


def balanced_accuracy(preds: list[int], labels: list[int]) -> float | None:
    """preds/labels: 1 = grounded (positive). BA = mean(recall_pos, recall_neg)."""
    tp = sum(1 for p, l in zip(preds, labels) if p == 1 and l == 1)
    fn = sum(1 for p, l in zip(preds, labels) if p == 0 and l == 1)
    tn = sum(1 for p, l in zip(preds, labels) if p == 0 and l == 0)
    fp = sum(1 for p, l in zip(preds, labels) if p == 1 and l == 0)
    if (tp + fn) == 0 or (tn + fp) == 0:
        return None
    return 0.5 * (tp / (tp + fn) + tn / (tn + fp))


def _git_sha() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"]).decode().strip()
    except Exception:
        return "unknown"


def _metrics(rows: list[dict], threshold: float) -> dict:
    # positive class = grounded = NOT hallucinated
    scores = [r["min_support"] for r in rows]
    labels = [0 if r["hallucinated"] else 1 for r in rows]
    preds = [1 if s >= threshold else 0 for s in scores]
    return {
        "n": len(rows),
        "n_grounded": sum(labels),
        "n_hallucinated": len(labels) - sum(labels),
        "auroc": auroc(scores, labels),
        "balanced_accuracy": balanced_accuracy(preds, labels),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n-per-cell", type=int, default=20)
    ap.add_argument("--tasks", default="QA,Summary,Data2txt")
    ap.add_argument("--threshold", type=float, default=0.5)
    ap.add_argument("--device", default=None)
    ap.add_argument("--out", default=None, help="scorecard path (default: scorecards/<date>_...)")
    ap.add_argument("--date", default=None, help="YYYY-MM-DD stamp (Date.now unavailable in-proc)")
    args = ap.parse_args()

    tasks = tuple(t.strip() for t in args.tasks.split(",") if t.strip())
    sample = ragtruth.load_sample(n_per_cell=args.n_per_cell, task_types=tasks)
    print(f"[b2] RAGTruth sample: {len(sample)} responses "
          f"({sum(r['hallucinated'] for r in sample)} hallucinated)")

    mc = MiniCheck(device=args.device)
    print(f"[b2] judge on {mc.device}: {mc.model.name_or_path if hasattr(mc.model,'name_or_path') else 'MiniCheck'}")

    t0 = time.time()
    rows: list[dict] = []
    for i, ex in enumerate(sample):
        res = mc.score_answer(ex["response"], [ex["document"]],
                              threshold=args.threshold)
        if res["n_claims"] == 0:
            continue
        rows.append({
            "task_type": ex["task_type"],
            "hallucinated": ex["hallucinated"],
            "min_support": res["min_support"],
            "mean_support": res["mean_support"],
            "n_claims": res["n_claims"],
        })
        if (i + 1) % 25 == 0:
            print(f"  scored {i + 1}/{len(sample)} ({time.time() - t0:.0f}s)")

    overall = _metrics(rows, args.threshold)
    per_task = {}
    for t in tasks:
        tr = [r for r in rows if r["task_type"] == t]
        if tr:
            per_task[t] = _metrics(tr, args.threshold)

    qa_auroc = (per_task.get("QA") or {}).get("auroc")
    gate_ref = qa_auroc if qa_auroc is not None else overall["auroc"]
    verdict = ("flan-t5-large-sufficient" if (gate_ref or 0) >= GATE_AUROC
               else "escalate-to-7b")

    scorecard = {
        "track": "faithfulness-judge-validation",
        "dataset": "RAGTruth (test split, GitHub ungated)",
        "judge_model": getattr(mc.model, "name_or_path", "MiniCheck-Flan-T5-Large"),
        "device": mc.device,
        "git_sha": _git_sha(),
        "seed": 42,
        "n_per_cell": args.n_per_cell,
        "threshold": args.threshold,
        "elapsed_s": round(time.time() - t0, 1),
        "overall": overall,
        "per_task": per_task,
        "gate_auroc": GATE_AUROC,
        "gate_reference": "QA-auroc" if qa_auroc is not None else "overall-auroc",
        "verdict": verdict,
    }

    print("\n=== RAGTruth judge-validation ===")
    print(f"overall: AUROC={overall['auroc']:.3f}  "
          f"BA@{args.threshold}={overall['balanced_accuracy']:.3f}  (n={overall['n']})")
    for t, m in per_task.items():
        print(f"  {t:9s}: AUROC={m['auroc']:.3f}  BA={m['balanced_accuracy']:.3f}  "
              f"(n={m['n']}, {m['n_hallucinated']} hall)")
    print(f"\nGATE (>= {GATE_AUROC} on {scorecard['gate_reference']}): "
          f"{gate_ref:.3f} -> {verdict}")

    if args.out or args.date:
        stamp = args.date or "undated"
        out = args.out or os.path.join(
            os.path.dirname(__file__), "..", "..", "scorecards",
            f"{stamp}_faithfulness-judge-ragtruth.json")
        os.makedirs(os.path.dirname(out), exist_ok=True)
        with open(out, "w") as fh:
            json.dump(scorecard, fh, indent=2)
        print(f"\nscorecard -> {os.path.abspath(out)}")

    return 0 if verdict == "flan-t5-large-sufficient" else 2


if __name__ == "__main__":
    raise SystemExit(main())
