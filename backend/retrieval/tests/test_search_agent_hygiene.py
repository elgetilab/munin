"""
Unit tests for three defects found on the iLOV drop reproducer (2026-08-27).

The reproducer question ("what is the extinction coefficient of iLOV at
280nm?") consistently produced 20-30 tool calls and a 637k-token turn. Reading
the stored `search` result showed why the model kept going after the tool that
was supposed to answer it:

  1. EVERY corpus hit had an empty snippet. `paper_search` emits its abstract
     excerpt as `excerpt`; `_norm_corpus` read `abstract`, which paper_search
     never sets. The model got bare titles, so it could not judge relevance or
     extract a value without a follow-up `source` read.
  2. `thin_evidence` was a bare COUNT of scholarly hits, so it asserted
     "evidence is fine" while two of ten hits were ruthenium solar-cell papers
     matched on the phrase "molar extinction coefficient".
  3. The model passed `queries` (not in this tool's schema). The dispatcher
     reads arguments by name, so it vanished silently: the model believed it
     had issued a 4-query fan-out, one query ran, and nothing said otherwise.

    docker exec munin-retrieval python /app/tests/test_search_agent_hygiene.py
"""

from __future__ import annotations

import asyncio
import sys
import traceback

sys.path.insert(0, "/app")
sys.path.insert(0, ".")

from mcp.tools import search_agent as sa  # noqa: E402


def _check(name: str, ok: bool, detail: str = "") -> bool:
    print(("[PASS] " if ok else "[FAIL] ") + name + (f"  {detail}" if detail and not ok else ""))
    return ok


# --- 1. corpus snippets -----------------------------------------------------

def test_corpus_snippet_reads_excerpt() -> bool:
    """THE regression: paper_search's real output shape."""
    rows = [{"title": "T", "doi": "10.1/x", "excerpt": "abstract text here",
             "score": 0.7, "year": 2020, "authors": ["A"]}]
    out = sa._norm_corpus(rows)
    return _check("corpus snippet reads `excerpt`",
                  out[0]["snippet"] == "abstract text here", f"got {out[0]['snippet']!r}")


def test_corpus_snippet_abstract_fallback() -> bool:
    """Any other caller shape must keep working."""
    rows = [{"title": "T", "abstract": "fallback text", "score": 0.7}]
    return _check("corpus snippet falls back to `abstract`",
                  sa._norm_corpus(rows)[0]["snippet"] == "fallback text")


def test_corpus_snippet_prefers_excerpt_over_abstract() -> bool:
    rows = [{"title": "T", "excerpt": "E", "abstract": "A", "score": 0.7}]
    return _check("`excerpt` wins when both present",
                  sa._norm_corpus(rows)[0]["snippet"] == "E")


def test_corpus_snippet_missing_both_is_empty_not_crash() -> bool:
    rows = [{"title": "T", "score": 0.7}]
    return _check("no excerpt/abstract -> empty string, no raise",
                  sa._norm_corpus(rows)[0]["snippet"] == "")


def test_corpus_snippet_capped() -> bool:
    rows = [{"title": "T", "excerpt": "x" * 900, "score": 0.7}]
    return _check("snippet capped at 300",
                  len(sa._norm_corpus(rows)[0]["snippet"]) == 300)


# --- 3. the stray `queries` argument ---------------------------------------

def test_queries_argument_is_rejected() -> bool:
    """Must be an explicit, actionable error rather than a silent drop."""
    async def go():
        return await sa.search(query="q", extra_queries='["a","b"]')
    r = asyncio.run(go())
    ok = ("error" in r and "queries" in r["error"]
          and ("paper_search" in r["error"] or "web_search" in r["error"]))
    return _check("stray `queries` rejected with an actionable message", ok,
                  f"got {r!r}")


def test_queries_none_is_not_rejected() -> bool:
    """The normal path must be untouched: absent `queries` is not an error.
    Uses an empty query so we fail fast on the OTHER guard without doing any
    retrieval work (this test must not need Qdrant or the network)."""
    async def go():
        return await sa.search(query="", extra_queries=None)
    r = asyncio.run(go())
    return _check("absent `queries` does not trip the new guard",
                  "non-empty query" in (r.get("error") or ""), f"got {r!r}")


def test_empty_queries_list_is_not_rejected() -> bool:
    """An empty list is not a fan-out request; do not punish it."""
    async def go():
        return await sa.search(query="", extra_queries=[])
    r = asyncio.run(go())
    return _check("empty `queries` list is not treated as a fan-out",
                  "non-empty query" in (r.get("error") or ""), f"got {r!r}")


TESTS = [
    test_corpus_snippet_reads_excerpt,
    test_corpus_snippet_abstract_fallback,
    test_corpus_snippet_prefers_excerpt_over_abstract,
    test_corpus_snippet_missing_both_is_empty_not_crash,
    test_corpus_snippet_capped,
    test_queries_argument_is_rejected,
    test_queries_none_is_not_rejected,
    test_empty_queries_list_is_not_rejected,
]


def main() -> int:
    failed = 0
    for fn in TESTS:
        try:
            if not fn():
                failed += 1
        except Exception:
            failed += 1
            print(f"[FAIL] {fn.__name__} raised:")
            traceback.print_exc()
    print(f"\n{len(TESTS) - failed}/{len(TESTS)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
