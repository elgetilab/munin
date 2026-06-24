"""Generate the router's labelled example set (A3 step 3).

Sources, both already profile-labelled and curated:
  1. persona `prompt_suggestions` (6 per persona; `content` = the example
     query, label = the persona id).
  2. routing-eval seed items that carry `expected.profile`.

Output: router_examples.json, committed (small, provenance-tagged). The
router embeds these with BGE at load time (we commit the QUERIES, not
embeddings, so the set re-embeds cleanly if the model changes). A5 expands
this set with paraphrases later.

Run:  python backend/retrieval/router_examples_build.py
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
_PERSONAS = _REPO / "shared" / "personas"
_OUT = Path(__file__).resolve().parent / "router_examples.json"

# Make the routing-eval seed items importable.
sys.path.insert(0, str(_REPO / "backend" / "benchmarks" / "munin_bench"))

_PROFILES = {"chat", "research", "code"}

# Cross-profile label-noise exclusions. The chat persona offers paper-finding
# suggestions ("Find papers about cholesterol-lipid interactions", "Search the
# web for the latest developments in cryo-EM") that COLLIDE with near-identical
# research suggestions ("Search for papers about cholesterol-lipid
# interactions", "recent advances in cryo-electron microscopy"). Feeding
# contradictory labels to the KNN degrades it. Resolution: paper-finding /
# scientific-literature queries lean RESEARCH (consistent with the seed items,
# where citing_papers / group_corpus_qa are research); drop the ambiguous chat
# search/find suggestions. Clean chat examples are the everyday/writing ones
# (weather, define, abstract, edit, grant). Matched by a substring of the query.
_EXCLUDE_SUBSTRINGS = {
    "chat": [
        "Find papers about cholesterol",        # collides with research #1
        "Search the web for the latest developments in cryo-EM",  # vs research #5
    ],
}


def _from_prompt_suggestions() -> list[dict]:
    out = []
    for pid in ("chat", "research", "code"):
        data = json.loads((_PERSONAS / f"{pid}.json").read_text())
        excludes = _EXCLUDE_SUBSTRINGS.get(pid, [])
        for s in data.get("prompt_suggestions") or []:
            query = (s.get("content") or s.get("title") or "").strip()
            if not query:
                continue
            if any(ex in query for ex in excludes):
                continue  # ambiguous cross-profile example, see note above
            out.append({"query": query, "profile": pid,
                        "source": f"prompt_suggestion:{pid}"})
    return out


def _from_seed_items() -> list[dict]:
    from routing.routing_eval import SEED_ITEMS  # noqa: E402
    out = []
    for it in SEED_ITEMS:
        if it.expected.profile in _PROFILES:
            out.append({"query": it.query.strip(),
                        "profile": it.expected.profile,
                        "source": f"seed_item:{it.id}"})
    return out


def main() -> int:
    examples = _from_prompt_suggestions() + _from_seed_items()
    # de-dup on (query, profile)
    seen, deduped = set(), []
    for e in examples:
        k = (e["query"], e["profile"])
        if k not in seen:
            seen.add(k)
            deduped.append(e)

    by_profile: dict[str, int] = {}
    for e in deduped:
        by_profile[e["profile"]] = by_profile.get(e["profile"], 0) + 1

    doc = {
        "meta": {
            "purpose": "A3 router KNN labelled example set (profile = chat|research|code)",
            "sources": ["persona prompt_suggestions", "routing-eval seed items with expected.profile"],
            "note": "Committed queries only; the router embeds with BGE at load. A5 expands this.",
            "counts": {"total": len(deduped), **by_profile},
        },
        "examples": deduped,
    }
    _OUT.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n")
    print(f"wrote {_OUT}: {len(deduped)} examples {by_profile}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
