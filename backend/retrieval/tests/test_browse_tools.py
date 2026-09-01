"""
Unit tests for the two BROWSE tools: list_documents and browse_tag_papers.

WHY they exist. In a 2026-08-20 chat a user asked, four different ways, for a
list of what he had uploaded and what was in the research-group corpus he had
attached. The model had only semantic search, so it guessed topics, ran four
queries, got zero hits each time and concluded his store was "probably empty" -
while `GET /api/documents` and `GET /api/tags/{kind}/{slug}/papers` were serving
exactly those inventories to the web UI beside it. The capability existed; only
the model could not reach it.

The load-bearing property in both tools is HONESTY ABOUT COVERAGE, not the
listing itself. A browse that returns page one and lets the model imply it saw
the whole collection reproduces the original failure in a new costume, so the
`total` and the truncation note are asserted here as hard requirements. The
second property is that an empty result is self-describing: for a SEARCH, zero
hits are ambiguous (bad query, or empty store); for an ENUMERATION they are not,
and the tool has to say so or the model will hedge exactly as it did then.

No network: Qdrant and the document store are stubbed.

    docker exec munin-retrieval python /app/tests/test_browse_tools.py
"""

from __future__ import annotations

import asyncio
import importlib
import sys
import traceback

sys.path.insert(0, "/app")
sys.path.insert(0, ".")

D = importlib.import_module("mcp.tools.documents")
K = importlib.import_module("mcp.tools.knowledge")
TB = importlib.import_module("tag_browse")
from mcp.context import current_user_email, current_query_tags  # noqa: E402


def _check(name, ok, detail=""):
    print(("[PASS] " if ok else "[FAIL] ") + name + (f"  {detail}" if detail else ""))
    return ok


# --- list_documents ---------------------------------------------------------

class _FakeDocStore:
    def __init__(self, docs):
        self.docs = docs
        self.calls = []

    async def list_documents(self, user_email, conversation_id=None):
        self.calls.append(user_email)
        return self.docs


def _run_list(docs, email="user@example.org", **kw):
    """Run list_documents against a stubbed document_store.

    The tool imports `document_store` lazily inside the function body, so the
    stub goes into sys.modules rather than onto an attribute.
    """
    real = sys.modules.get("document_store")
    fake = _FakeDocStore(docs)
    sys.modules["document_store"] = fake
    tok = current_user_email.set(email) if email else None
    try:
        return asyncio.run(D.list_documents(**kw)), fake
    finally:
        if tok is not None:
            current_user_email.reset(tok)
        if real is not None:
            sys.modules["document_store"] = real
        else:
            del sys.modules["document_store"]


def _doc(i, status="embedded"):
    return {"document_id": f"d{i}", "filename": f"paper-{i}.pdf", "chunks": 12,
            "status": status, "upload_time": "2026-08-01T10:00:00Z"}


def test_list_requires_auth():
    res, _ = _run_list([_doc(1)], email=None)
    return _check("list_documents refuses without an authenticated user",
                  bool(res.get("error")) and res.get("documents") == [])


def test_list_filters_to_the_caller():
    _, fake = _run_list([_doc(1)], email="someone@example.org")
    return _check("list_documents scopes to the context user",
                  fake.calls == ["someone@example.org"], f"{fake.calls}")


def test_empty_store_says_it_is_empty_not_unmatched():
    """The whole point of the tool. A search returning nothing is ambiguous; an
    enumeration returning nothing is not, and the result must say which it is."""
    res, _ = _run_list([])
    note = (res.get("note") or "").lower()
    return _check("empty inventory distinguishes itself from a missed query",
                  res["total"] == 0 and "empty" in note and "not a search" in note,
                  f"note={res.get('note')!r}")


def test_truncation_is_declared():
    res, _ = _run_list([_doc(i) for i in range(120)], limit=10)
    return _check("a truncated page reports the true total and says so",
                  res["total"] == 120 and res["returned"] == 10
                  and "120" in (res.get("note") or ""),
                  f"total={res['total']} returned={res['returned']} note={res.get('note')!r}")


def test_limit_is_clamped_and_coerced():
    big, _ = _run_list([_doc(i) for i in range(400)], limit=9999)
    junk, _ = _run_list([_doc(i) for i in range(400)], limit="not a number")
    return _check("limit is clamped to the max and survives junk",
                  big["returned"] == 200 and junk["returned"] == 50,
                  f"big={big['returned']} junk={junk['returned']}")


