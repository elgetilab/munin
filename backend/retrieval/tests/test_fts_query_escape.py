"""
Regression test: free text reaching SQLite FTS5 MATCH in chat_store.

Before the fix, ``search_user_messages`` (the search_past_conversations
tool) and ``get_conversations(search=...)`` (the sidebar search) passed
the raw string to ``fts.content MATCH ?``. FTS5 parses that string as a
query expression, so a hyphenated term such as ``abc-3`` or ``foo-TPA``
was read as ``abc NOT column:3`` and failed with
``no such column: 3`` / ``no such column: TPA``. Quotes, colons, stray
asterisks and parentheses raised other syntax errors.

Fix: ``chat_store.fts5_query`` turns free text into a safe FTS5 query
(each token quoted as a phrase, embedded quotes doubled, empty tokens
dropped, implicit AND kept, bare ``OR`` and trailing ``*`` prefix kept),
and both call sites use it. Input that sanitises to nothing returns no
results instead of an error.

All inputs below are synthetic. Uses a temp DB built by the real
``init_db`` schema code.

Run:
    python backend/retrieval/tests/test_fts_query_escape.py
    docker run --rm --network none -e CHATS_DB_PATH=/tmp/chats.db \\
        -v "$PWD/backend/retrieval":/app munin-retrieval:latest \\
        python /app/tests/test_fts_query_escape.py
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import traceback
from pathlib import Path

sys.path.insert(0, "/app")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

_TMPDIR = tempfile.mkdtemp(prefix="munin-test-fts-")
os.environ["CHATS_DB_PATH"] = os.path.join(_TMPDIR, "chats.db")

import chat_store  # noqa: E402

USER = "fts-user@example.invalid"
OTHER = "fts-other@example.invalid"

# Synthetic message bodies; one conversation each.
DOCS = {
    "hyphen": "We compared the widget-3 prototype against the baseline.",
    "acronym": "The foo-TPA ligand binds in the second pocket.",
    "german": "Die Größe der Übergangsmetall Probe wurde gemessen.",
    "quote": 'He wrote "alpha beta" on the board, then gamma.',
    "colon": "Ratio 3:1 mixture of solvent and water.",
    "near": "Optimizer warmup schedule with cosine decay.",
    "other_word": "Completely unrelated text about zebras.",
}


async def _seed() -> dict:
    ids = {}
    for key, text in DOCS.items():
        conv = await chat_store.create_conversation(
            user_email=USER, persona="chat", title=f"t-{key}",
        )
        await chat_store.add_message(conv["id"], "user", text)
        ids[key] = conv["id"]
    # Another user's identical text must never leak into USER's search.
    conv = await chat_store.create_conversation(
        user_email=OTHER, persona="chat", title="other",
    )
    await chat_store.add_message(conv["id"], "user", DOCS["hyphen"])
    return ids


def _run(body):
    """Fresh DB per test: run init + seed + body in one event loop."""
    async def go():
        chat_store._db = None
        chat_store.CHATS_DB_PATH = tempfile.mktemp(
            prefix="chats-", suffix=".db", dir=_TMPDIR,
        )
        try:
            await chat_store.init_db()
            ids = await _seed()
            return await body(ids)
        finally:
            await chat_store.close_db()
    return asyncio.run(go())


async def _tool_hits(query: str) -> tuple[set, int]:
    res = await chat_store.search_user_messages(USER, query, limit=20)
    return {r["conversation_id"] for r in res["results"]}, res["total_matches"]


async def _sidebar_hits(query: str) -> tuple[set, int]:
    res = await chat_store.get_conversations(USER, limit=50, search=query)
    return {c["id"] for c in res["conversations"]}, res["total"]


# (query, expected conversation keys). Each runs through both call sites.
CASES = [
    ("widget-3", {"hyphen"}),
    ("foo-TPA", {"acronym"}),
    ("3", {"hyphen", "colon"}),
    ("TPA", {"acronym"}),
    ('"alpha beta"', {"quote"}),
    ('alpha" beta', {"quote"}),
    ("3:1", {"colon"}),
    ("content:ligand", set()),
    ("warmup NEAR cosine", {"near"}),
    ("warmup AND cosine", {"near"}),
    ("warmup OR zebras", {"near", "other_word"}),
    ("warm*", {"near"}),
    ("*", set()),
    ("Größe", {"german"}),
    ("übergangsmetall", {"german"}),
    ("(widget", {"hyphen"}),
    ("NOT", set()),
    ("   ", set()),
    ('""', set()),
    ("- : * ( )", set()),
]


def test_tool_search_handles_fts_syntax() -> None:
    async def body(ids):
        failures = []
        for q, keys in CASES:
            want = {ids[k] for k in keys}
            try:
                got, total = await _tool_hits(q)
            except Exception as exc:  # the bug: sqlite OperationalError
                failures.append(f"{q!r}: raised {type(exc).__name__}: {exc}")
                continue
            if got != want or total != len(want):
                failures.append(f"{q!r}: got {len(got)}/{total}, want {len(want)}")
        return failures
    failures = _run(body)
    assert not failures, "; ".join(failures)


def test_sidebar_search_handles_fts_syntax() -> None:
    async def body(ids):
        failures = []
        for q, keys in CASES:
            want = {ids[k] for k in keys}
            if not q:
                continue
            try:
                got, total = await _sidebar_hits(q)
            except Exception as exc:
                failures.append(f"{q!r}: raised {type(exc).__name__}: {exc}")
                continue
            if got != want or total != len(want):
                failures.append(f"{q!r}: got {len(got)}/{total}, want {len(want)}")
        return failures
    failures = _run(body)
    assert not failures, "; ".join(failures)


def test_sidebar_empty_search_lists_all() -> None:
    # search=None / "" keeps meaning "no filter", not "no results".
    async def body(ids):
        res = await chat_store.get_conversations(USER, limit=50, search="")
        return res["total"]
    assert _run(body) == len(DOCS)


def test_fts5_query_shape() -> None:
    q = chat_store.fts5_query
    assert q("widget-3") == '"widget-3"'
    assert q('a"b') == '"a""b"'
    assert q("alpha beta") == '"alpha" "beta"'
    assert q('"alpha beta" gamma') == '"alpha beta" "gamma"'
    assert q("a OR b") == '"a" OR "b"'
    assert q("OR a OR") == '"a"'
    assert q("a AND b") == '"a" "b"'
    assert q("warm*") == '"warm"*'
    assert q("* : -") == ""
    assert q("") == ""
    assert q(None) == ""


TESTS = [
    test_fts5_query_shape,
    test_tool_search_handles_fts_syntax,
    test_sidebar_search_handles_fts_syntax,
    test_sidebar_empty_search_lists_all,
]


def main() -> int:
    passed = failed = 0
    for t in TESTS:
        try:
            t()
            print(f"[PASS] {t.__name__}")
            passed += 1
        except Exception as exc:
            print(f"[FAIL] {t.__name__} - {type(exc).__name__}: {exc}")
            if not isinstance(exc, AssertionError):
                traceback.print_exc()
            failed += 1
    print(f"\n{passed} passed, {failed} failed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
