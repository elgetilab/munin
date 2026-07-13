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
from ..metrics.bootstrap import single_bootstrap

_HERE = os.path.dirname(__file__)
_RUNS = os.path.join(_HERE, "..", "..", "ablation_runs")


def run(arms=("rag", "agentic"), device=None, date: str | None = None) -> dict:
    mc = MiniCheck(device=device)
    out = {}
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
        out[arm] = single_bootstrap(per) if per else None
        m = out[arm]
        print(f"[d-faith:{arm}] % claims supported = "
              f"{m['mean']:.3f} [{m['ci_low']:.3f},{m['ci_high']:.3f}] (n={m['n']})"
              if m else f"[d-faith:{arm}] no scorable answers")
    sc = {"track": "harness-ablation-faithfulness", "date": date,
          "judge": "MiniCheck-Flan-T5-Large", "frac_claims_supported": out}
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
