"""Track C2b: paired shadow-corpus abstention (over-abstention + correct-abstention).

Runs the LitQA2 MCQ answer track on the frozen 50-question set against two arms:
  - PRESENT: the live corpus (papers_bge, :8080) - source paper IS retrievable.
  - ABSENT : the shadow corpus (papers_shadow, :8081) - source REMOVED.

Same questions, flipped ground truth. Desired calibration: PRESENT answers,
ABSENT abstains (picks "Insufficient information"). Metrics:
  - over-abstention: PRESENT abstained on an answerable question.
  - correct-abstention: of PRESENT-correct questions, ABSENT abstained (the flip).
  - over-confidence: ABSENT still answered (did not abstain) with the source gone.

Run each arm separately (the shadow instance comes up via docker-compose.shadow.yml):
    ... run_c2 --arm present --base-url http://127.0.0.1:8080 --email ...
    ... run_c2 --arm absent  --base-url http://127.0.0.1:8081 --email ...   # after varghele
The second run (when both arms are captured) writes the paired scorecard.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed

from ..benchmarks.litqa2_runner import load_litqa2, _score_one

_HERE = os.path.dirname(__file__)
_QSET = os.path.join(_HERE, "c2_questions.json")


def _questions() -> list[dict]:
    want = {q["qid"] for q in json.load(open(_QSET))["questions"]}
    return [q for q in load_litqa2() if q["qid"] in want]


def _git_sha() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "--short", "HEAD"]).decode().strip()
    except Exception:
        return "unknown"


def run_arm(arm: str, base_url: str, email: str, *, concurrency: int = 1,
            work: str | None = None) -> dict:
    qs = _questions()
    work = work or os.path.join(_HERE, "..", "..", "c2_runs")
    os.makedirs(work, exist_ok=True)
    path = os.path.join(work, f"{arm}.verdicts.json")
    print(f"[c2:{arm}] scoring {len(qs)} questions against {base_url} (concurrency={concurrency})")
    results: dict[str, dict] = {}

    def one(q):
        return _score_one(q, base_url, email)

    with ThreadPoolExecutor(max_workers=concurrency) as ex:
        futs = {ex.submit(one, q): q for q in qs}
        for i, fut in enumerate(as_completed(futs), 1):
            r = fut.result()
            results[r["qid"]] = r
            if i % 10 == 0 or i == len(qs):
                print(f"  {i}/{len(qs)}")
    json.dump(results, open(path, "w"), indent=2)
    print(f"[c2:{arm}] verdicts -> {path}")
    return results


def _paired(present: dict, absent: dict, date: str | None) -> dict:
    qids = [q for q in present if q in absent]
    def rate(v, pred): return sum(1 for q in v if pred(v[q])) / len(v) if v else None
    p_acc = rate(present, lambda r: r["verdict"] == "correct")
    a_acc = rate(absent, lambda r: r["verdict"] == "correct")
    p_abst = rate(present, lambda r: r["verdict"] == "abstain")
    a_abst = rate(absent, lambda r: r["verdict"] == "abstain")

    # calibration on the ANSWERABLE subset (present answered correctly)
    answerable = [q for q in qids if present[q]["verdict"] == "correct"]
    correct_abstention = sum(1 for q in answerable if absent[q]["verdict"] == "abstain")
    still_correct = sum(1 for q in answerable if absent[q]["verdict"] == "correct")
    now_wrong = sum(1 for q in answerable if absent[q]["verdict"] == "incorrect")

    return {
        "n_paired": len(qids),
        "present": {"accuracy": p_acc, "abstain_rate": p_abst},
        "absent": {"accuracy": a_acc, "abstain_rate": a_abst},
        "answerable_n": len(answerable),
        "on_answerable_when_source_removed": {
            "correct_abstention": correct_abstention,     # desired
            "answered_still_correct": still_correct,       # from memory/web, no local source
            "answered_now_wrong": now_wrong,               # over-confident guess
            "correct_abstention_rate": (correct_abstention / len(answerable)) if answerable else None,
        },
        "date": date, "git_sha": _git_sha(),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--arm", required=True, choices=["present", "absent"])
    ap.add_argument("--base-url", required=True)
    ap.add_argument("--email", default=os.getenv("MUNIN_BENCH_EMAIL", "litqa2-eval@localhost"))
    ap.add_argument("--concurrency", type=int, default=1)
    ap.add_argument("--date", default=None)
    args = ap.parse_args()

    run_arm(args.arm, args.base_url, args.email, concurrency=args.concurrency)

    work = os.path.join(_HERE, "..", "..", "c2_runs")
    pp = os.path.join(work, "present.verdicts.json")
    ap_ = os.path.join(work, "absent.verdicts.json")
    if os.path.exists(pp) and os.path.exists(ap_):
        sc = _paired(json.load(open(pp)), json.load(open(ap_)), args.date)
        print("\n=== Track C2b paired abstention ===")
        print(json.dumps(sc, indent=2))
        if args.date:
            out = os.path.join(_HERE, "..", "..", "scorecards",
                               f"{args.date}_abstention-c2-shadow.json")
            json.dump(sc, open(out, "w"), indent=2)
            print(f"[c2] scorecard -> {out}")
    else:
        print(f"\n[c2] {args.arm} arm captured. Run the other arm to get the paired scorecard.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
