"""Probe: does the agentic loop pass `focus` to read_paper? (gating question)

The proposed within-document-excerpt lever is focus-gated: read_paper returns
verbatim passages ranked against `focus`. If the loop rarely passes focus, the
lever never fires. This re-runs a few over-abstention MCQs through the live chat
and records the `focus` argument on every read_paper/compare_papers call.

    PYTHONPATH=$HOME/.cache/munin_bench_deps:. $PY -m munin_bench.ablation.diag_focus_probe
"""

from __future__ import annotations

import json
import os
import time
import urllib.request

from ..benchmarks.litqa2_runner import load_litqa2, build_mcq

_HERE = os.path.dirname(__file__)
BASE = "http://127.0.0.1:8080"
EMAIL = "litqa2-eval@localhost"
READ_TOOLS = {"read_paper", "compare_papers"}
N = 6


def _capture_focus(prompt: str, deadline: int = 300) -> list[dict]:
    body = {"persona": "research", "ephemeral": True,
            "messages": [{"role": "user", "content": prompt}]}
    req = urllib.request.Request(
        BASE + "/api/chat/completions", data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "X-Munin-Email": EMAIL,
                 "X-Munin-Ephemeral": "true", "Accept": "text/event-stream"}, method="POST")
    ev, reads = None, []
    t0 = time.time()
    resp = urllib.request.urlopen(req, timeout=60)
    try:
        for raw in resp:
            if time.time() - t0 > deadline:
                break
            line = raw.decode("utf-8", "replace").rstrip("\n")
            if line.startswith("event:"):
                ev = line[6:].strip(); continue
            if not line.startswith("data:"):
                continue
            d = line[5:].strip()
            if not d or d == "[DONE]":
                continue
            try:
                o = json.loads(d)
            except Exception:
                continue
            if ev == "tool_call" and o.get("name") in READ_TOOLS:
                args = o.get("arguments") or {}
                reads.append({"tool": o.get("name"),
                              "focus": (args.get("focus") if isinstance(args, dict) else None),
                              "doi": (args.get("doi") if isinstance(args, dict) else None)})
            if ev == "done":
                break
    finally:
        resp.close()
    return reads


def main() -> int:
    over = json.load(open(os.path.join(_HERE, "..", "..", "ablation_runs",
                                        "diag_overabstain.json")))["rows"][:N]
    qmap = {q["qid"]: q for q in load_litqa2()}
    all_reads, with_focus = 0, 0
    for i, r in enumerate(over, 1):
        q = qmap[r["qid"]]
        reads = _capture_focus(build_mcq(q)["prompt"])
        nf = sum(1 for x in reads if x["focus"])
        all_reads += len(reads); with_focus += nf
        print(f"  {i}/{N} {r['qid'][:8]} reads={len(reads)} with_focus={nf}")
        for x in reads:
            print(f"      {x['tool']:14s} focus={x['focus']!r}")

    print("\n=== focus-pass probe ===")
    print(f"  read calls: {all_reads}, with focus: {with_focus} "
          f"({(with_focus/all_reads if all_reads else 0):.0%})")
    print(f"  -> {'focus is commonly passed; focus-gated lever will fire' if all_reads and with_focus/all_reads >= 0.5 else 'focus is often omitted; the lever needs a fallback query or a stronger schema nudge'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
