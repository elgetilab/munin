"""Track D runner: score one arm (bare / rag / agentic) on the LitQA2 subset.

Each arm produces the same per-question verdict (correct/incorrect/abstain/
unparseable) + cost (tokens, inference-time, tool calls). Written to
ablation_runs/<arm>.json (gitignored); compare.py builds the paired scorecard.

    PYTHONPATH=$HOME/.cache/munin_bench_deps:. $PY -m munin_bench.ablation.run_arm \
      --arm bare   --n 100
    ... --arm rag  --n 100 --top-k 5
    ... --arm agentic --n 100 --base-url http://127.0.0.1:8080 --email ...
"""

from __future__ import annotations

import argparse
import json
import os
import random
import time

from ..benchmarks.litqa2_runner import load_litqa2, build_mcq, parse_letter
from ..abstention.corpus import in_corpus
from .. import config
from . import vllm_answer as V

_HERE = os.path.dirname(__file__)
_QSET = os.path.join(_HERE, "d_questions.json")


def select_questions(n: int, seed: int = 7) -> list[dict]:
    qs = [q for q in load_litqa2()
          if len([d for d in q.get("source_dois", []) if d]) >= 1
          and in_corpus([d for d in q["source_dois"] if d][0])]
    random.Random(seed).shuffle(qs)
    return qs[:n]


def _questions(n: int) -> list[dict]:
    if os.path.exists(_QSET):
        want = set(json.load(open(_QSET))["qids"])
        return [q for q in load_litqa2() if q["qid"] in want]
    sel = select_questions(n)
    json.dump({"qids": [q["qid"] for q in sel], "n": len(sel), "seed": 7},
              open(_QSET, "w"), indent=2)
    return sel


def _verdict(mcq: dict, text: str) -> dict:
    letter = parse_letter(text, mcq["letters"], mcq["options"])
    if letter is None:
        v = "unparseable"
    elif letter == mcq["correct"]:
        v = "correct"
    elif letter == mcq["abstain"]:
        v = "abstain"
    else:
        v = "incorrect"
    return {"verdict": v, "letter": letter}


# --- retrieval for the RAG arm -------------------------------------------
_enc = None


def _bge():
    global _enc
    if _enc is None:
        from ..clients import load_encoder
        _enc = load_encoder(config.BGE_LARGE_PATH, hf_fallback=config.BGE_LARGE_HF_ID)
    return _enc


def _retrieve(question: str, top_k: int) -> list[str]:
    from ..clients import get_qdrant
    vec = _bge().encode(config.BGE_QUERY_INSTRUCTION + question).tolist()
    hits = get_qdrant().query_points(
        collection_name=config.PAPERS_BGE_COLLECTION, query=vec,
        limit=top_k, with_payload=True).points
    ctx = []
    for h in hits:
        p = h.payload or {}
        title = (p.get("title") or "").strip()
        abstract = (p.get("abstract") or "").strip()
        ctx.append(f"{title}: {abstract}"[:1200])
    return ctx


# --- agentic arm (live chat) capture: verdict + tool_calls + timing ------
def _agentic_one(q: dict, base_url: str, email: str, deadline: int = 300) -> dict:
    import urllib.request
    mcq = build_mcq(q)
    body = {"persona": "research", "ephemeral": True,
            "messages": [{"role": "user", "content": mcq["prompt"]}]}
    req = urllib.request.Request(
        base_url.rstrip("/") + "/api/chat/completions", data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "X-Munin-Email": email,
                 "X-Munin-Ephemeral": "true", "Accept": "text/event-stream"}, method="POST")
    from ..faithfulness.capture import RETRIEVAL_TOOLS, _extract_texts
    content, ev, n_calls = "", None, 0
    contexts: list[str] = []
    seen: set[str] = set()
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
                n_calls += 1
            elif ev == "tool_result" and o.get("name") in RETRIEVAL_TOOLS:
                found: list[str] = []
                _extract_texts(o.get("result"), found)
                for t in found:
                    if t not in seen:
                        seen.add(t); contexts.append(t)
            if ev == "done":
                break
    finally:
        resp.close()
    return {"qid": q["qid"], **_verdict(mcq, content), "tool_calls": n_calls,
            "elapsed_s": round(time.time() - t0, 2), "prompt_tokens": None,
            "completion_tokens": None, "answer": content, "contexts": contexts}


def run(arm: str, n: int, *, top_k: int = 5, base_url: str = "http://127.0.0.1:8080",
        email: str = "litqa2-eval@localhost") -> dict:
    qs = _questions(n)
    print(f"[d:{arm}] {len(qs)} questions")
    per = []
    t0 = time.time()
    for i, q in enumerate(qs, 1):
        mcq = build_mcq(q)
        if arm == "bare":
            r = V.bare_answer(mcq["prompt"])
            row = {"qid": q["qid"], **_verdict(mcq, r["content"]), "tool_calls": 0,
                   "elapsed_s": r["elapsed_s"], "prompt_tokens": r["prompt_tokens"],
                   "completion_tokens": r["completion_tokens"], "n_contexts": 0}
        elif arm == "rag":
            ctx = _retrieve(q["question"], top_k)
            r = V.rag_answer(mcq["prompt"], ctx)
            row = {"qid": q["qid"], **_verdict(mcq, r["content"]), "tool_calls": 0,
                   "elapsed_s": r["elapsed_s"], "prompt_tokens": r["prompt_tokens"],
                   "completion_tokens": r["completion_tokens"], "n_contexts": len(ctx),
                   "answer": r["content"], "contexts": ctx}
        elif arm == "agentic":
            row = _agentic_one(q, base_url, email)
        else:
            raise SystemExit(f"unknown arm {arm}")
        per.append(row)
        if i % 10 == 0 or i == len(qs):
            print(f"  {i}/{len(qs)} ({time.time()-t0:.0f}s)")

    work = os.path.join(_HERE, "..", "..", "ablation_runs")
    os.makedirs(work, exist_ok=True)
    out = os.path.join(work, f"{arm}.json")
    json.dump({"arm": arm, "n": len(per), "per_q": per}, open(out, "w"), indent=2)
    from collections import Counter
    c = Counter(r["verdict"] for r in per)
    acc = c["correct"] / len(per)
    print(f"[d:{arm}] accuracy={acc:.3f} verdicts={dict(c)} -> {out}")
    return {"arm": arm, "accuracy": acc, "verdicts": dict(c)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--arm", required=True, choices=["bare", "rag", "agentic"])
    ap.add_argument("--n", type=int, default=100)
    ap.add_argument("--top-k", type=int, default=5)
    ap.add_argument("--base-url", default="http://127.0.0.1:8080")
    ap.add_argument("--email", default="litqa2-eval@localhost")
    args = ap.parse_args()
    run(args.arm, args.n, top_k=args.top_k, base_url=args.base_url, email=args.email)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
