"""Apply a post-answer harness step to stored answers: a paired, replay-free arm.

A step that only rewrites the final answer (today: citecheck in enforce mode)
can be measured on answers already captured by `run`, so the two arms differ
in that step and nothing else: no regeneration noise. Answers the step leaves
unchanged keep their earlier verdict, so only changed answers cost a judge
call.

    python -m munin_bench.userfail.postfix --src 2026-11-23_sage-lk030 \\
        --out 2026-11-23_sage-lk030+cc [--ids uf-a,uf-b]

Then `run recheck`, `run judge` and `run score` on --out, and `run compare`
src vs out. The correction call goes to the backbone at MUNIN_VLLM_URL
(default http://127.0.0.1:8000, production vLLM on the cluster host) with the
same request production sends.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import httpx

from .cases import load_cases
from .run import BENCH, _jsonl, run_checks, store_dir

RETRIEVAL = BENCH.parent / "retrieval"


def _citecheck():
    sys.path.insert(0, str(RETRIEVAL))
    import citecheck  # noqa: E402
    return citecheck


def _host_correct(cc, vllm: str, model: str):
    """citecheck._correct against the host's vLLM, same payload as production
    (thinking off via chat_template_kwargs, as LLM_THINKING_MODE=enable_thinking)."""
    async def correct(content, bad, ungrounded):
        body = {"model": model,
                "messages": [{"role": "system", "content": cc.CORRECT_SYSTEM},
                             {"role": "user", "content": cc.correction_prompt(content, bad, ungrounded)}],
                "max_tokens": min(16000, len(content) // 3 + 1500), "temperature": 0.2,
                "stream": False, "chat_template_kwargs": {"enable_thinking": False}}
        async with httpx.AsyncClient(timeout=300) as client:
            r = await client.post(f"{vllm}/v1/chat/completions", json=body)
            r.raise_for_status()
            choice = (r.json().get("choices") or [{}])[0]
        if choice.get("finish_reason") == "length":
            return None
        text = ((choice.get("message") or {}).get("content") or "").strip()
        return text or None
    return correct


async def _apply(cc, recs: dict, ids, mode: str) -> dict:
    out = {}
    for i, r in recs.items():
        r = json.loads(json.dumps(r))
        if (ids is None or i in ids) and r.get("turns") and not r.get("error"):
            final = r["turns"][-1]
            before = final.get("answer") or ""
            after, notes = await cc.run(before, [], mode_override=mode)
            final["answer"] = after
            r["postfix"] = {"step": f"citecheck:{mode}", "changed": after != before, **notes}
            print(f"[postfix] {i}: {notes} {'CHANGED' if after != before else ''}", flush=True)
        out[i] = r
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="munin_bench.userfail.postfix")
    ap.add_argument("--src", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--ids", type=lambda s: set(s.split(",")), default=None)
    ap.add_argument("--mode", default="enforce", choices=("warn", "enforce"))
    ap.add_argument("--vllm", default=os.environ.get("MUNIN_VLLM_URL", "http://127.0.0.1:8000"))
    args = ap.parse_args(argv)

    store = store_dir()
    src, dst = store / "runs" / args.src, store / "runs" / args.out
    if dst.exists():
        print(f"[postfix] {dst} exists; pick a new --out")
        return 1
    model = httpx.get(f"{args.vllm}/v1/models", timeout=10).json()["data"][0]["id"]
    cc = _citecheck()
    cc._correct = _host_correct(cc, args.vllm, model)

    recs = _jsonl(src / "records.jsonl")
    if args.ids is not None:
        recs = {i: r for i, r in recs.items() if i in args.ids}
    fixed = asyncio.run(_apply(cc, recs, args.ids, args.mode))
    cases = {c.id: c for c in load_cases(store)}
    for i, r in fixed.items():
        if i in cases and r.get("turns"):
            r["checks"] = run_checks(cases[i].checks, r)
            r["mech_pass"] = all(c["pass"] for c in r["checks"])

    dst.mkdir(parents=True)
    meta = json.loads((src / "meta.json").read_text())
    sha = subprocess.run(["git", "-C", str(BENCH), "rev-parse", "--short=12", "HEAD"],
                         capture_output=True, text=True).stdout.strip()
    meta.update(tag=args.out, postfix={"src": args.src, "step": f"citecheck:{args.mode}",
                                       "git_sha": sha, "vllm_model": model,
                                       "at": datetime.now(timezone.utc).isoformat()})
    meta["harness"] = {**(meta.get("harness") or {}), "postfix": f"citecheck:{args.mode}@{sha}"}
    (dst / "meta.json").write_text(json.dumps(meta, indent=1))
    (dst / "records.jsonl").write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in fixed.values()))
    # Unchanged answers keep their verdict: the judge saw exactly this text.
    judged = _jsonl(src / "judged.jsonl")
    keep = [v for i, v in judged.items() if i in fixed and not (fixed[i].get("postfix") or {}).get("changed")]
    (dst / "judged.jsonl").write_text("".join(json.dumps(v, ensure_ascii=False) + "\n" for v in keep))
    n = sum(1 for r in fixed.values() if (r.get("postfix") or {}).get("changed"))
    print(f"[postfix] {len(fixed)} records, {n} changed, {len(keep)} verdicts carried over -> {dst}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