def test_unsearchable_documents_are_flagged():
    """`status='stored'` means the file exists with no embeddings, so
    search_user_docs cannot see it. Normal for an image, a failed extraction for
    anything else. Flattening the two into "you have 3 documents" is how a user
    ends up told a file is there when nothing can read it."""
    res, _ = _run_list([_doc(1), _doc(2, status="stored"), _doc(3, status="stored")])
    return _check("documents that exist but are not embedded are called out",
                  "2 of these are stored but not embedded" in (res.get("note_unsearchable") or ""),
                  f"{res.get('note_unsearchable')!r}")


# --- browse_tag_papers ------------------------------------------------------

def _stub_browse(result=None, raises=None):
    """Patch tag_browse.browse_tag_papers; returns (restore, calls)."""
    calls = []
    orig = TB.browse_tag_papers

    def fake(kind, slug, offset=0, limit=50, sort="year_desc"):
        calls.append({"kind": kind, "slug": slug, "offset": offset,
                      "limit": limit, "sort": sort})
        if raises is not None:
            raise raises
        return dict(result or {"kind": kind, "slug": slug, "total": 0,
                               "offset": offset, "limit": limit, "sort": sort,
                               "papers": []})

    TB.browse_tag_papers = fake

    def restore():
        TB.browse_tag_papers = orig

    return restore, calls


def _run_browse(tags=None, **kw):
    tok = current_query_tags.set(tags) if tags is not None else None
    try:
        return asyncio.run(K.browse_tag_papers(**kw))
    finally:
        if tok is not None:
            current_query_tags.reset(tok)


def _page(n_papers, total, offset=0):
    return {"kind": "group", "slug": "deibel", "total": total, "offset": offset,
            "limit": n_papers, "sort": "year_desc",
            "papers": [{"title": f"p{i}", "doi": f"10.x/{i}"} for i in range(n_papers)]}


def test_browse_passes_explicit_args_through():
    restore, calls = _stub_browse(_page(5, 1572))
    try:
        _run_browse(kind="group", slug="deibel", offset=20, limit=5, sort="year_asc")
    finally:
        restore()
    return _check("explicit kind/slug/paging reach the browse layer",
                  calls == [{"kind": "group", "slug": "deibel", "offset": 20,
                             "limit": 5, "sort": "year_asc"}], f"{calls}")


def test_browse_defaults_to_the_single_active_tag():
    """A user who attached #deibel and says "list the first 10" has already named
    the collection. Making the model restate the slug is how it guesses one."""
    restore, calls = _stub_browse(_page(3, 1572))
    try:
        res = _run_browse(tags=[{"kind": "group", "value": "Deibel"}], limit=3)
    finally:
        restore()
    return _check("a single scope tag supplies kind and slug",
                  calls and calls[0]["kind"] == "group" and calls[0]["slug"] == "deibel"
                  and res.get("scoped_from_active_tags") is True,
                  f"{calls}")


def test_browse_refuses_to_guess_between_two_tags():
    """Tags AND-combine in search, but a browse takes ONE collection. Picking
    silently would show a different set from the user's scope without saying so."""
    restore, calls = _stub_browse(_page(3, 10))
    try:
        res = _run_browse(tags=[{"kind": "group", "value": "deibel"},
                                {"kind": "topic", "value": "perovskites"}])
    finally:
        restore()
    return _check("two attached tags produce an error, not a guess",
                  bool(res.get("error")) and not calls
                  and "ambiguous" in (res.get("hint") or "").lower(),
                  f"calls={calls} err={(res.get('error') or '')[:40]!r}")


def test_browse_with_no_tags_and_no_args_asks():
    restore, calls = _stub_browse()
    try:
        res = _run_browse(tags=[])
    finally:
        restore()
    return _check("no tags and no arguments asks rather than browsing something",
                  bool(res.get("error")) and not calls)


def test_browse_declares_partial_coverage():
    """The failure this tool exists to prevent, in a new costume: page one
    presented as the whole corpus."""
    restore, _ = _stub_browse(_page(10, 1572))
    try:
        res = _run_browse(kind="group", slug="deibel", limit=10)
    finally:
        restore()
    note = res.get("note") or ""
    return _check("a partial page reports the collection's real size",
                  "10 of 1572" in note, f"note={note!r}")


