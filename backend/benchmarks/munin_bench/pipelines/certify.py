"""Track E certification gate: check a run_all scorecard against thresholds.

The 're-certification-lite' loop (master plan sec 6): provisional thresholds =
current committed baselines with a regression margin (`certification_thresholds.json`).
A model / harness / encoder swap FAILS the gate if a metric drops below (or a
max exceeds) its floor, if the harness stops beating the bare model, or if a
reliability scenario FAILs. Missing metrics are SKIPPED (that track was not run),
so it works on partial scorecards.

NOT the follow-up's validated certification (predictive-validity is Track F); this
is the operational gate that turns the scorecard history into a pass/fail check.

    $PY -m munin_bench.pipelines.certify scorecards/<date>_<tag>.json
    exit 0 = PASS, 1 = FAIL.
"""

from __future__ import annotations

import argparse
import json
import os

_DEFAULT_THRESHOLDS = os.path.join(
    os.path.dirname(__file__), "..", "..", "certification_thresholds.json")

_OPS = {">=": lambda a, b: a >= b, "<=": lambda a, b: a <= b,
        ">": lambda a, b: a > b, "<": lambda a, b: a < b}


def _metric(card: dict, task: str, system: str, metric: str):
    try:
        return card["tasks"][task][system]["metrics"][metric]["mean"]
    except (KeyError, TypeError):
        return None


def certify(card: dict, thresholds: dict) -> dict:
    checks = []

    for t in thresholds.get("absolute", []):
        val = _metric(card, t["task"], t["system"], t["metric"])
        if val is None:
            status = "SKIP"
        else:
            status = "PASS" if _OPS[t["op"]](val, t["value"]) else "FAIL"
        checks.append({"name": f"{t['task']}/{t['system']}.{t['metric']} {t['op']} {t['value']}",
                       "status": status, "actual": val, "baseline": t.get("baseline"),
                       "what": t.get("what")})

    for t in thresholds.get("harness_value", []):
        a = _metric(card, t["task"], t["system_a"], t["metric"])
        b = _metric(card, t["task"], t["system_b"], t["metric"])
        if a is None or b is None:
            status = "SKIP"
        else:
            status = "PASS" if _OPS[t["op"]](a, b) else "FAIL"
        checks.append({"name": f"{t['task']}: {t['system_a']}.{t['metric']} {t['op']} {t['system_b']}.{t['metric']}",
                       "status": status, "actual": f"{a} vs {b}", "what": t.get("what")})

    rel = (card.get("meta") or {}).get("reliability") or {}
    for scenario, allowed in thresholds.get("reliability", {}).items():
        s = (rel.get(scenario) or {}).get("status")
        if s is None:
            status = "SKIP"
        else:
            status = "PASS" if s in allowed else "FAIL"
        checks.append({"name": f"reliability/{scenario} in {allowed}",
                       "status": status, "actual": s})

    n_fail = sum(1 for c in checks if c["status"] == "FAIL")
    n_skip = sum(1 for c in checks if c["status"] == "SKIP")
    overall = "FAIL" if n_fail else "PASS"
    return {"overall": overall, "n_fail": n_fail, "n_skip": n_skip, "checks": checks}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("scorecard")
    ap.add_argument("--thresholds", default=_DEFAULT_THRESHOLDS)
    args = ap.parse_args()

    card = json.load(open(args.scorecard))
    thresholds = json.load(open(args.thresholds))
    res = certify(card, thresholds)

    print(f"=== Certification gate: {os.path.basename(args.scorecard)} ===")
    for c in res["checks"]:
        act = c.get("actual")
        act_s = f"{act:.4f}" if isinstance(act, float) else str(act)
        print(f"  [{c['status']:4s}] {c['name']}  (actual {act_s})")
    print(f"\nOVERALL: {res['overall']}  ({res['n_fail']} fail, {res['n_skip']} skipped)")
    return 1 if res["overall"] == "FAIL" else 0


if __name__ == "__main__":
    raise SystemExit(main())
