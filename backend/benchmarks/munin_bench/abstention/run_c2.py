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
import math
import os
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed

import numpy as np

from ..benchmarks.litqa2_runner import load_litqa2, _score_one
from ..metrics.bootstrap import single_bootstrap
from ..scorecard import corpus_snapshot

_HERE = os.path.dirname(__file__)
_QSET = os.path.join(_HERE, "c2_questions.json")

# CI settings. Seed and resample count match the rest of the suite
# (munin_bench.metrics.bootstrap), so a re-score is bit-stable.
_CI_RESAMPLES = 2000
_CI_SEED = 42
_Z_95 = 1.959963984540054


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
    # Sidecar metadata. `egress` is a first-class experimental variable for C2:
    # with egress=full the model re-fetches the "removed" source papers from
    # Semantic Scholar / Unpaywall, so the ABSENT arm stops measuring
    # corpus-grounded abstention (measured 2026-07-27: 17 of 49 removed sources
    # were pulled back in over the web). Hold it constant across the pair, and
    # use egress=off for any corpus-grounded claim.
    # `corpus` is the other first-class variable here: the ABSENT arm is defined
    # by searching a corpus with the source papers removed, so the pair is only
    # interpretable if each arm records which collections it read and how many
    # points they held. run_suite.sh passes the shadow names for the absent arm.
    meta = {"arm": arm, "base_url": base_url, "n": len(results),
            "egress": os.getenv("MUNIN_EVAL_EGRESS", "off"),
            "corpus": corpus_snapshot(),
            "git_sha": _git_sha()}
    json.dump(meta, open(os.path.join(work, f"{arm}.meta.json"), "w"), indent=2)
    print(f"[c2:{arm}] verdicts -> {path}  (egress={meta['egress']})")
    return results


def _prop_ci(flags: list[int]) -> dict | None:
    """95% CI on a proportion, two ways.

    `bootstrap` is the percentile bootstrap the rest of the suite uses, so this
    number is comparable with every other CI in a scorecard. `wilson` is the
    closed-form score interval, reported alongside because at the n we care
    about here (the answerable subset is ~27 items) the bootstrap is coarse:
    it can only land on multiples of 1/n, and it degenerates at p near 0 or 1
    where every resample agrees. Where the two disagree, prefer Wilson and say
    so; where they agree, the bootstrap number is the one to quote for
    consistency.
    """
    n = len(flags)
    if n == 0:
        return None
    boot = single_bootstrap([float(f) for f in flags],
                            n_resamples=_CI_RESAMPLES, seed=_CI_SEED)
    p = boot["mean"]
    z2n = _Z_95 ** 2 / n
    centre = (p + z2n / 2.0) / (1.0 + z2n)
    half = (_Z_95 / (1.0 + z2n)) * math.sqrt(p * (1.0 - p) / n + _Z_95 ** 2 / (4.0 * n * n))
    return {
        "rate": p,
        "n": n,
        "bootstrap": {"ci_low": boot["ci_low"], "ci_high": boot["ci_high"],
                      "n_resamples": _CI_RESAMPLES, "seed": _CI_SEED},
        "wilson": {"ci_low": max(0.0, centre - half), "ci_high": min(1.0, centre + half)},
    }


def _unconditional_ci(present: dict, absent: dict, qids: list[str]) -> dict | None:
    """CI on correct-abstention that also propagates WHICH items are answerable.

    `_prop_ci` on the answerable subset is *conditional*: it treats the 27
    answerable items as fixed and asks how the abstention rate would vary over
    a resample of them. But the subset is itself a random outcome (it is the
    set the PRESENT arm happened to answer correctly). Here we resample the
    full paired question set, re-derive the answerable subset inside each
    resample, and recompute the rate.

    Do NOT expect this to be wider. It is a ratio estimator, so numerator and
    denominator co-vary and the extra subset-membership variance largely
    cancels: on the real 2026-07-27 data it comes out marginally NARROWER than
    the conditional interval ([0.481, 0.833] against [0.481, 0.852]). Its value
    is as a robustness check that conditioning on the observed subset is not
    flattering the interval, not as a more conservative bound. Resamples with
    an empty answerable subset are dropped and counted.
    """
    if not qids:
        return None
    rng = np.random.default_rng(_CI_SEED)
    n = len(qids)
    idx = rng.integers(0, n, size=(_CI_RESAMPLES, n))
    rates, empties = [], 0
    for row in idx:
        sub = [qids[i] for i in row if present[qids[i]]["verdict"] == "correct"]
        if not sub:
            empties += 1
            continue
        rates.append(sum(1 for q in sub if absent[q]["verdict"] == "abstain") / len(sub))
    if not rates:
        return None
    arr = np.asarray(rates, dtype=float)
    return {
        "ci_low": float(np.percentile(arr, 2.5)),
        "ci_high": float(np.percentile(arr, 97.5)),
        "n_resamples": _CI_RESAMPLES,
        "seed": _CI_SEED,
        "empty_resamples": empties,
        "note": "resamples the full paired set and re-derives the answerable "
                "subset each time, so it carries the uncertainty in which "
                "questions are answerable as well as in the abstention rate",
    }


