"""Track D faithfulness-per-arm: MiniCheck grounding for RAG vs agentic.

Scores each arm's answer claims against that arm's contexts (RAG: the retrieved
top-k; agentic: the tool-result passages captured live). Bare has no contexts, so
faithfulness is N/A. Reuses the Track B MiniCheck judge (extract claim mode).

    MUNIN_BENCH_ENTAILMENT_DEVICE=cuda:0 PYTHONPATH=... $PY \
      -m munin_bench.ablation.faithfulness --date 2026-07-13
"""

from __future__ import annotations

import argparse
import json
import os

from ..faithfulness.minicheck import MiniCheck
from ..metrics.bootstrap import paired_bootstrap, single_bootstrap

_HERE = os.path.dirname(__file__)
_RUNS = os.path.join(_HERE, "..", "..", "ablation_runs")


def run(arms=("rag", "agentic"), device=None, date: str | None = None) -> dict:
    mc = MiniCheck(device=device)
    out = {}
    per_q: dict[str, dict[str, float]] = {}
    for arm in arms:
        path = os.path.join(_RUNS, f"{arm}.json")
        if not os.path.exists(path):
            continue
        rows = json.load(open(path))["per_q"]
        rows = [r for r in rows if r.get("answer") and r.get("contexts")]
        per = []
        for r in rows:
            res = mc.score_answer(r["answer"], r["contexts"], claim_mode="extract")
            if res["n_claims"]:
                per.append(res["frac_supported"])
                # Keep the per-question value keyed by qid. The master plan
                # (sec 3) specifies faithfulness "per-arm, with PAIRED bootstrap
                # CIs"; marginal per-arm CIs alone cannot deliver that, and
                # overlapping marginal CIs are not a paired test.
                per_q.setdefault(arm, {})[r["qid"]] = res["frac_supported"]
        out[arm] = single_bootstrap(per) if per else None
        m = out[arm]
        print(f"[d-faith:{arm}] % claims supported = "
              f"{m['mean']:.3f} [{m['ci_low']:.3f},{m['ci_high']:.3f}] (n={m['n']})"
              if m else f"[d-faith:{arm}] no scorable answers")
    sc = {"track": "harness-ablation-faithfulness", "date": date,
          "judge": "MiniCheck-Flan-T5-Large", "frac_claims_supported": out,
          "per_question": per_q}
    # Paired delta on the questions BOTH arms scored.
    if len(per_q) == 2:
        a_name, b_name = "rag", "agentic"
        shared = sorted(set(per_q.get(a_name, {})) & set(per_q.get(b_name, {})))
        if shared:
            av = [per_q[a_name][q] for q in shared]
            bv = [per_q[b_name][q] for q in shared]
            d = paired_bootstrap(bv, av)   # a - b = agentic - rag
            sc["paired_agentic_minus_rag"] = d
            print(f"[d-faith] PAIRED agentic-rag = {d['mean_diff']:+.4f} "
                  f"[{d['ci_low']:+.4f},{d['ci_high']:+.4f}] "
                  f"p={d['p_value_two_sided']:.3f} (n={d['n']})")
    # bare is structurally unscoreable: a parametric arm retrieves nothing, so
    # there is no evidence set to check claims against. Recorded explicitly so
    # the missing third column is not read as an oversight.
    sc["bare"] = {"scoreable": False,
                  "reason": ("no retrieved contexts (0/199 answers carry any); "
                             "faithfulness is undefined, not merely unmeasured")}
    if date:
        p = os.path.join(_HERE, "..", "..", "scorecards", f"{date}_harness-ablation-faithfulness.json")
        json.dump(sc, open(p, "w"), indent=2)
        print(f"[d-faith] scorecard -> {p}")
    return sc


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--device", default=None)
    ap.add_argument("--date", default=None)
    args = ap.parse_args()
    run(device=args.device, date=args.date)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
