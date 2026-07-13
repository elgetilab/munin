"""Track D compare: paired accuracy + cost across bare / rag / agentic arms.

Reads ablation_runs/{bare,rag,agentic}.json (same question set), aligns by qid,
and reports per-arm accuracy + cost with paired-bootstrap deltas (the
harness-value signal): bare->rag = value of retrieval, rag->agentic = value of
the agentic loop.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
from collections import Counter

from ..metrics.bootstrap import paired_bootstrap

_HERE = os.path.dirname(__file__)
_RUNS = os.path.join(_HERE, "..", "..", "ablation_runs")


def _load(arm: str) -> dict:
    return {r["qid"]: r for r in json.load(open(os.path.join(_RUNS, f"{arm}.json")))["per_q"]}


def _git_sha() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "--short", "HEAD"]).decode().strip()
    except Exception:
        return "unknown"


def _cost(rows: list[dict]) -> dict:
    def mean(key):
        vals = [r[key] for r in rows if r.get(key) is not None]
        return round(sum(vals) / len(vals), 1) if vals else None
    return {
        "mean_prompt_tokens": mean("prompt_tokens"),
        "mean_completion_tokens": mean("completion_tokens"),
        "mean_elapsed_s": mean("elapsed_s"),
        "mean_tool_calls": mean("tool_calls"),
    }


def compare(arms=("bare", "rag", "agentic"), date: str | None = None) -> dict:
    data = {a: _load(a) for a in arms if os.path.exists(os.path.join(_RUNS, f"{a}.json"))}
    arms = [a for a in arms if a in data]
    qids = sorted(set.intersection(*[set(data[a]) for a in arms]))
    print(f"[d:compare] arms={arms} | paired questions={len(qids)}")

    per_arm = {}
    for a in arms:
        rows = [data[a][q] for q in qids]
        c = Counter(r["verdict"] for r in rows)
        attempted = c["correct"] + c["incorrect"]  # parseable, non-abstain
        per_arm[a] = {
            "accuracy": c["correct"] / len(rows),
            "abstain_rate": c["abstain"] / len(rows),
            "precision_of_attempted": (c["correct"] / attempted) if attempted else None,
            "verdicts": dict(c),
            "cost": _cost(rows),
        }

    def corr(a):  # per-question 0/1 correctness in qid order
        return [1.0 if data[a][q]["verdict"] == "correct" else 0.0 for q in qids]

    def _pb(hi, lo):
        d = paired_bootstrap(corr(hi), corr(lo))
        return {"delta": round(d["mean_diff"], 4),
                "ci": [round(d["ci_low"], 4), round(d["ci_high"], 4)],
                "p": round(d["p_value_two_sided"], 4)}

    deltas = {}
    present = [a for a in ("bare", "rag", "agentic") if a in arms]
    for i in range(len(present) - 1):
        lo, hi = present[i], present[i + 1]
        deltas[f"{hi}_minus_{lo}"] = _pb(hi, lo)
    if "bare" in arms and "agentic" in arms:
        deltas["agentic_minus_bare"] = _pb("agentic", "bare")

    sc = {"track": "harness-ablation", "n_paired": len(qids), "git_sha": _git_sha(),
          "date": date, "per_arm": per_arm, "deltas": deltas}
    if date:
        out = os.path.join(_HERE, "..", "..", "scorecards", f"{date}_harness-ablation.json")
        json.dump(sc, open(out, "w"), indent=2)
        print(f"[d:compare] scorecard -> {out}")

    print("\n=== Track D — harness ablation ===")
    for a in arms:
        pa = per_arm[a]; co = pa["cost"]
        print(f"  {a:8s} acc={pa['accuracy']:.3f} abstain={pa['abstain_rate']:.3f} "
              f"| tok~{co['mean_completion_tokens']}c {co['mean_elapsed_s']}s "
              f"calls={co['mean_tool_calls']}")
    print("  deltas (paired):")
    for k, v in deltas.items():
        print(f"    {k}: {v['delta']:+.3f} {v['ci']} p={v['p']}")
    return sc


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=None)
    args = ap.parse_args()
    compare(date=args.date)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
