"""Lab-analysis eval runner.

    cd backend/benchmarks
    export MUNIN_GATEWAY_TOKEN_FILE=<file>      # when retrieval enforces it
    python -m munin_bench.labanalysis.run --suite S1 --reps 3 --tag lab-baseline
    python -m munin_bench.labanalysis.run --case s1_4pl --reps 1 --tag smoke --no-scorecard
    python -m munin_bench.labanalysis.run --regrade results/lab/<tag>/records.jsonl

Cases come from fixtures/lab/ (public) and, with --heldout, from the private
set (default /opt/munin/data/evals/lab). Raw records (answers, artifacts,
check details) go to results/lab/<tag>/, which is not committed; the
scorecard goes to scorecards/ and must be copied to docs/paper-kit/scorecards/
in the same commit.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date
from pathlib import Path

from ..metrics.bootstrap import single_bootstrap
from ..scorecard import make_run_header, write_scorecard
from .cases import Case, load_cases
from .graders import grade
from .record import RunRecord

HERE = Path(__file__).resolve().parent
BENCH = HERE.parent.parent
FIXTURES = BENCH / "fixtures" / "lab"
HELDOUT = Path("/opt/munin/data/evals/lab")


def grade_record(case: Case, rec: RunRecord) -> dict:
    """Per-check results and the case score (fraction of checks passed)."""
    checks = []
    for turn_no, turn in enumerate(case.turns, start=1):
        for c in turn.checks:
            if rec.error and turn_no > len(rec.turns):
                passed, detail = False, f"turn not reached: {rec.error}"
            else:
                passed, detail = grade(c, rec, turn_no)
            checks.append({"turn": turn_no, "type": c["type"],
                           "name": c.get("name") or c.get("key") or c["type"],
                           "passed": bool(passed), "detail": detail})
    n = len(checks)
    score = sum(c["passed"] for c in checks) / n if n else 0.0
    return {"case": case.id, "suite": case.suite, "rep": rec.rep, "checks": checks,
            "score": score, "pass": n > 0 and all(c["passed"] for c in checks),
            "error": rec.error}


def summarize(graded: list[dict], system: str) -> dict:
    """Scorecard tasks: one per suite, per_query keyed '<case>#<rep>'."""
    tasks: dict = {}
    for suite in sorted({g["suite"] for g in graded}):
        rows = [g for g in graded if g["suite"] == suite]
        pq = {f"{g['case']}#{g['rep']}": {"case_score": g["score"], "case_pass": float(g["pass"])}
              for g in rows}
        metrics = {}
        for m in ("case_score", "case_pass"):
            vals = [v[m] for v in pq.values()]
            b = single_bootstrap(vals)
            metrics[m] = {"mean": b["mean"], "ci_low": b["ci_low"], "ci_high": b["ci_high"], "n": b["n"]}
        tasks[f"lab-{suite}"] = {system: {"metrics": metrics, "per_query": pq}}
    return tasks


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--suite", action="append", help="S1..S7, repeatable")
    ap.add_argument("--case", action="append", help="case id or id prefix, repeatable")
    ap.add_argument("--reps", type=int, default=1)
    ap.add_argument("--tag", required=False, default="lab")
    ap.add_argument("--arm", default="adhoc", help="adhoc or labkit (recorded in the scorecard)")
    # "munin" is what the web UI sends (GET /api/personas default_persona): the
    # router picks the profile per turn. Sending no persona on a new chat is
    # NOT auto-route: retrieval treats it as a raw /v1/ passthrough.
    ap.add_argument("--persona", default="munin", help="munin = auto-route, as users get; or chat/code/research")
    ap.add_argument("--base", default=os.environ.get("MUNIN_RETRIEVAL_URL", "http://127.0.0.1:8080"))
    ap.add_argument("--fixtures", type=Path, default=FIXTURES)
    ap.add_argument("--heldout", nargs="?", const=HELDOUT, type=Path, default=None)
    ap.add_argument("--keep", action="store_true", help="do not delete the chats (debugging)")
    ap.add_argument("--no-scorecard", action="store_true")
    ap.add_argument("--regrade", type=Path, help="re-grade a records.jsonl without the model")
    ap.add_argument("--sandbox-image", default="munin-sandbox:latest")
    ap.add_argument("--list", action="store_true", help="list the selected cases and exit")
    args = ap.parse_args(argv)

    roots = [args.fixtures] + ([args.heldout] if args.heldout else [])
    cases = [c for r in roots if r.exists() for c in load_cases(r, args.suite, args.case)]
    by_id = {c.id: c for c in cases}
    if args.list:
        for c in cases:
            print(f"{c.suite}  {c.id}  turns={len(c.turns)} checks={sum(len(t.checks) for t in c.turns)}")
        return 0

    out_dir = BENCH / "results" / "lab" / args.tag
    out_dir.mkdir(parents=True, exist_ok=True)

    if args.regrade:
        records = [RunRecord.from_json(json.loads(line)) for line in args.regrade.read_text().splitlines() if line]
        graded = [grade_record(by_id[r.case_id], r) for r in records if r.case_id in by_id]
    else:
        if not cases:
            print("no cases selected", file=sys.stderr)
            return 2
        from .client import LabClient
        client = LabClient(base=args.base, persona=args.persona)
        graded, rec_path = [], out_dir / "records.jsonl"
        try:
            with open(rec_path, "a") as fh:
                for case in cases:
                    for rep in range(1, args.reps + 1):
                        rec, bundle = client.run_case(case, rep, keep=args.keep,
                                                      sandbox_image=args.sandbox_image)
                        fh.write(json.dumps(rec.to_json()) + "\n")
                        fh.flush()
                        if bundle:
                            (out_dir / f"{case.id}#{rep}.zip").write_bytes(bundle)
                        g = grade_record(case, rec)
                        graded.append(g)
                        fails = [f"{c['name']}: {c['detail']}" for c in g["checks"] if not c["passed"]]
                        print(f"{'PASS' if g['pass'] else 'FAIL'} {case.id}#{rep} "
                              f"score={g['score']:.2f} {g['error'] or ''}", flush=True)
                        for f in fails:
                            print(f"    - {f[:200]}", flush=True)
        finally:
            client.close()

    with open(out_dir / "graded.json", "w") as fh:
        json.dump(graded, fh, indent=1)
    if not graded:
        return 1
    mean = sum(g["score"] for g in graded) / len(graded)
    print(f"\n{sum(g['pass'] for g in graded)}/{len(graded)} cases passed, mean check score {mean:.3f}")

    if not args.no_scorecard:
        meta = make_run_header(tag=args.tag, encoder="n/a", extra={
            "suite": "lab-analysis", "arm": args.arm, "persona": args.persona,
            "reps": args.reps, "heldout": bool(args.heldout),
            "cases": sorted(by_id), "vision_judge": None,
        })
        system = f"{meta.get('model', 'unknown')}/{args.arm}"
        path = write_scorecard(meta, summarize(graded, system), str(BENCH / "results"),
                               date.today().isoformat())
        print(f"scorecard: {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
