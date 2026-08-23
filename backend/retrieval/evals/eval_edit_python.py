#!/usr/bin/env python3
"""Paired A/B: does exposing edit_python stop the model re-pasting whole scripts?

Companion to docs/agent-track/CODE-EDIT-TOOL-PLAN.md. Measured result
(2026-08-23, pooled n=189/arm): re-paste 29.1% -> 18.0% (p=0.015),
edit_python adoption 0% -> 6.9% (p=0.0002).

Run INSIDE the retrieval container, which is where the tokenizer, personas and
vLLM client are all already initialised:

    docker exec munin-retrieval python3 /app/evals/eval_edit_python.py \
        --limit 45 --reps 3 --seed 7

THREE DESIGN POINTS THAT DECIDE WHETHER THE NUMBER MEANS ANYTHING:

1. Cases are EVERY point where a run_python returned and the model then made
   another tool call, NOT just the ones that re-pasted. Selecting on the
   behaviour under test would pin the baseline at 100% and make any result look
   like a win. The true production baseline over this population is 40.6%
   (the 56% quoted early in the work counted only run_python->run_python pairs
   and was inflated by excluding every other continuation).

2. The arms differ in the request, not the deployment: `tools` with and without
   edit_python, plus the edit_hint. So the comparison stays valid after deploy
   and does not need a pre-deploy snapshot.

3. The after-arm INJECTS the edit_hint that deployed run_python now returns.
   The replayed historical results predate the tool (0 of 26 carry it), and a
   first run without this measured only whether the model discovers edit_python
   from the schema unprompted: adoption 1.7% vs 6.7% with the hint. Removing
   the injection silently changes what is being measured.

KNOWN LIMITS. Absolute rates run below production (~29% vs 40.6%) because
collapsing a turn's calls into one assistant message makes the model likelier
to treat the task as finished; read the RELATIVE move, not the levels. The
harness sends a fixed max_tokens and does not call chat_context.fit_max_tokens,
so a few oversized prefixes come back as vLLM 400s; they are identical in both
arms and are reported as their own outcome rather than folded into the rates.

Nothing is executed. One step is generated and the chosen tool is read, so
there is no sandbox execution, no egress, and no writes to any store.
"""
from __future__ import annotations
import argparse, asyncio, difflib, json, random, sqlite3, sys
sys.path.insert(0, "/app")

import chat_service as cs
import personas
from vllm_client import vllm_post_json, VLLMRequestError
from database import VLLM_MODEL_NAME

personas.load_personas()
CODE = personas.get_persona("code")
SYSTEM = personas.build_system_prompt(CODE)
FULL_TOOLS = cs._openai_tools_schema(CODE)
BASE_TOOLS = [t for t in FULL_TOOLS if t["function"]["name"] != "edit_python"]
assert len(FULL_TOOLS) == len(BASE_TOOLS) + 1, "edit_python must be in the code schema"

MAX_SRC_CHARS = 12000  # keep prefixes bounded; skip pathological outliers


def load_cases(limit: int, seed: int) -> list[dict]:
    conn = sqlite3.connect("file:/data/chats.db?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "select conversation_id, index_in_conversation, tool_calls from messages "
        "where tool_calls is not null and created_at >= '2026-06-01'"
    ).fetchall()
    cases = []
    for r in rows:
        tcs = json.loads(r["tool_calls"])
        for i in range(len(tcs) - 1):
            if tcs[i].get("name") != "run_python":
                continue
            src = (tcs[i].get("arguments") or {}).get("code") or ""
            if not src.strip() or len(src) > MAX_SRC_CHARS:
                continue
            cases.append({
                "conv": r["conversation_id"],
                "idx": r["index_in_conversation"],
                "pos": i,
                "calls": tcs[: i + 1],
                "base_src": src,
                "actual_next": tcs[i + 1].get("name"),
            })
    random.Random(seed).shuffle(cases)
    return cases[:limit]


EDIT_HINT = (
    "To change part of this code, call edit_python with the lines to "
    "replace. Do not re-send the whole script; the kernel state persists."
)


