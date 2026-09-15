"""Track D runner: score one arm (bare / rag / agentic) on the LitQA2 subset.

Each arm produces the same per-question verdict (correct/incorrect/abstain/
unparseable) + cost (tokens, inference-time, tool calls). Written to
ablation_runs/<tag>/<arm>.json (gitignored); compare.py builds the paired
scorecard from the same tag dir.

    PYTHONPATH=$HOME/.cache/munin_bench_deps:. $PY -m munin_bench.ablation.run_arm \
      --arm bare   --n 100 --tag <run-tag>
    ... --arm rag  --n 100 --top-k 5 --tag <run-tag>
    ... --arm agentic --n 100 --tag <run-tag> --base-url http://127.0.0.1:8080 --email ...

Capture is incremental and resumable: every finished question is appended to
ablation_runs/<tag>/<arm>.capture.jsonl as it completes, and a re-invocation
with the same tag skips the qids already present. The 2026-08-26 agentic arm
died at 191/199 when the 02:00 cron cancelled vLLM and the last eight had to
be recovered by hand; a ten-hour arm must survive that. Mirrors run_c1.

An unparseable verdict carries a `reason`: `empty_content` (the turn returned
nothing), `deadline_hit` (the stream was cut at the deadline), or
`letter_not_found` (text came back but no answer letter). On a non-Qwen
backbone a tool-parser mismatch shows up as the first two, and they must not
be read as model quality (THIRD-MODEL-REVIEW section 3).
"""

from __future__ import annotations

import argparse
import json
import os
import random
import subprocess
import time

from ..benchmarks.litqa2_runner import load_litqa2, build_mcq, parse_letter
from ..abstention.corpus import in_corpus
from .. import config
from . import runs_dir
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


# Raw tool-call markup that should never reach `content` when the server-side
# parser is doing its job. Qwen3 XML tags and gpt-oss harmony channel tokens.
_TOOL_MARKUP = ("<tool_call>", "</tool_call>", "<|channel|>", "<|call|>",
                "<|start|>", " to=functions.", "to=functions.")


def _verdict(mcq: dict, text: str, *, deadline_hit: bool = False) -> dict:
    letter = parse_letter(text, mcq["letters"], mcq["options"])
    out: dict = {"letter": letter}
    if letter is None:
        out["verdict"] = "unparseable"
        if not (text or "").strip():
            out["reason"] = "empty_content"
        elif deadline_hit:
            out["reason"] = "deadline_hit"
        else:
            out["reason"] = "letter_not_found"
    elif letter == mcq["correct"]:
        out["verdict"] = "correct"
    elif letter == mcq["abstain"]:
        out["verdict"] = "abstain"
    else:
        out["verdict"] = "incorrect"
    # Suspected tool-call parse leak: the model emitted call markup as prose.
    # Counted, never scored; compare.py reports it next to accuracy.
    out["tool_markup_in_content"] = any(m in (text or "") for m in _TOOL_MARKUP)
    return out


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
# deadline=900 matches the standalone LitQA2 answer track (raised 300->900 on
# 2026-07-24 after truncations-scored-as-wrong were found to be a harness limit,
# not a model failure). At 300s a `source` read of several full papers can't
# finish, so the turn truncates mid-loop and scores unparseable; 900 lets a
# multi-read research turn complete. Cost timing stays valid at concurrency=1.
def _agentic_one(q: dict, base_url: str, email: str, deadline: int = 900) -> dict:
    import urllib.request
    mcq = build_mcq(q)
    body = {"persona": "research", "ephemeral": True,
            "messages": [{"role": "user", "content": mcq["prompt"]}]}
    req = urllib.request.Request(
        base_url.rstrip("/") + "/api/chat/completions", data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "X-Munin-Email": email,
                 "X-Munin-Ephemeral": "true", "Accept": "text/event-stream",
                 # cost guard: eval never touches the paid Brave key / S2 API.
                 # Set MUNIN_EVAL_EGRESS=full to opt back in for a live run.
                 "X-Munin-Egress": os.getenv("MUNIN_EVAL_EGRESS", "off")},
        method="POST")
    from ..faithfulness.capture import RETRIEVAL_TOOLS, _extract_texts
    content, ev, n_calls = "", None, 0
    contexts: list[str] = []
    seen: set[str] = set()
    # T11 tool-use reliability: ordered per-tool OUTCOMES from tool_result
    # events (SSE result dict carries an "error" key on executor failure -
    # chat_service one(): {"error": ...}). Local-stream signal, no web dep.
    tool_events: list[dict] = []
    deadline_hit = False
    t0 = time.time()
    resp = urllib.request.urlopen(req, timeout=60)
    try:
        for raw in resp:
            if time.time() - t0 > deadline:
                deadline_hit = True
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
            elif ev == "tool_result":
                res = o.get("result")
                _rd = res if isinstance(res, dict) else {}
                tool_events.append({
                    "name": o.get("name"),
                    # hard error: executor raised -> {"error": ...}
                    "is_error": "error" in _rd,
                    # soft degradation: the tool ran but SELF-REPORTS failure
                    # (web_search sets "warning"/"engines_unresponsive" on a
                    # Brave 402 / all-engines-down; invisible to is_error).
                    "degraded": bool(_rd.get("warning")
                                     or _rd.get("engines_unresponsive")),
                    "duration_ms": o.get("duration_ms"),
                })
                if o.get("name") in RETRIEVAL_TOOLS:
                    found: list[str] = []
                    _extract_texts(res, found)
                    for t in found:
                        if t not in seen:
                            seen.add(t); contexts.append(t)
            if ev == "done":
                break
    finally:
        resp.close()
    return {"qid": q["qid"], **_verdict(mcq, content, deadline_hit=deadline_hit),
            "tool_calls": n_calls, "deadline_hit": deadline_hit,
            "elapsed_s": round(time.time() - t0, 2), "prompt_tokens": None,
            "completion_tokens": None, "answer": content, "contexts": contexts,
            "tool_events": tool_events}


