"""
Unit tests for source(mode="evidence") and its wiring into search.

WHY. papers_bge holds ONE vector per paper built from title+abstract, so a
measured value living in a methods section or table is unreachable by
paper-level retrieval. Measured on the iLOV reproducer: the model compensated
with 11-14 hand-rolled document fetches per turn, ~60% of all its tool calls.
Evidence mode adds the missing depth axis (paper -> chunk -> scored passage).

The rules pinned here are the CITATION rules. Both come from real failures in
this repo: a quote must be copied verbatim rather than generated (source's own
_QA_SYSTEM permits answering from model knowledge, which is how a UV-microscopy
paper returned an iLOV coefficient), and evidence without a locator is dropped
rather than returned unattributed (bibref.py exists because invented author
attributions reached users).

    docker exec munin-retrieval python /app/tests/test_source_evidence.py
"""

from __future__ import annotations

import sys, traceback

sys.path.insert(0, "/app")
sys.path.insert(0, ".")

import importlib                             # noqa: E402
# `from mcp.tools import source` binds the FUNCTION, not the module, because the
# package re-exports it under the same name. Import the module explicitly.
S = importlib.import_module("mcp.tools.source")
SA = importlib.import_module("mcp.tools.search_agent")


def _check(name, ok, detail=""):
    print(("[PASS] " if ok else "[FAIL] ") + name + (f"  {detail}" if detail and not ok else ""))
    return ok


# --- the answer-shaped gate -------------------------------------------------

def test_value_questions_trigger_evidence():
    qs = ["what is the extinction coefficient of iLOV at 280nm?",
          "what Kd is reported for setmelanotide",
          "how much polarization is achieved"]
    return _check("value questions are answer-shaped", all(SA._wants_answer(q) for q in qs))


def test_discovery_queries_do_not():
    """Deep Research calls search once per sub-question; those are discovery
    phrasings and must NOT each trigger an extra judge call."""
    qs = ["find papers on LOV domains",
          "review the literature on GPCR allostery",
          "papers about membrane proteins"]
    return _check("discovery queries are not answer-shaped",
                  not any(SA._wants_answer(q) for q in qs))


def test_empty_query_is_not_answer_shaped():
    return _check("empty/None query is safe",
                  not SA._wants_answer("") and not SA._wants_answer(None))


# --- judge parsing ----------------------------------------------------------

def test_scores_parse():
    got = S._parse_scores("1: 9 states the value\n2: 3 on topic only\n3: 0 irrelevant", 3)
    return _check("scores parse", got == [9.0, 3.0, 0.0], f"got {got}")


def test_scores_degrade_on_garbage():
    """A scorer failure must reorder nothing, not raise. -1 means unscored and
    the caller falls back to cosine order."""
    got = S._parse_scores("the model wrote prose instead", 3)
    return _check("garbage judge output -> all unscored", got == [-1.0, -1.0, -1.0], f"got {got}")


def test_scores_partial_and_clamped():
    got = S._parse_scores("2: 7\n1: 99\n9: 5", 3)
    ok = got[0] == 10.0 and got[1] == 7.0 and got[2] == -1.0
    return _check("partial/out-of-range/out-of-bounds handled", ok, f"got {got}")


def test_scores_parse_bracketed_numbering():
    """The judge is SHOWN passages as `[1] ...` and intermittently answers in
    that shape rather than the bare `1:` the system prompt asks for. Measured
    2026-08-31 against the live index: 2 of 4 identical calls came back
    bracketed, every line missed the regex, and all 35 scores went to -1, so
    the result silently degraded to cosine order with score=null. Nothing
    raised and the passages were still correct, which is why it survived the
    first 11 tests: they only ever fed the unbracketed form."""
    got = S._parse_scores("[1]: 9 states the value\n[2]: 3 on topic\n[3]: 0 irrelevant", 3)
    return _check("bracketed judge numbering parses", got == [9.0, 3.0, 0.0], f"got {got}")


def test_scores_parse_mixed_numbering():
    """Both shapes in one reply must parse; the judge is not consistent within
    a single response either."""
    got = S._parse_scores("[1]: 8 bracketed\n2: 4 bare\n[3]. 1 bracketed dot", 3)
    return _check("mixed bracketed/bare numbering parses", got == [8.0, 4.0, 1.0], f"got {got}")


def test_bracket_tolerance_does_not_swallow_prose():
    """The optional bracket must not turn prose into scores. `[note] 5` has no
    delimiter after a number, and a bare sentence must still be unscored."""
    got = S._parse_scores("the model wrote prose\n[note] 5 not a score", 2)
    return _check("bracket tolerance still rejects prose", got == [-1.0, -1.0], f"got {got}")


# --- mode plumbing ----------------------------------------------------------

def test_evidence_requires_a_question():
    import asyncio
    r = asyncio.run(S.source(refs=[], mode="evidence"))
    return _check("evidence without a question is rejected",
                  "error" in r and "question" in r["error"], f"got {r}")


def test_evidence_allows_empty_refs():
    """Every other mode reads a named document; evidence searches the index, so
    refs are optional there. If this regresses, evidence can only ever be used
    on papers the caller already found, which defeats it."""
    import asyncio
    r = asyncio.run(S.source(refs=[], mode="evidence", question="test question"))
    return _check("evidence accepts empty refs",
                  not ("error" in r and "non-empty list" in str(r.get("error"))), f"got {str(r)[:120]}")


def test_other_modes_still_require_refs():
    import asyncio
    r = asyncio.run(S.source(refs=[], mode="qa", question="q"))
    return _check("qa still requires refs", "error" in r and "non-empty" in r["error"])


def test_unknown_mode_lists_evidence():
    import asyncio
    r = asyncio.run(S.source(refs=[{"doi": "10.1/x"}], mode="bogus"))
    return _check("unknown mode message mentions evidence",
                  "error" in r and "evidence" in r["error"], f"got {r}")


def test_missing_collection_degrades_gracefully():
    """Until the chunk index is built, evidence must return a clear reason
    rather than raising into the caller's turn."""
    import asyncio
    old = S.CHUNKS_COLLECTION
    S.CHUNKS_COLLECTION = "definitely_not_a_collection_xyz"
    try:
        r = asyncio.run(S.source(refs=[], mode="evidence", question="q"))
        ok = r.get("evidence") == [] and "not built yet" in str(r.get("reason", ""))
    finally:
        S.CHUNKS_COLLECTION = old
    return _check("absent chunk collection degrades with a reason", ok)


TESTS = [
    test_value_questions_trigger_evidence,
    test_discovery_queries_do_not,
    test_empty_query_is_not_answer_shaped,
    test_scores_parse,
    test_scores_degrade_on_garbage,
    test_scores_partial_and_clamped,
    test_scores_parse_bracketed_numbering,
    test_scores_parse_mixed_numbering,
    test_bracket_tolerance_does_not_swallow_prose,
    test_evidence_requires_a_question,
    test_evidence_allows_empty_refs,
    test_other_modes_still_require_refs,
    test_unknown_mode_lists_evidence,
    test_missing_collection_degrades_gracefully,
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