def _correct_abstention_delta(old: tuple[dict, dict], new: tuple[dict, dict]) -> dict | None:
    """Paired bootstrap on the CHANGE in correct-abstention between two runs.

    The two runs share the same 50 frozen questions, so the comparison can be
    paired at the QUESTION level even though their answerable subsets differ in
    size (20 vs 27): one resample of qids drives both arms, and each arm derives
    its own answerable subset inside that resample. That is what makes the delta
    a paired quantity rather than two independent proportions eyeballed against
    each other, and it is why comparing the two marginal CIs is weaker evidence
    than this test.
    """
    (op, oa), (np_, na) = old, new
    qids = [q for q in op if q in oa and q in np_ and q in na]
    if not qids:
        return None

    def rate(idx_qids, present_v, absent_v):
        sub = [q for q in idx_qids if present_v[q]["verdict"] == "correct"]
        if not sub:
            return None
        return sum(1 for q in sub if absent_v[q]["verdict"] == "abstain") / len(sub)

    obs_old, obs_new = rate(qids, op, oa), rate(qids, np_, na)
    rng = np.random.default_rng(_CI_SEED)
    n = len(qids)
    idx = rng.integers(0, n, size=(_CI_RESAMPLES, n))
    diffs, dropped = [], 0
    for row in idx:
        sample = [qids[i] for i in row]
        r_old, r_new = rate(sample, op, oa), rate(sample, np_, na)
        if r_old is None or r_new is None:
            dropped += 1
            continue
        diffs.append(r_new - r_old)
    if not diffs:
        return None
    arr = np.asarray(diffs, dtype=float)
    p_le, p_ge = float(np.mean(arr <= 0.0)), float(np.mean(arr >= 0.0))
    return {
        "old_rate": obs_old,
        "new_rate": obs_new,
        "delta": (obs_new - obs_old) if (obs_old is not None and obs_new is not None) else None,
        "ci_low": float(np.percentile(arr, 2.5)),
        "ci_high": float(np.percentile(arr, 97.5)),
        "p_value_two_sided": min(1.0, 2.0 * min(p_le, p_ge)),
        "n_paired_questions": n,
        "n_resamples": _CI_RESAMPLES,
        "seed": _CI_SEED,
        "dropped_resamples": dropped,
    }