def _git_sha() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "--short", "HEAD"],
                                       stderr=subprocess.DEVNULL).decode().strip()
    except Exception:
        return "unknown"


def _served_model() -> str:
    """Served model id from the vLLM the bare/RAG arms call; the agentic arm's
    backend is assumed to sit on the same vLLM (the driver asserts it)."""
    import urllib.request
    try:
        with urllib.request.urlopen(config.VLLM_URL + "/v1/models", timeout=8) as r:
            return json.load(r)["data"][0].get("id") or config.VLLM_MODEL_NAME
    except Exception:
        return config.VLLM_MODEL_NAME


def run(arm: str, n: int, *, top_k: int = 5, base_url: str = "http://127.0.0.1:8080",
        email: str = "litqa2-eval@localhost", tag: str | None = None,
        deadline: int = 900) -> dict:
    qs = _questions(n)
    work = runs_dir(tag, create=True)
    cap_path = os.path.join(work, f"{arm}.capture.jsonl")
    done: dict[str, dict] = {}
    if os.path.exists(cap_path):
        for line in open(cap_path):
            if line.strip():
                r = json.loads(line)
                done[r["qid"]] = r
    todo = [q for q in qs if q["qid"] not in done]
    print(f"[d:{arm}] {len(qs)} questions -> {work} "
          f"({len(done)} already captured, {len(todo)} to run)")
    t0 = time.time()
    with open(cap_path, "a", buffering=1) as fh:
        for i, q in enumerate(todo, 1):
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
                row = _agentic_one(q, base_url, email, deadline=deadline)
            else:
                raise SystemExit(f"unknown arm {arm}")
            fh.write(json.dumps(row) + "\n")
            done[q["qid"]] = row
            if i % 10 == 0 or i == len(todo):
                print(f"  {i}/{len(todo)} ({time.time()-t0:.0f}s)")

    # Question order, not completion order, so per-query arrays align across arms.
    per = [done[q["qid"]] for q in qs if q["qid"] in done]
    out = os.path.join(work, f"{arm}.json")
    json.dump({"arm": arm, "n": len(per), "per_q": per}, open(out, "w"), indent=2)
    # Sidecar read by abstention.risk_coverage (egress) and by the paired
    # compare; before this, meta files were written by hand or not at all and
    # risk-coverage tripped its UNRECORDED EGRESS warning.
    meta = {"arm": arm, "n": len(per), "tag": os.path.basename(work),
            "base_url": base_url if arm == "agentic" else config.VLLM_URL,
            "egress": (os.getenv("MUNIN_EVAL_EGRESS", "off") if arm == "agentic"
                       else "n/a-no-tools"),
            "model": _served_model(), "git_sha": _git_sha(),
            "deadline_s": deadline if arm == "agentic" else None,
            "reasoning_effort": config.LLM_REASONING_EFFORT or None,
            "max_output_tokens": config.MAX_OUTPUT_TOKENS,
            "finished_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    json.dump(meta, open(os.path.join(work, f"{arm}.meta.json"), "w"), indent=2)
    from collections import Counter
    c = Counter(r["verdict"] for r in per)
    acc = c["correct"] / len(per) if per else 0.0
    reasons = Counter(r.get("reason") for r in per if r["verdict"] == "unparseable")
    print(f"[d:{arm}] accuracy={acc:.3f} verdicts={dict(c)}"
          + (f" unparseable_reasons={dict(reasons)}" if reasons else "")
          + f" -> {out}")
    return {"arm": arm, "accuracy": acc, "verdicts": dict(c),
            "unparseable_reasons": dict(reasons), "runs_dir": work}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--arm", required=True, choices=["bare", "rag", "agentic"])
    ap.add_argument("--n", type=int, default=100)
    ap.add_argument("--top-k", type=int, default=5)
    ap.add_argument("--base-url", default="http://127.0.0.1:8080")
    ap.add_argument("--email", default="litqa2-eval@localhost")
    ap.add_argument("--tag", default=None,
                    help="ablation_runs/<tag>/ (or env MUNIN_ABLATION_TAG). Required "
                         "for a write: never reuse a committed run's tag")
    ap.add_argument("--deadline", type=int, default=900,
                    help="agentic arm per-question wall-clock deadline, seconds")
    args = ap.parse_args()
    if not (args.tag or os.getenv("MUNIN_ABLATION_TAG")):
        raise SystemExit("--tag (or MUNIN_ABLATION_TAG) is required so a run can "
                         "never overwrite a committed run's per-query arrays")
    run(args.arm, args.n, top_k=args.top_k, base_url=args.base_url, email=args.email,
        tag=args.tag, deadline=args.deadline)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
