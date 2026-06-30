"""Local-pool pipeline (RETRIEVAL-EVAL-SPEC Phase 4).

Modes:
  --extract-queries  (4a)  pull candidate user queries from the chat store
                           into data/local/candidates.jsonl for varghele to
                           curate into queries.jsonl (4b).
  --build-pool       (4c)  [not yet] run the retriever sweep over the curated
                           queries.jsonl and write the judging pool.

PRIVACY: candidates.jsonl contains real user message text. It lives under
data/local/ which is gitignored and never leaves the cluster. Do not commit it.

Phase 4a usage:
    python -m munin_bench.pipelines.build_pool --extract-queries \\
        --db /opt/munin/data/chats.db --sample 300 --seed 42
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sqlite3

DEFAULT_DB = os.getenv("CHATS_DB_PATH", "/opt/munin/data/chats.db")
DATA_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "data", "local")
)
MIN_QUERY_CHARS = 20
SKIP_PREFIXES = ("/", "<")


def _paper_search_count(tool_calls_json: str) -> int:
    """Number of papers a turn's paper_search call(s) returned, or -1 if the
    turn made no paper_search call (so the caller can skip it)."""
    try:
        calls = json.loads(tool_calls_json) if tool_calls_json else []
    except (json.JSONDecodeError, TypeError):
        return -1
    if not isinstance(calls, list):
        return -1
    made_call = False
    total = 0
    for c in calls:
        if not isinstance(c, dict) or c.get("name") != "paper_search":
            continue
        made_call = True
        result = c.get("result") or {}
        results = result.get("results") if isinstance(result, dict) else None
        if isinstance(results, list):
            total += len(results)
    return total if made_call else -1


def extract_candidates(db_path: str):
    """Yield candidate dicts: a user message whose NEXT assistant turn made a
    paper_search call (heuristics from spec 4a applied)."""
    db = sqlite3.connect(db_path)
    db.row_factory = sqlite3.Row
    cur = db.cursor()
    rows = cur.execute(
        "SELECT conversation_id, index_in_conversation, role, content, "
        "tool_calls, created_at FROM messages "
        "ORDER BY conversation_id, index_in_conversation"
    ).fetchall()

    # group by conversation, preserve order
    by_conv: dict[str, list[sqlite3.Row]] = {}
    for r in rows:
        by_conv.setdefault(r["conversation_id"], []).append(r)

    for conv_id, msgs in by_conv.items():
        for i, m in enumerate(msgs):
            if m["role"] != "user":
                continue
            text = (m["content"] or "").strip()
            if len(text) < MIN_QUERY_CHARS or text.startswith(SKIP_PREFIXES):
                continue
            # first assistant turn after this user message
            nxt = next((x for x in msgs[i + 1:] if x["role"] == "assistant"), None)
            if nxt is None:
                continue
            n_papers = _paper_search_count(nxt["tool_calls"])
            if n_papers < 0:  # no paper_search -> nothing to evaluate
                continue
            yield {
                "text": text,
                "conversation_id": conv_id,
                "timestamp": m["created_at"],
                "n_papers_retrieved_originally": n_papers,
            }
    db.close()


def run_extract(db_path: str, sample_n: int, seed: int) -> str:
    cands = list(extract_candidates(db_path))

    # dedupe exact-duplicate user text (same query asked twice), keep first
    seen, deduped = set(), []
    for c in cands:
        key = c["text"].lower()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(c)

    rng = random.Random(seed)
    if len(deduped) > sample_n:
        deduped = rng.sample(deduped, sample_n)
    else:
        rng.shuffle(deduped)

    deduped.sort(key=lambda c: c["timestamp"])
    for i, c in enumerate(deduped, 1):
        c_qid = {"qid": f"q{i:04d}"}
        c_qid.update(c)
        deduped[i - 1] = c_qid

    os.makedirs(DATA_DIR, exist_ok=True)
    out = os.path.join(DATA_DIR, "candidates.jsonl")
    with open(out, "w") as fh:
        for c in deduped:
            fh.write(json.dumps(c, ensure_ascii=False) + "\n")

    print(f"candidates written: {len(deduped)} -> {out}")
    if len(deduped) < 100:
        print(
            f"  [WARN] only {len(deduped)} candidates (spec 4b targets 100-200 "
            "final queries). The corpus of paper_search chat turns is still "
            "small; either gather more usage before curating, or proceed with a "
            "smaller pool and report n honestly."
        )
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--extract-queries", action="store_true")
    ap.add_argument("--build-pool", action="store_true")
    ap.add_argument("--db", default=DEFAULT_DB)
    ap.add_argument("--sample", type=int, default=300)
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    if args.extract_queries:
        run_extract(args.db, args.sample, args.seed)
        return 0
    if args.build_pool:
        raise SystemExit("--build-pool is Phase 4c (needs curated queries.jsonl); not built yet")
    raise SystemExit("specify --extract-queries (4a) or --build-pool (4c)")


if __name__ == "__main__":
    raise SystemExit(main())
