"""
Retry and Edit: `replace_from` on POST /api/chat/completions.

The server keeps the conversation and a turn sends only its new user message,
so retrying or editing a message has to drop that message and everything after
it before the turn runs. `chat_store.delete_messages_from` does that, and the
route checks the request before any stream starts.

Store half (no app import): which rows go, which stay, the summary, the search
index, and the ids that are refused. Route half: the 4xx answers, driven
through httpx.ASGITransport so no startup hook, model or stream runs; it needs
main's dependencies and SKIPs without them.

All data is synthetic, in a throwaway database built by the real schema code.

    python backend/retrieval/tests/test_replace_from.py
    docker exec munin-retrieval python /app/tests/test_replace_from.py
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

_TMPDIR = tempfile.mkdtemp(prefix="munin-test-replace-from-")
os.environ["CHATS_DB_PATH"] = os.path.join(_TMPDIR, "chats.db")

import chat_store  # noqa: E402

USER = "replace-user@example.invalid"
OTHER = "replace-other@example.invalid"


async def _fresh_db() -> None:
    chat_store._db = None
    chat_store.CHATS_DB_PATH = tempfile.mktemp(prefix="chats-", suffix=".db", dir=_TMPDIR)
    await chat_store.init_db()


async def _seed() -> tuple[str, list[dict]]:
    """A conversation of three turns: u0 a0 u1 a1 u2 a2."""
    conv = await chat_store.create_conversation(user_email=USER, persona="chat", title="t")
    rows = []
    for i in range(3):
        rows.append(await chat_store.add_message(conv["id"], "user", f"question {i} zebra{i}"))
        rows.append(await chat_store.add_message(conv["id"], "assistant", f"answer {i}"))
    return conv["id"], rows


def _run(body):
    async def go():
        await _fresh_db()
        try:
            return await body()
        finally:
            await chat_store.close_db()
    return asyncio.run(go())


async def _contents(conv_id: str) -> list[str]:
    conv = await chat_store.get_conversation(conv_id, USER)
    return [m["content"] for m in conv["messages"]]


# ---------------------------------------------------------------- store ---

def test_drops_the_message_and_everything_after() -> None:
    async def body():
        cid, rows = await _seed()
        n = await chat_store.delete_messages_from(cid, rows[2]["id"])  # u1
        assert n == 4, n
        assert await _contents(cid) == ["question 0 zebra0", "answer 0"]
    _run(body)


def test_the_next_message_takes_the_freed_place() -> None:
    async def body():
        cid, rows = await _seed()
        await chat_store.delete_messages_from(cid, rows[4]["id"])  # u2
        new = await chat_store.add_message(cid, "user", "question 2, edited")
        assert new["index_in_conversation"] == 4, new["index_in_conversation"]
        assert (await _contents(cid))[-1] == "question 2, edited"
    _run(body)


def test_retrying_the_first_message_empties_the_conversation() -> None:
    async def body():
        cid, rows = await _seed()
        assert await chat_store.delete_messages_from(cid, rows[0]["id"]) == 6
        assert await _contents(cid) == []
    _run(body)


def test_refuses_a_reply_another_conversation_or_an_unknown_id() -> None:
    async def body():
        cid, rows = await _seed()
        other = await chat_store.create_conversation(user_email=USER, persona="chat", title="o")
        assert await chat_store.delete_messages_from(cid, rows[1]["id"]) is None  # a reply
        assert await chat_store.delete_messages_from(other["id"], rows[2]["id"]) is None
        assert await chat_store.delete_messages_from(cid, "no-such-id") is None
        assert len(await _contents(cid)) == 6  # nothing went
    _run(body)


def test_clears_a_summary_that_covers_a_dropped_message() -> None:
    async def body():
        cid, rows = await _seed()
        await chat_store.update_summary(cid, "summary of turns 0-1", through_index=3)
        await chat_store.delete_messages_from(cid, rows[2]["id"])  # index 2 <= 3
        conv = await chat_store.get_conversation(cid, USER)
        assert conv["summary"] is None and conv["summary_through_index"] is None, conv["summary"]
    _run(body)


def test_keeps_a_summary_of_turns_that_stay() -> None:
    async def body():
        cid, rows = await _seed()
        await chat_store.update_summary(cid, "summary of turn 0", through_index=1)
        await chat_store.delete_messages_from(cid, rows[4]["id"])  # index 4 > 1
        conv = await chat_store.get_conversation(cid, USER)
        assert conv["summary"] == "summary of turn 0" and conv["summary_through_index"] == 1
    _run(body)


def test_dropped_messages_leave_the_search_index() -> None:
    async def body():
        cid, rows = await _seed()
        before = await chat_store.search_user_messages(USER, "zebra2", limit=10)
        assert before["total_matches"] == 1, before
        await chat_store.delete_messages_from(cid, rows[4]["id"])
        after = await chat_store.search_user_messages(USER, "zebra2", limit=10)
        assert after["total_matches"] == 0, after
        kept = await chat_store.search_user_messages(USER, "zebra0", limit=10)
        assert kept["total_matches"] == 1, kept
    _run(body)


# ---------------------------------------------------------------- route ---

def _post(app, body: dict, email: str):
    import httpx
    async def go():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
            return await c.post("/api/chat/completions", json=body, headers={"X-Munin-Email": email})
    return go()


def test_route_refuses_bad_requests_before_any_stream() -> None:
    try:
        import httpx  # noqa: F401
        import main
    except Exception as exc:  # host without the app's dependencies
        print(f"[SKIP] route checks: cannot import main ({type(exc).__name__}: {exc})")
        return

    async def body():
        cid, rows = await _seed()
        other = await chat_store.create_conversation(user_email=OTHER, persona="chat", title="o")
        msg = [{"role": "user", "content": "again"}]

        def req(**extra):
            return {"persona": "chat", "messages": msg, **extra}

        cases = [
            ("not a string", req(conversation_id=cid, replace_from=5), USER, 400),
            ("empty", req(conversation_id=cid, replace_from=""), USER, 400),
            ("no conversation", req(replace_from=rows[2]["id"]), USER, 400),
            ("ephemeral", req(conversation_id=cid, replace_from=rows[2]["id"], ephemeral=True), USER, 400),
            ("someone else's chat", req(conversation_id=other["id"], replace_from=rows[2]["id"]), USER, 404),
            ("another user", req(conversation_id=cid, replace_from=rows[2]["id"]), OTHER, 404),
            ("a reply's id", req(conversation_id=cid, replace_from=rows[3]["id"]), USER, 404),
            ("unknown id", req(conversation_id=cid, replace_from="nope"), USER, 404),
        ]
        failures = []
        for name, b, email, want in cases:
            r = await _post(main.app, b, email)
            if r.status_code != want:
                failures.append(f"{name}: {r.status_code} {r.text[:120]}")
        assert not failures, "; ".join(failures)
        # None of them touched the conversation.
        assert len(await _contents(cid)) == 6
    _run(body)


TESTS = [
    test_drops_the_message_and_everything_after,
    test_the_next_message_takes_the_freed_place,
    test_retrying_the_first_message_empties_the_conversation,
    test_refuses_a_reply_another_conversation_or_an_unknown_id,
    test_clears_a_summary_that_covers_a_dropped_message,
    test_keeps_a_summary_of_turns_that_stay,
    test_dropped_messages_leave_the_search_index,
    test_route_refuses_bad_requests_before_any_stream,
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
