"""Track B runner: B3 capture (live, one arm) + B4 faithfulness metrics.

Flow (capture and score are separable so the slow live pass runs once):

  1. CAPTURE: drive the live research chat per LitQA2 question, record
     {qid, question, answer, contexts, ...} to ``<arm>.capture.jsonl``.
  2. SCORE: MiniCheck-score each captured answer's claims against its contexts.
  3. METRICS (B4): mean faithfulness, %% fully-supported, %% any-unsupported,
     with single-sample bootstrap CIs (Track A Phase-1 infra). Write a scorecard.

The record shape carries an ``arm`` field so Track D's bare / RAG / agentic arms
drop into the same score+metrics path unchanged.

    # capture + score the live agentic arm over the in-corpus pool (limit 40):
    MUNIN_BENCH_ENTAILMENT_DEVICE=cpu \
    /opt/munin/services/pipeline/venv/bin/python \
      -m munin_bench.faithfulness.faithfulness_runner \
      --base-url http://127.0.0.1:8080 --email you@uni --limit 40 --date 2026-07-08
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from ..metrics.bootstrap import single_bootstrap
from ..benchmarks.litqa2_runner import load_litqa2
from .capture import capture_answer
from .minicheck import MiniCheck


def _git_sha() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"]).decode().strip()
    except Exception:
        return "unknown"


# ---------------------------------------------------------------------------
# B3 capture
# ---------------------------------------------------------------------------
def _load_jsonl(path: str) -> list[dict]:
    if not os.path.exists(path):
        return []
    with open(path) as fh:
        return [json.loads(l) for l in fh if l.strip()]


def capture_pool(base_url: str, email: str, out_path: str, *,
                 limit: int = 0, concurrency: int = 1,
                 deadline: int = 300) -> list[dict]:
    """Capture answers to ``out_path`` INCREMENTALLY (append per completion) and
    RESUMABLY (skip qids already present). Live agentic capture is slow
    (~1-3 min/answer) and a long pool run can cross the 02:00 vLLM shutdown, so
    partial progress must survive; re-running continues where it left off."""
    questions = load_litqa2()
    if limit:
        questions = questions[:limit]
    existing = _load_jsonl(out_path)
    done = {r["qid"] for r in existing}
    todo = [q for q in questions if q["qid"] not in done]
    print(f"[b3] capturing {len(todo)} answers "
          f"({len(done)} already present, concurrency={concurrency})")
    t0 = time.time()

    def one(q: dict) -> dict:
        try:
            cap = capture_answer(base_url, email, q["question"], deadline=deadline)
            return {"qid": q["qid"], "question": q["question"],
                    "source_dois": q.get("source_dois", []), **cap}
        except Exception as e:  # network / stream failure -> record, don't crash
            return {"qid": q["qid"], "question": q["question"],
                    "answer": "", "contexts": [], "truncated": True,
                    "n_tool_results": 0, "error": type(e).__name__}

    # append-as-completed so a kill/shutdown never loses finished captures
    with open(out_path, "a", buffering=1) as fh, \
            ThreadPoolExecutor(max_workers=concurrency) as ex:
        futs = {ex.submit(one, q): q for q in todo}
        for i, fut in enumerate(as_completed(futs), 1):
            fh.write(json.dumps(fut.result()) + "\n")
            if i % 5 == 0 or i == len(todo):
                print(f"  captured {i}/{len(todo)} ({time.time() - t0:.0f}s)")

    results = _load_jsonl(out_path)
    results.sort(key=lambda r: r["qid"])
    print(f"[b3] capture -> {out_path} ({len(results)} total)")
    return results


# ---------------------------------------------------------------------------
# B3 score + B4 metrics
# ---------------------------------------------------------------------------
def score_and_metrics(captures: list[dict], *, arm: str, base_url: str,
                      device=None, threshold: float = 0.5,
                      n_resamples: int = 1000, date: str | None = None,
                      out_path: str | None = None) -> dict:
    mc = MiniCheck(device=device)
    print(f"[b3] scoring {len(captures)} answers with MiniCheck on {mc.device}")

    per_q: list[dict] = []
    t0 = time.time()
    for i, cap in enumerate(captures, 1):
        res = mc.score_answer(cap.get("answer", ""), cap.get("contexts", []),
                              threshold=threshold)
        per_q.append({
            "qid": cap["qid"],
            "n_claims": res["n_claims"],
            "n_contexts": len(cap.get("contexts", [])),
            "mean_support": res["mean_support"],
            "min_support": res["min_support"],
            "frac_supported": res["frac_supported"],
            "fully_supported": res["fully_supported"],
            "any_unsupported": res["any_unsupported"],
            "truncated": cap.get("truncated", False),
            "n_tool_results": cap.get("n_tool_results", 0),
        })
        if i % 20 == 0 or i == len(captures):
            print(f"  scored {i}/{len(captures)} ({time.time() - t0:.0f}s)")

    # B4: metrics over answers that produced >=1 claim (empty/failed excluded,
    # reported separately so nothing is silently dropped).
    scored = [r for r in per_q if r["n_claims"] > 0]
    n_empty = len(per_q) - len(scored)
    mean_support = single_bootstrap([r["mean_support"] for r in scored],
                                    n_resamples=n_resamples) if scored else None
    frac_full = single_bootstrap([1.0 if r["fully_supported"] else 0.0
                                  for r in scored],
                                 n_resamples=n_resamples) if scored else None

    scorecard = {
        "track": "faithfulness",
        "arm": arm,
        "judge_model": getattr(mc.model, "name_or_path", "MiniCheck-Flan-T5-Large"),
        "judge_device": mc.device,
        "generator_base_url": base_url,
        "git_sha": _git_sha(),
        "seed": 42,
        "threshold": threshold,
        "date": date,
        "n_answers": len(per_q),
        "n_scored": len(scored),
        "n_empty_or_failed": n_empty,
        "mean_claims_per_answer": (
            sum(r["n_claims"] for r in scored) / len(scored)) if scored else None,
        "mean_contexts_per_answer": (
            sum(r["n_contexts"] for r in scored) / len(scored)) if scored else None,
        # headline faithfulness metrics with bootstrap CIs
        "mean_faithfulness": mean_support,
        "frac_fully_supported": frac_full,
        "frac_any_unsupported": (
            1.0 - frac_full["mean"] if frac_full else None),
        "per_q": per_q,
    }

    if out_path:
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        with open(out_path, "w") as fh:
            json.dump(scorecard, fh, indent=2)
        print(f"[b4] scorecard -> {out_path}")
    return scorecard


def _print_summary(sc: dict) -> None:
    mf, ff = sc["mean_faithfulness"], sc["frac_fully_supported"]
    print("\n=== Track B faithfulness (arm: %s) ===" % sc["arm"])
    print(f"answers: {sc['n_answers']} scored={sc['n_scored']} "
          f"empty/failed={sc['n_empty_or_failed']}")
    print(f"mean claims/answer={sc['mean_claims_per_answer']:.1f}  "
          f"mean contexts/answer={sc['mean_contexts_per_answer']:.1f}")
    if mf:
        print(f"mean faithfulness = {mf['mean']:.3f}  "
              f"[{mf['ci_low']:.3f}, {mf['ci_high']:.3f}]  (n={mf['n']})")
    if ff:
        print(f"%% fully supported = {ff['mean']:.3f}  "
              f"[{ff['ci_low']:.3f}, {ff['ci_high']:.3f}]")
        print(f"%% any unsupported = {sc['frac_any_unsupported']:.3f}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default="http://127.0.0.1:8080")
    ap.add_argument("--email", default=os.getenv("MUNIN_BENCH_EMAIL", ""))
    ap.add_argument("--arm", default="agentic-live")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--concurrency", type=int, default=1)
    ap.add_argument("--deadline", type=int, default=300)
    ap.add_argument("--device", default=None)
    ap.add_argument("--date", default=None)
    ap.add_argument("--capture-only", action="store_true")
    ap.add_argument("--score-only", action="store_true",
                    help="skip capture; score an existing <arm>.capture.jsonl")
    ap.add_argument("--work-dir", default=None,
                    help="dir for the capture jsonl (default: faithfulness_runs/)")
    args = ap.parse_args()

    work = args.work_dir or os.path.join(
        os.path.dirname(__file__), "..", "..", "faithfulness_runs")
    os.makedirs(work, exist_ok=True)
    cap_path = os.path.join(work, f"{args.arm}.capture.jsonl")

    if args.score_only:
        captures = [json.loads(l) for l in open(cap_path)]
    else:
        if not args.email:
            print("ERROR: --email (or MUNIN_BENCH_EMAIL) required for capture")
            return 1
        captures = capture_pool(args.base_url, args.email, cap_path,
                                limit=args.limit, concurrency=args.concurrency,
                                deadline=args.deadline)
        if args.capture_only:
            print(f"[b3] captured {len(captures)}; skipping scoring")
            return 0

    stamp = args.date or "undated"
    out = os.path.join(os.path.dirname(__file__), "..", "..", "scorecards",
                       f"{stamp}_faithfulness-{args.arm}.json")
    sc = score_and_metrics(captures, arm=args.arm, base_url=args.base_url,
                           device=args.device, date=args.date, out_path=out)
    _print_summary(sc)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
