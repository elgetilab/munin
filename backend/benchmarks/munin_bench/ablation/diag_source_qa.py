"""Task-4 verification: run the `source` agent (mode=qa) vs the 0.82 oracle.

The full-text ceiling oracle (diag_fulltext_oracle.py) fed raw paper text to the
model and flipped 9/11 over-abstentions to correct. The `source` agent's qa mode
is meant to deliver that in production: resolve -> extract FULL text -> one LLM
call (not read_paper's summarise-and-discard). This runs source(mode=qa) on the
same 20 over-abstention questions and scores it with the same letter-parser, so
we can compare directly against the oracle.

Unlike the oracle (which only covered the 11 with a local PDF), source does its
own resolution + OA fetch, so it can cover more. Per-question errors are caught
and recorded rather than aborting the run.

Run in the retrieval-env runner (has jsonschema etc.), with retrieval + benchmarks
on the path and host-facing service env:

    PYTHONPATH=$HOME/.cache/munin_bench_deps:\\
      /opt/munin/services/pipeline/venv/lib/python3.12/site-packages:\\
      backend/retrieval:backend/benchmarks \\
    GROBID_URL=http://localhost:8070 PAPERS_CACHE_DIR=/opt/munin/data/papers_cached \\
    $HOME/.cache/source_verify_venv/bin/python -m munin_bench.ablation.diag_source_qa
"""

from __future__ import annotations

import asyncio
import json
import os
from collections import Counter

from ..benchmarks.litqa2_runner import load_litqa2, build_mcq
from .run_arm import _verdict

_HERE = os.path.dirname(__file__)


def _over_rows() -> list[dict]:
    path = os.path.join(_HERE, "..", "..", "ablation_runs", "diag_overabstain.json")
    return json.load(open(path))["rows"]


async def _one(source_fn, q: dict, doi: str) -> dict:
    mcq = build_mcq(q)
    try:
        env = await source_fn(refs=[{"doi": doi}], mode="qa", question=mcq["prompt"])
    except Exception as exc:  # noqa: BLE001
        return {"qid": q["qid"], "src": doi, "error": f"{type(exc).__name__}: {exc}"}
    answer = env.get("answer") or ""
    vd = _verdict(mcq, answer)
    src = env.get("source") or {}
    return {"qid": q["qid"], "src": doi, "verdict": vd["verdict"],
            "letter": vd["letter"], "outcome": env.get("outcome"),
            "read_depth": env.get("read_depth"), "origin": src.get("origin"),
            "answer_tail": answer[-300:]}


def main() -> int:
    from mcp.tools.source import source  # imported here so path errors are obvious

    rows = _over_rows()
    qmap = {q["qid"]: q for q in load_litqa2()}
    print(f"[diag] source(qa) on {len(rows)} over-abstention questions")
    out = []
    for i, r in enumerate(rows, 1):
        q = qmap.get(r["qid"])
        if not q:
            continue
        res = asyncio.run(_one(source, q, r["src"]))
        out.append(res)
        if "error" in res:
            print(f"  {i}/{len(rows)} {r['qid'][:8]} ERROR {res['error'][:80]}")
        else:
            print(f"  {i}/{len(rows)} {r['qid'][:8]} outcome={res['outcome']:14s} "
                  f"verdict={res['verdict']:11s} depth={res['read_depth']} "
                  f"origin={res['origin']}")

    scored = [r for r in out if "verdict" in r]
    vc = Counter(r["verdict"] for r in scored)
    depth = Counter(r.get("read_depth") for r in scored)
    n_err = sum(1 for r in out if "error" in r)
    n = len(scored)
    print("\n=== source(qa) vs the 0.82 full-text oracle ===")
    print(f"  scored={n}  errors={n_err}")
    print(f"  correct={vc['correct']} incorrect={vc['incorrect']} "
          f"abstain={vc['abstain']} unparseable={vc['unparseable']}")
    full = [r for r in scored if r.get("read_depth") == "full_text"]
    fc = Counter(r["verdict"] for r in full)
    print(f"  full-text reads: {len(full)}  (correct={fc['correct']}, "
          f"abstain={fc['abstain']}, incorrect={fc['incorrect']})")
    print(f"  read_depth mix: {dict(depth)}")
    if n:
        print(f"  -> abstain->correct conversion on the over-abstention set: "
              f"{vc['correct']}/{n}  (oracle ceiling was 9/11 = 0.82 on local-PDF subset)")
    json.dump({"n": n, "errors": n_err, "verdicts": dict(vc),
               "read_depth": dict(depth), "rows": out},
              open(os.path.join(_HERE, "..", "..", "ablation_runs",
                                "diag_source_qa.json"), "w"), indent=2)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