def build_prefix(case: dict, include_hint: bool = False) -> list[dict]:
    """system + prior user text + assistant(tool_calls so far) + their results.

    ``include_hint`` reproduces what the DEPLOYED run_python now returns. The
    historical results replayed here predate the tool, so without this the
    eval only measures whether the model discovers edit_python from the schema
    on its own, never testing the adoption mechanism that was actually built.
    """
    conn = sqlite3.connect("file:/data/chats.db?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    prior = conn.execute(
        "select role, content from messages where conversation_id=? and "
        "index_in_conversation < ? order by index_in_conversation",
        (case["conv"], case["idx"]),
    ).fetchall()
    msgs = [{"role": "system", "content": SYSTEM}]
    for p in prior:
        if p["role"] in ("user", "assistant") and (p["content"] or "").strip():
            msgs.append({"role": p["role"], "content": p["content"]})
    if not any(m["role"] == "user" for m in msgs):
        msgs.append({"role": "user", "content": "Please continue."})
    msgs.append({
        "role": "assistant", "content": "",
        "tool_calls": [{
            "id": c.get("id") or f"c{n}", "type": "function",
            "function": {"name": c.get("name"),
                         "arguments": json.dumps(c.get("arguments"))},
        } for n, c in enumerate(case["calls"])],
    })
    last = len(case["calls"]) - 1
    for n, c in enumerate(case["calls"]):
        result = c.get("result")
        if include_hint and n == last and isinstance(result, dict):
            result = dict(result)
            result["edit_hint"] = EDIT_HINT
        msgs.append({
            "role": "tool", "tool_call_id": c.get("id") or f"c{n}",
            "content": cs.truncate_tool_result(result),
        })
    return msgs


def classify(choice: dict, base_src: str) -> str:
    tcs = (choice.get("message") or {}).get("tool_calls") or []
    if not tcs:
        return "no_tool"
    fn = (tcs[0] or {}).get("function") or {}
    name = fn.get("name")
    if name == "edit_python":
        return "edit_python"
    if name != "run_python":
        return "other_tool"
    raw = fn.get("arguments") or ""
    try:
        code = json.loads(raw).get("code") or ""
    except Exception:
        code = raw  # truncated by max_tokens; the prefix still classifies it
    n = min(len(code), len(base_src), 1500)
    if n < 80:
        return "run_python_short"
    ratio = difflib.SequenceMatcher(None, base_src[:n], code[:n]).quick_ratio()
    return "run_python_repaste" if ratio > 0.8 else "run_python_new"


async def one(msgs: list[dict], tools: list[dict], seed: int) -> str | None:
    body = {
        "model": VLLM_MODEL_NAME, "messages": msgs, "tools": tools,
        "tool_choice": "auto", "stream": False,
        "max_tokens": 1200, "temperature": 0.7, "seed": seed,
    }
    try:
        resp = await vllm_post_json(body, timeout=180.0, purpose="eval_edit_python")
    except VLLMRequestError as e:
        return f"ERROR:{str(e)[:60]}"
    choices = resp.get("choices") or []
    return choices[0] if choices else None


async def main_async(limit: int, reps: int, seed: int) -> int:
    cases = load_cases(limit, seed)
    print(f"cases: {len(cases)}  reps: {reps}  (concurrency 1, nothing executed)\n")
    tally = {"before": {}, "after": {}}
    per_case = []
    for ci, case in enumerate(cases, 1):
        prefix = {
            "before": build_prefix(case, include_hint=False),
            "after": build_prefix(case, include_hint=True),
        }
        row = {"conv": case["conv"][:8], "before": [], "after": []}
        for arm, tools in (("before", BASE_TOOLS), ("after", FULL_TOOLS)):
            msgs = prefix[arm]
            for rep in range(reps):
                choice = await one(msgs, tools, seed=seed * 1000 + ci * 10 + rep)
                if isinstance(choice, str):
                    label = choice
                elif choice is None:
                    label = "no_choice"
                else:
                    label = classify(choice, case["base_src"])
                tally[arm][label] = tally[arm].get(label, 0) + 1
                row[arm].append(label)
        per_case.append(row)
        print(f"  [{ci:2}/{len(cases)}] {row['conv']}  before={row['before']}  after={row['after']}",
              flush=True)

    print("\n=== tallies ===")
    labels = sorted(set(tally["before"]) | set(tally["after"]))
    n = len(cases) * reps
    print(f"{'outcome':22}{'before':>10}{'after':>10}")
    for lb in labels:
        print(f"{lb:22}{tally['before'].get(lb,0):>10}{tally['after'].get(lb,0):>10}")
    for arm in ("before", "after"):
        rp = tally[arm].get("run_python_repaste", 0)
        print(f"{arm:6} re-paste rate: {100*rp/n:.1f}%   "
              f"edit_python: {100*tally[arm].get('edit_python',0)/n:.1f}%")
    json.dump({"tally": tally, "per_case": per_case, "n": n},
              open("/tmp/eval_edit_python_result.json", "w"), indent=2)
    print("\nraw -> /tmp/eval_edit_python_result.json")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=20)
    ap.add_argument("--reps", type=int, default=3)
    ap.add_argument("--seed", type=int, default=42)
    a = ap.parse_args()
    sys.exit(asyncio.run(main_async(a.limit, a.reps, a.seed)))
