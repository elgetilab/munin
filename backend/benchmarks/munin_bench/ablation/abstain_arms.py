"""Track D abstention add-on: bare + RAG on the C1 fabricated set.

Does the HARNESS cause Munin's good abstention (C1: 0.98 refuse, 0 confabulation),
or does the bare model refuse fabricated papers too? Runs the 100 fabricated
items through the bare arm (direct vLLM, no tools) and the RAG arm (retrieve
top-k for the fake question -> the fake paper is not in the corpus, so contexts
are unrelated) and classifies with the C1 detector.

    PYTHONPATH=... $PY -m munin_bench.ablation.abstain_arms --arm bare
    ... --arm rag --top-k 5
"""

from __future__ import annotations

import argparse
import json
import os
import time

from ..abstention.detect import classify
from . import vllm_answer as V
from . import runs_dir
from .run_arm import _retrieve

_HERE = os.path.dirname(__file__)
_SET = os.path.join(_HERE, "..", "abstention", "fabricated_abstention.json")


def run(arm: str, *, top_k: int = 5, limit: int = 0, tag: str | None = None) -> dict:
    items = json.load(open(_SET))["items"]
    if limit:
        items = items[:limit]
    print(f"[d-abstain:{arm}] {len(items)} fabricated items")
    per = []
    t0 = time.time()
    for i, it in enumerate(items, 1):
        q = it["question"]
        if arm == "bare":
            ans = V.bare_answer(q)["content"]
        elif arm == "rag":
            ctx = _retrieve(q, top_k)
            ans = V.rag_answer(q, ctx)["content"]
        else:
            raise SystemExit("arm must be bare|rag")
        cls = classify(ans, asked_doi=it.get("doi"))
        per.append({"id": it["id"], "kind": it["kind"], "verdict": cls["verdict"],
                    "abstained": cls["abstained"], "cited_in_corpus": cls["cited_in_corpus"],
                    "answer_tail": (ans or "")[-400:]})
        if i % 20 == 0 or i == len(items):
            print(f"  {i}/{len(items)} ({time.time()-t0:.0f}s)")

    from collections import Counter
    c = Counter(p["verdict"] for p in per)
    n = len(per)
    abstain = sum(1 for p in per if p["abstained"]) / n
    confab_cite = sum(1 for p in per if p["verdict"] == "confabulated_local_cite")
    work = runs_dir(tag, create=True)
    json.dump({"arm": arm, "n": n, "abstain_rate": abstain,
               "confabulated_local_cite": confab_cite, "verdicts": dict(c),
               "per_item": per}, open(os.path.join(work, f"abstain_{arm}.json"), "w"), indent=2)
    print(f"[d-abstain:{arm}] abstain_rate={abstain:.3f} confab_local_cite={confab_cite} verdicts={dict(c)}")
    return {"arm": arm, "abstain_rate": abstain, "confabulated_local_cite": confab_cite}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--arm", required=True, choices=["bare", "rag"])
    ap.add_argument("--top-k", type=int, default=5)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--tag", default=None, help="ablation_runs/<tag>/ (or MUNIN_ABLATION_TAG)")
    args = ap.parse_args()
    if not (args.tag or os.getenv("MUNIN_ABLATION_TAG")):
        raise SystemExit("--tag (or MUNIN_ABLATION_TAG) is required for a write")
    run(args.arm, top_k=args.top_k, limit=args.limit, tag=args.tag)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
