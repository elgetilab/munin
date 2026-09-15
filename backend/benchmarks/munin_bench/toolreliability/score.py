"""T11 - tool-use reliability metrics (offline reducer).

Consumes per-query records that carry an ordered `tool_events` list
(`[{name, is_error, duration_ms}]`, appended per SSE `tool_result` in
ablation/run_arm.py::_agentic_one - a tool error is a `result` dict with an
"error" key, chat_service.one()). Pure aggregation, no live run.

Metrics (BENCHMARK-TODO T11):
  - mean_calls_per_query : mean tool_results per query
  - error_rate           : HARD errors (executor raised -> result.error) / calls
  - degraded_rate        : hard errors OR SOFT self-reported failures (a tool
                           ran but returned a "warning"/"engines_unresponsive",
                           e.g. web_search on a Brave 402). Captures the outage
                           class that error_rate MISSES - the 2026-07-26 Brave
                           402 showed 0% hard-error but a real degraded_rate.
  - recovery_rate        : of queries with >=1 tool FAILURE (hard or soft),
                           fraction that still land a LATER good call OR finish
                           with a non-empty answer (didn't dead-end)
  - per_tool             : {name: {calls, errors, degraded, error_rate,
                           degraded_rate}}

Usage:
    python -m munin_bench.toolreliability.score <run.json> [--tag NAME]
where <run.json> is an ablation-arm file ({arm,n,per_q:[...]}) whose per_q
records include `tool_events`, typically ablation_runs/<run-tag>/agentic.json.
"""
from __future__ import annotations

import argparse
import json
import os
from collections import defaultdict


def score(records: list[dict]) -> dict:
    """records: list of per-query dicts with `tool_events` (+ optional `answer`)."""
    n_q = len(records)
    total_calls = total_errors = total_degraded = 0
    per_tool: dict[str, dict] = defaultdict(
        lambda: {"calls": 0, "errors": 0, "degraded": 0})
    q_with_failure = 0
    q_recovered = 0
    q_with_events = 0

    def _failed(e: dict) -> bool:  # hard error OR soft degradation
        return bool(e.get("is_error") or e.get("degraded"))

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
            if _failed(e):
                per_tool[name]["degraded"] += 1
                total_degraded += 1
        # recovery: query had >=1 tool FAILURE (hard or soft)?
        fail_idxs = [i for i, e in enumerate(evs) if _failed(e)]
        if fail_idxs:
            q_with_failure += 1
            later_ok = any(not _failed(e) for e in evs[fail_idxs[0] + 1:])
            finished = bool((r.get("answer") or "").strip())
            if later_ok or finished:
                q_recovered += 1

    for t in per_tool.values():
        t["error_rate"] = round(t["errors"] / t["calls"], 4) if t["calls"] else 0.0
        t["degraded_rate"] = round(t["degraded"] / t["calls"], 4) if t["calls"] else 0.0

    return {
        "n_queries": n_q,
        "n_queries_with_tool_events": q_with_events,
        "total_tool_calls": total_calls,
        "mean_calls_per_query": round(total_calls / n_q, 2) if n_q else 0.0,
        "error_rate": round(total_errors / total_calls, 4) if total_calls else 0.0,
        "degraded_rate": round(total_degraded / total_calls, 4) if total_calls else 0.0,
        "n_queries_with_failure": q_with_failure,
        "recovery_rate": round(q_recovered / q_with_failure, 4) if q_with_failure else None,
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
