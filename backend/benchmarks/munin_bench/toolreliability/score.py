"""T11 - tool-use reliability metrics (offline reducer).

Consumes per-query records that carry an ordered `tool_events` list
(`[{name, is_error, duration_ms}]`, appended per SSE `tool_result` in
ablation/run_arm.py::_agentic_one - a tool error is a `result` dict with an
"error" key, chat_service.one()). Pure aggregation, no live run.

Metrics (BENCHMARK-TODO T11):
  - mean_calls_per_query : mean tool_results per query
  - error_rate           : tool_results with error / all tool_results
  - recovery_rate        : of queries with >=1 tool error, fraction that
                           still land a LATER non-error tool call OR finish
                           with a non-empty answer (didn't dead-end on the error)
  - per_tool             : {name: {calls, errors, error_rate}}

Usage:
    python -m munin_bench.toolreliability.score <run.json> [--tag NAME]
where <run.json> is an ablation-arm file ({arm,n,per_q:[...]}) whose per_q
records include `tool_events`.
"""
from __future__ import annotations

import argparse
import json
import os
from collections import defaultdict


def score(records: list[dict]) -> dict:
    """records: list of per-query dicts with `tool_events` (+ optional `answer`)."""
    n_q = len(records)
    total_calls = total_errors = 0
    per_tool: dict[str, dict] = defaultdict(lambda: {"calls": 0, "errors": 0})
    q_with_error = 0
    q_recovered = 0
    q_with_events = 0

    for r in records:
        evs = r.get("tool_events") or []
        if evs:
            q_with_events += 1
        total_calls += len(evs)
        for e in evs:
            name = e.get("name") or "unknown"
            per_tool[name]["calls"] += 1
            if e.get("is_error"):
                per_tool[name]["errors"] += 1
                total_errors += 1
        # recovery: this query had >=1 error?
        err_idxs = [i for i, e in enumerate(evs) if e.get("is_error")]
        if err_idxs:
            q_with_error += 1
            first_err = err_idxs[0]
            later_success = any(not e.get("is_error") for e in evs[first_err + 1:])
            finished = bool((r.get("answer") or "").strip())
            if later_success or finished:
                q_recovered += 1

    for t in per_tool.values():
        t["error_rate"] = round(t["errors"] / t["calls"], 4) if t["calls"] else 0.0

    return {
        "n_queries": n_q,
        "n_queries_with_tool_events": q_with_events,
        "total_tool_calls": total_calls,
        "mean_calls_per_query": round(total_calls / n_q, 2) if n_q else 0.0,
        "error_rate": round(total_errors / total_calls, 4) if total_calls else 0.0,
        "n_queries_with_error": q_with_error,
        "recovery_rate": round(q_recovered / q_with_error, 4) if q_with_error else None,
        "per_tool": {k: dict(v) for k, v in sorted(
            per_tool.items(), key=lambda kv: -kv[1]["calls"])},
    }


def _load_records(path: str) -> list[dict]:
    d = json.load(open(path))
    if isinstance(d, dict) and "per_q" in d:      # ablation-arm file
        return d["per_q"]
    if isinstance(d, list):                        # bare list of records
        return d
    raise SystemExit(f"unrecognised run file shape: {path}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("run", help="ablation-arm json ({arm,n,per_q}) with tool_events")
    ap.add_argument("--tag", default=None)
    ap.add_argument("--out-dir", default=os.path.join(
        os.path.dirname(__file__), "..", "..", "scorecards"))
    args = ap.parse_args()

    recs = _load_records(args.run)
    have = sum(1 for r in recs if r.get("tool_events") is not None)
    if have == 0:
        raise SystemExit(
            "No `tool_events` in any record - this run predates the T11 capture "
            "instrumentation (run_arm.py::_agentic_one). Re-run the agentic arm "
            "to populate; error/recovery cannot be recovered retroactively.")
    sc = score(recs)
    print(json.dumps(sc, indent=2))
    if args.tag:
        os.makedirs(args.out_dir, exist_ok=True)
        out = os.path.join(args.out_dir, f"{args.tag}_toolreliability.json")
        json.dump(sc, open(out, "w"), indent=2)
        print(f"\nwrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