def _paired(present: dict, absent: dict, date: str | None,
            *, git_sha: str | None = None) -> dict:
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

    def flags(pred): return [1 if pred(absent[q]) else 0 for q in answerable]

    return {
        "n_paired": len(qids),
        "present": {
            "accuracy": p_acc, "abstain_rate": p_abst,
            "accuracy_ci": _prop_ci([1 if present[q]["verdict"] == "correct" else 0 for q in qids]),
            "abstain_rate_ci": _prop_ci([1 if present[q]["verdict"] == "abstain" else 0 for q in qids]),
        },
        "absent": {
            "accuracy": a_acc, "abstain_rate": a_abst,
            "accuracy_ci": _prop_ci([1 if absent[q]["verdict"] == "correct" else 0 for q in qids]),
            "abstain_rate_ci": _prop_ci([1 if absent[q]["verdict"] == "abstain" else 0 for q in qids]),
        },
        "answerable_n": len(answerable),
        "on_answerable_when_source_removed": {
            "correct_abstention": correct_abstention,     # desired
            "answered_still_correct": still_correct,       # from memory/web, no local source
            "answered_now_wrong": now_wrong,               # over-confident guess
            "correct_abstention_rate": (correct_abstention / len(answerable)) if answerable else None,
            # The three cells are a multinomial over the answerable subset, so
            # their CIs are marginal and are NOT independent - do not read the
            # three intervals as if they could move separately.
            "correct_abstention_rate_ci": _prop_ci(flags(lambda r: r["verdict"] == "abstain")),
            "answered_still_correct_rate_ci": _prop_ci(flags(lambda r: r["verdict"] == "correct")),
            "answered_now_wrong_rate_ci": _prop_ci(flags(lambda r: r["verdict"] == "incorrect")),
            "correct_abstention_rate_ci_unconditional": _unconditional_ci(present, absent, qids),
        },
        "date": date, "git_sha": git_sha or _git_sha(),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--arm", choices=["present", "absent"])
    ap.add_argument("--base-url")
    ap.add_argument("--email", default=os.getenv("MUNIN_BENCH_EMAIL", "litqa2-eval@localhost"))
    ap.add_argument("--concurrency", type=int, default=1)
    ap.add_argument("--date", default=None)
    # Re-score already-captured verdicts without re-running generation. Used to
    # add the CIs to the 2026-07-27 scorecards after the fact; also the cheap
    # path whenever the scoring changes but the captures do not.
    ap.add_argument("--rescore", action="store_true",
                    help="score existing verdict files only; no generation")
    ap.add_argument("--suffix", default="",
                    help="verdict-file suffix, e.g. 'egressfull' for *.verdicts.egressfull.json")
    ap.add_argument("--out-tag", default="abstention-c2-shadow",
                    help="scorecard basename after the date")
    ap.add_argument("--keep-git-sha", default=None,
                    help="preserve the capture-time git sha instead of stamping HEAD")
    ap.add_argument("--vs-suffix", default=None,
                    help="verdict-file suffix of an EARLIER run to compare against; "
                         "adds a question-paired bootstrap on the correct-abstention delta")
    ap.add_argument("--work-dir", default=None,
                    help="verdict dir (default c2_runs/). Use c2_runs/<tag>/ for a new backbone; "
                         "run_arm writes <arm>.verdicts.json IN PLACE")
    ap.add_argument("--vs-dir", default=None,
                    help="dir holding the --vs-suffix files (default: --work-dir)")
    args = ap.parse_args()

    work = args.work_dir or os.path.join(_HERE, "..", "..", "c2_runs")
    if not args.rescore:
        if not args.arm or not args.base_url:
            ap.error("--arm and --base-url are required unless --rescore is given")
        run_arm(args.arm, args.base_url, args.email, concurrency=args.concurrency, work=work)

    sfx = f".{args.suffix}" if args.suffix else ""
    pp = os.path.join(work, f"present.verdicts{sfx}.json")
    ap_ = os.path.join(work, f"absent.verdicts{sfx}.json")
    if os.path.exists(pp) and os.path.exists(ap_):
        sc = _paired(json.load(open(pp)), json.load(open(ap_)), args.date,
                     git_sha=args.keep_git_sha)
        if args.rescore:
            # Provenance: the verdicts are from the capture run, the CIs are
            # not. Say so in the file rather than letting a later reader assume
            # the whole scorecard was produced in one pass.
            sc["rescored_by"] = "munin_bench.abstention.run_c2 --rescore"
            sc["rescored_at_git_sha"] = _git_sha()
        if args.vs_suffix is not None:
            vsfx = f".{args.vs_suffix}" if args.vs_suffix else ""
            vsdir = args.vs_dir or work
            op = os.path.join(vsdir, f"present.verdicts{vsfx}.json")
            oa = os.path.join(vsdir, f"absent.verdicts{vsfx}.json")
            if os.path.exists(op) and os.path.exists(oa):
                sc["correct_abstention_vs_" + (args.vs_suffix or os.path.basename(os.path.normpath(vsdir)))] = _correct_abstention_delta(
                    (json.load(open(op)), json.load(open(oa))),
                    (json.load(open(pp)), json.load(open(ap_))),
                )
            else:
                print(f"[c2] --vs-suffix {args.vs_suffix!r} in {vsdir}: verdict files not found, skipping delta")
        print("\n=== Track C2b paired abstention ===")
        print(json.dumps(sc, indent=2))
        if args.date:
            out = os.path.join(_HERE, "..", "..", "scorecards",
                               f"{args.date}_{args.out_tag}.json")
            json.dump(sc, open(out, "w"), indent=2)
            print(f"[c2] scorecard -> {out}")
    else:
        print(f"\n[c2] {args.arm} arm captured. Run the other arm to get the paired scorecard.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
