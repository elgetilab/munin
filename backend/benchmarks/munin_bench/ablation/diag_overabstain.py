"""Diagnostic: do the OVER-ABSTENTION cases read_paper the source full text?

Track D showed 20/100 agentic answers abstained despite the source being in BGE
top-10. Hypothesis: the specific MCQ answer is in full text/tables, not the
abstract, and the loop searches abstracts without reading the source paper. This
re-runs those questions through the live chat, capturing the tool-call SEQUENCE
(names + key args), and reports whether read_paper was called - and on the source
DOI. If read_paper is rarely used, forcing a full-text read is the lever; if it
IS used and still abstains, the value is in figures/tables (the LitQA2 ceiling).

    PYTHONPATH=$HOME/.cache/munin_bench_deps:. $PY -m munin_bench.ablation.diag_overabstain
"""

from __future__ import annotations

import json
import os
import time
import urllib.request
from collections import Counter

from ..benchmarks.litqa2_runner import load_litqa2, build_mcq, parse_letter
from ..abstention.corpus import normalize_doi
from .. import config
from .run_arm import _bge
from ..clients import get_qdrant

_HERE = os.path.dirname(__file__)
BASE = "http://127.0.0.1:8080"
EMAIL = "litqa2-eval@localhost"


def _over_abstention_qids() -> list[str]:
    ag = {r["qid"]: r for r in json.load(open(os.path.join(
        _HERE, "..", "..", "ablation_runs", "agentic.json")))["per_q"]}
    qmap = {q["qid"]: q for q in load_litqa2()}
    enc = _bge(); qc = get_qdrant()
    out = []
    for qid, r in ag.items():
        if r["verdict"] != "abstain":
            continue
        q = qmap.get(qid)
        sd = [d for d in (q or {}).get("source_dois", []) if d] if q else []
        if not sd:
            continue
        doi = normalize_doi(sd[0])
        vec = enc.encode(config.BGE_QUERY_INSTRUCTION + q["question"]).tolist()
        hits = qc.query_points(collection_name=config.PAPERS_BGE_COLLECTION,
                               query=vec, limit=10, with_payload=["doi"]).points
        if doi in {normalize_doi((h.payload or {}).get("doi") or "") for h in hits}:
            out.append((qid, doi))
    return out


def _run_capture(q: dict, deadline: int = 300) -> dict:
    mcq = build_mcq(q)
    body = {"persona": "research", "ephemeral": True,
            "messages": [{"role": "user", "content": mcq["prompt"]}]}
    req = urllib.request.Request(
        BASE + "/api/chat/completions", data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "X-Munin-Email": EMAIL,
                 "X-Munin-Ephemeral": "true", "Accept": "text/event-stream"}, method="POST")
    content, ev = "", None
    calls: list[tuple[str, list[str]]] = []  # (name, [dois it touched])
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
            if ev == "token" and o.get("content"):
                content += o["content"]
            elif ev == "tool_call":
                args = o.get("arguments") or {}
                dois: list[str] = []
                if isinstance(args, dict):
                    if args.get("doi"):
                        dois.append(normalize_doi(args["doi"]))
                    for d in (args.get("dois") or []):  # compare_papers fans out read_paper
                        if d:
                            dois.append(normalize_doi(d))
                calls.append((o.get("name"), dois))
            if ev == "done":
                break
    finally:
        resp.close()
    return {"letter": parse_letter(content, mcq["letters"], mcq["options"]),
            "calls": calls}


def main() -> int:
    over = _over_abstention_qids()
    qmap = {q["qid"]: q for q in load_litqa2()}
    print(f"[diag] {len(over)} over-abstention questions (source retrievable, abstained)")
    rows = []
    READ_TOOLS = {"read_paper", "compare_papers"}
    for i, (qid, src) in enumerate(over, 1):
        res = _run_capture(qmap[qid])
        names = Counter(n for n, _ in res["calls"])
        read_dois = sorted({d for n, ds in res["calls"] if n in READ_TOOLS for d in ds})
        n_reads = sum(names.get(t, 0) for t in READ_TOOLS)
        rows.append({"qid": qid, "src": src, "letter": res["letter"],
                     "n_calls": len(res["calls"]),
                     "read_paper": n_reads,
                     "read_source": src in read_dois,
                     "read_dois": read_dois})
        print(f"  {i}/{len(over)} {qid[:8]} calls={len(res['calls'])} "
              f"reads={n_reads} read_source={src in read_dois}")

    n = len(rows)
    any_read = sum(1 for r in rows if r["read_paper"])
    read_src = sum(1 for r in rows if r["read_source"])
    print("\n=== over-abstention read_paper diagnosis ===")
    print(f"  n={n}")
    print(f"  called read_paper at all: {any_read}/{n}")
    print(f"  read_paper'd the SOURCE:  {read_src}/{n}")
    print(f"  -> {'LEVER: force full-text read' if read_src < n/2 else 'CEILING: reads source but still abstains (value in figures/tables)'}")
    json.dump({"n": n, "any_read": any_read, "read_source": read_src, "rows": rows},
              open(os.path.join(_HERE, "..", "..", "ablation_runs", "diag_overabstain.json"), "w"), indent=2)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
