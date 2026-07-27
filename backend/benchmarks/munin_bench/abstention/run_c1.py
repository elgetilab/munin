"""Track C1 runner: drive the live chat on the fabricated set, classify, score.

For each fabricated/unanswerable item, asks the live research chat the question
and records the final answer + tool calls, then classifies (correct_abstain vs
confabulation) via `detect`. Headline: abstain rate (Munin correctly refused
X% of nonexistent papers) + confabulated-local-citation rate (fully automatic).

Capture is written incrementally + resumably to c1_runs/ (gitignored); the
committed record is the scorecard.

    PYTHONPATH=$HOME/.cache/munin_bench_deps:. \
    /opt/munin/services/pipeline/venv/bin/python -m munin_bench.abstention.run_c1 \
      --base-url http://127.0.0.1:8080 --email litqa2-eval@localhost --date 2026-07-10
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

from ..metrics.bootstrap import single_bootstrap
from .detect import classify

_HERE = os.path.dirname(__file__)
_SET = os.path.join(_HERE, "fabricated_abstention.json")


def _ask(base_url: str, email: str, question: str, deadline: int = 900) -> dict:
    """Ask one fabricated-paper question.

    `deadline` was 240s until 2026-07-27. That predated `source(mode=qa)`
    full-text reading (~20s/read) and the agent architecture, so on the current
    harness a 240s cap truncates mid-reasoning. A truncated response has no
    abstention marker, so `detect` scores it as "did not abstain" -> an
    inflated confabulation rate. Same failure litqa2_runner hit at 300s and
    fixed on 2026-07-24; matched to its 900s here.
    """
    body = {"persona": "research", "ephemeral": True,
            "messages": [{"role": "user", "content": question}]}
    req = urllib.request.Request(
        base_url.rstrip("/") + "/api/chat/completions",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "X-Munin-Email": email,
                 "X-Munin-Ephemeral": "true", "Accept": "text/event-stream",
                 # Egress must be EXPLICIT. Until 2026-07-27 this runner sent no
                 # header at all, so it silently inherited the server-side
                 # default of "full" (main.py) while run_c2 -> litqa2_runner
                 # defaulted to "off". The two abstention arms were therefore
                 # not comparable to each other, nor to the ablation (which
                 # runs "full"). Set MUNIN_EVAL_EGRESS explicitly; "full" is
                 # required for any run that shares a figure with the ablation.
                 "X-Munin-Egress": os.getenv("MUNIN_EVAL_EGRESS", "off")},
        method="POST")
    content = ""
    tools: list[str] = []
    ev = None
    start = time.time()
    resp = urllib.request.urlopen(req, timeout=60)
    try:
        for raw in resp:
            if time.time() - start > deadline:
                break
            line = raw.decode("utf-8", "replace").rstrip("\n")
            if line.startswith("event:"):
                ev = line[6:].strip()
                continue
            if not line.startswith("data:"):
                continue
            d = line[5:].strip()
            if not d or d == "[DONE]":
                continue
            try:
                obj = json.loads(d)
            except Exception:
                continue
            if ev == "token" and isinstance(obj, dict) and obj.get("content"):
                content += obj["content"]
            elif ev == "tool_call" and isinstance(obj, dict):
                tools.append(obj.get("name"))
            if ev == "done":
                break
    finally:
        resp.close()
    return {"answer": content, "tool_calls": tools}


def _git_sha() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "--short", "HEAD"]).decode().strip()
    except Exception:
        return "unknown"


def run(base_url: str, email: str, *, limit: int = 0, concurrency: int = 1,
        deadline: int = 900,
        date: str | None = None, out_dir: str | None = None) -> dict:
    payload = json.load(open(_SET))
    items = payload["items"]
    if limit:
        items = items[:limit]
    work = out_dir or os.path.join(_HERE, "..", "..", "c1_runs")
    os.makedirs(work, exist_ok=True)
    cap_path = os.path.join(work, "c1.capture.jsonl")
    done = set()
    if os.path.exists(cap_path):
        done = {json.loads(l)["id"] for l in open(cap_path) if l.strip()}
    todo = [it for it in items if it["id"] not in done]
    print(f"[c1] {len(todo)} items to run ({len(done)} present), concurrency={concurrency}")

    def one(it: dict) -> dict:
        try:
            res = _ask(base_url, email, it["question"], deadline=deadline)
        except Exception as e:
            return {**it, "answer": "", "tool_calls": [], "error": type(e).__name__}
        return {**it, **res}

    t0 = time.time()
    with open(cap_path, "a", buffering=1) as fh, ThreadPoolExecutor(max_workers=concurrency) as ex:
        futs = {ex.submit(one, it): it for it in todo}
        for i, fut in enumerate(as_completed(futs), 1):
            fh.write(json.dumps(fut.result()) + "\n")
            if i % 10 == 0 or i == len(todo):
                print(f"  ran {i}/{len(todo)} ({time.time()-t0:.0f}s)")

    caps = [json.loads(l) for l in open(cap_path) if l.strip()]
    per = []
    for c in caps:
        cls = classify(c.get("answer", ""), asked_doi=c.get("doi"))
        per.append({"id": c["id"], "kind": c["kind"], "doi": c.get("doi"),
                    "verdict": cls["verdict"], "abstained": cls["abstained"],
                    "cited_in_corpus": cls["cited_in_corpus"],
                    "tool_calls": len(c.get("tool_calls", [])),
                    "answer_words": cls["answer_words"],
                    "answer_tail": (c.get("answer", "") or "")[-400:]})

    from collections import Counter
    verdicts = Counter(p["verdict"] for p in per)
    n = len(per)
    abstain = single_bootstrap([1.0 if p["abstained"] else 0.0 for p in per]) if per else None
    n_confab_cite = sum(1 for p in per if p["verdict"] == "confabulated_local_cite")
    n_confab = sum(1 for p in per if p["verdict"] in ("confabulated_local_cite", "possible_confabulation"))

    sc = {
        "track": "abstention-c1-fabricated",
        "n": n, "git_sha": _git_sha(), "date": date, "seed": 42,
        "generator_base_url": base_url,
        "set_meta": payload["meta"],
        "verdicts": dict(verdicts),
        "abstain_rate": abstain,
        "confabulation_rate": (n_confab / n) if n else None,
        "confabulated_local_cite": n_confab_cite,
        "per_item": per,
    }
    if date:
        out = os.path.join(_HERE, "..", "..", "scorecards",
                           f"{date}_abstention-c1-fabricated.json")
        os.makedirs(os.path.dirname(out), exist_ok=True)
        json.dump(sc, open(out, "w"), indent=2)
        print(f"[c1] scorecard -> {os.path.abspath(out)}")

    print("\n=== Track C1 abstention (fabricated papers) ===")
    print(f"n={n} | verdicts={dict(verdicts)}")
    if abstain:
        print(f"ABSTAIN (correct refusal) = {abstain['mean']:.3f} "
              f"[{abstain['ci_low']:.3f}, {abstain['ci_high']:.3f}]")
    print(f"confabulation rate = {sc['confabulation_rate']:.3f} "
          f"(incl. {n_confab_cite} confabulated LOCAL citations)")
    return sc


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default="http://127.0.0.1:8080")
    ap.add_argument("--email", default=os.getenv("MUNIN_BENCH_EMAIL", "litqa2-eval@localhost"))
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--concurrency", type=int, default=1)
    ap.add_argument("--deadline", type=int, default=900,
                    help="wall-clock cap per question (s); 900 matches litqa2_runner")
    ap.add_argument("--date", default=None)
    args = ap.parse_args()
    run(args.base_url, args.email, limit=args.limit,
        concurrency=args.concurrency, date=args.date, deadline=args.deadline)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
