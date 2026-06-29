"""Regenerate the AgentRetriever frozen variant set from a queries file.

This is a PROVENANCE tool, not a reproduction one: vLLM greedy decoding is not
bit-reproducible across model versions, so the committed JSON is the benchmark
input of record and this script documents how it was made. Run it ONCE when
``data/local/queries.jsonl`` exists (Phase 4b), commit the output, and tag the
header with the generating model + prompt SHA.

The expansion mirrors backend/retrieval/mcp/tools/query_expansion.py
(EXPANSION_SYSTEM_PROMPT verbatim, n=5 = base + 4 variants, temp 0.5, thinking
disabled). Base query is always element 0.

Usage:
    python -m munin_bench.frozen_variants.regen_variants \\
        --queries data/local/queries.jsonl \\
        --out munin_bench/frozen_variants/local_pool_variants.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import urllib.request
from datetime import datetime, timezone

from .. import config

# VERBATIM from query_expansion.py::EXPANSION_SYSTEM_PROMPT.
EXPANSION_SYSTEM_PROMPT = (
    "You are a search query expansion helper. Given one base query, return a "
    "JSON array of 3 to 5 alternative search queries that approach the topic "
    "from different angles. Vary phrasing, synonyms, and specificity: include "
    "one broader query, one narrower query, and at least one that uses "
    "different technical terms. Do not add commentary. Return ONLY a JSON "
    "array of strings."
)

PROMPT_SHA = hashlib.sha256(EXPANSION_SYSTEM_PROMPT.encode()).hexdigest()[:16]


def _parse_variants(raw: str) -> list[str]:
    """Mirror query_expansion._parse_variants: strip <think>, grab the first
    JSON array, fall back to line-splitting."""
    if not raw:
        return []
    raw = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL).strip()
    match = re.search(r"\[[^\[\]]*\]", raw, flags=re.DOTALL)
    if match:
        try:
            parsed = json.loads(match.group(0))
            if isinstance(parsed, list):
                return [
                    str(x).strip()
                    for x in parsed
                    if isinstance(x, (str, int, float)) and str(x).strip()
                ]
        except json.JSONDecodeError:
            pass
    out = []
    for line in raw.splitlines():
        s = line.strip().lstrip("-*0123456789. ").strip().strip("\"'`,")
        if s:
            out.append(s)
    return out


def expand_query(base: str, n: int = config.AGENT_VARIANT_N) -> list[str]:
    """Expand one base query to up to ``n`` variants (base first)."""
    base = (base or "").strip()
    if not base:
        return []
    target = max(1, n - 1)
    user = (
        f"Base query: {base}\n\n"
        f"Return {target} alternative queries as a JSON array of strings. "
        "Do not repeat the base query in the array."
    )
    payload = {
        "model": config.VLLM_MODEL_NAME,
        "messages": [
            {"role": "system", "content": EXPANSION_SYSTEM_PROMPT},
            {"role": "user", "content": user},
        ],
        "max_tokens": 250,
        "temperature": 0.5,
        "stream": False,
        "chat_template_kwargs": {"enable_thinking": False},
    }
    req = urllib.request.Request(
        f"{config.VLLM_URL}/v1/chat/completions",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            data = json.loads(r.read())
        content = (data["choices"][0].get("message") or {}).get("content") or ""
    except Exception as e:
        print(f"[warn] expansion failed for {base!r}: {e}", file=sys.stderr)
        return [base]

    variants = _parse_variants(content)
    seen = {base.lower()}
    ordered = [base]
    for v in variants:
        if v.lower() in seen:
            continue
        seen.add(v.lower())
        ordered.append(v)
        if len(ordered) >= n:
            break
    return ordered


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--queries", required=True, help="queries.jsonl (qid, query)")
    ap.add_argument("--out", required=True, help="output frozen variants JSON")
    ap.add_argument("--n", type=int, default=config.AGENT_VARIANT_N)
    args = ap.parse_args()

    variants: dict[str, list[str]] = {}
    with open(args.queries) as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            q = row["query"]
            variants[q] = expand_query(q, args.n)
            print(f"  {row.get('qid', '?')}: {len(variants[q])} variants")

    out = {
        "meta": {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "model": config.VLLM_MODEL_NAME,
            "prompt_sha16": PROMPT_SHA,
            "n": args.n,
            "temperature": 0.5,
            "note": "Provenance only; vLLM greedy decode is not bit-reproducible.",
        },
        "variants": variants,
    }
    with open(args.out, "w") as fh:
        json.dump(out, fh, indent=2, ensure_ascii=False)
    print(f"wrote {len(variants)} queries -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