def test_browse_full_collection_has_no_truncation_note():
    restore, _ = _stub_browse(_page(4, 4))
    try:
        res = _run_browse(kind="group", slug="deibel", limit=10)
    finally:
        restore()
    return _check("a complete listing is not hedged", not res.get("note"),
                  f"note={res.get('note')!r}")


def test_browse_empty_collection_says_empty():
    restore, _ = _stub_browse(_page(0, 0))
    try:
        res = _run_browse(kind="group", slug="ghosts", limit=10)
    finally:
        restore()
    note = (res.get("note") or "").lower()
    return _check("an empty collection is distinguished from a missed query",
                  "not a search" in note and "empty" in note, f"note={note!r}")


def test_browse_surfaces_bad_input_as_an_error():
    restore, _ = _stub_browse(raises=TB.TagBrowseError("unknown tag kind: 'nope'"))
    try:
        res = _run_browse(kind="nope", slug="x")
    finally:
        restore()
    return _check("a bad kind returns an error rather than raising",
                  "unknown tag kind" in (res.get("error") or ""), f"{res}")


def test_browse_never_raises_into_the_turn():
    restore, _ = _stub_browse(raises=RuntimeError("qdrant exploded"))
    try:
        res = _run_browse(kind="group", slug="deibel")
    finally:
        restore()
    return _check("an unexpected failure degrades to an error dict",
                  "qdrant exploded" in (res.get("error") or ""), f"{res}")


# --- registration -----------------------------------------------------------

def test_both_tools_are_fully_registered():
    """Implementation + schema + dispatcher, the three places a tool must exist.
    `verify_dispatch_registry` enforces this at startup; asserting it here means
    a half-registered tool fails in tests rather than at boot."""
    from mcp.schemas import MCP_TOOLS
    import mcp.dispatchers  # noqa: F401  (registers the shims)
    from mcp._dispatch import verify_dispatch_registry
    from mcp import tools as tools_pkg

    names = ("list_documents", "browse_tag_papers")
    in_schema = all(n in MCP_TOOLS for n in names)
    in_pkg = all(hasattr(tools_pkg, n) for n in names)
    try:
        verify_dispatch_registry()
        dispatch_ok = True
    except Exception as e:
        dispatch_ok = False
        print(f"       verify_dispatch_registry raised: {e}")
    return _check("both tools are registered in schema, package and dispatcher",
                  in_schema and in_pkg and dispatch_ok,
                  f"schema={in_schema} pkg={in_pkg} dispatch={dispatch_ok}")


def test_browse_tools_are_discoverable_by_the_words_a_model_tries():
    """The transcript is the test case: the model searched for "browse",
    "list_all", "enumerate" and "catalog" and found nothing. tool_search drops
    "list" as a stopword, so the descriptions have to carry the rest."""
    from mcp.tools.tool_search import tool_search
    wanted = {"enumerate my uploaded documents": "list_documents",
              "browse all papers in a research group": "browse_tag_papers",
              "catalog the contents of a collection": "browse_tag_papers",
              "inventory of everything I uploaded": "list_documents"}
    misses = []
    for query, expected in wanted.items():
        res = asyncio.run(tool_search(query=query))
        names = [m["name"] for m in res.get("matches", [])]
        if expected not in names:
            misses.append((query, names[:3]))
    return _check("tool_search surfaces the browse tools for natural phrasings",
                  not misses, f"missed {misses}")


TESTS = [
    test_list_requires_auth,
    test_list_filters_to_the_caller,
    test_empty_store_says_it_is_empty_not_unmatched,
    test_truncation_is_declared,
    test_limit_is_clamped_and_coerced,
    test_unsearchable_documents_are_flagged,
    test_browse_passes_explicit_args_through,
    test_browse_defaults_to_the_single_active_tag,
    test_browse_refuses_to_guess_between_two_tags,
    test_browse_with_no_tags_and_no_args_asks,
    test_browse_declares_partial_coverage,
    test_browse_full_collection_has_no_truncation_note,
    test_browse_empty_collection_says_empty,
    test_browse_surfaces_bad_input_as_an_error,
    test_browse_never_raises_into_the_turn,
    test_both_tools_are_fully_registered,
    test_browse_tools_are_discoverable_by_the_words_a_model_tries,
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
