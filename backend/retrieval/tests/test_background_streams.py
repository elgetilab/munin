"""
Integration tests for the background-turns HTTP surface:

- POST /api/chat/completions/{stream_id}/cancel  (explicit Stop)
- ``active_stream`` on GET /api/chats/{conversation_id}
- ``generating`` flag on GET /api/chats

Run inside the retrieval container (it imports `main`, which pulls the
full dependency tree; deps are present in the container image):

    docker exec munin-retrieval python /app/tests/test_background_streams.py

Or locally if the retrieval deps are installed:

    python backend/retrieval/tests/test_background_streams.py

Same approach as test_resume_endpoint.py: drive the real endpoints via
``httpx.ASGITransport(app=app)``, seed ``stream_registry.registry``
directly, never fire the startup hook (no embedders). Chat rows are
seeded through chat_store against a throwaway SQLite file selected via
CHATS_DB_PATH before import.
"""
from __future__ import annotations

import asyncio
import os
import shutil
import sys
import tempfile
import traceback
from pathlib import Path

sys.path.insert(0, "/app")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Must be set before chat_store is imported (it reads the env var at
# module import time).
_DB_DIR = tempfile.mkdtemp(prefix="munin-test-chats-")
os.environ["CHATS_DB_PATH"] = os.path.join(_DB_DIR, "chats.db")

import httpx  # noqa: E402

import chat_store  # noqa: E402
import stream_registry  # noqa: E402
from stream_registry import Stream  # noqa: E402
from main import app  # noqa: E402

OWNER = "owner@test.local"  # _require_user_email lowercases; keep these lower
INTRUDER = "intruder@test.local"


def _check(name: str, ok: bool, detail: str = "") -> bool:
    label = "PASS" if ok else "FAIL"
    print(f"[{label}] {name}{(' - ' + detail) if detail and not ok else ''}")
    return ok


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    )


def _seed_stream(*, owner: str = OWNER, conversation_id: str = "conv-test",
                 done: bool = False,
                 events: list[tuple[str, str]] | None = None) -> Stream:
    s = Stream(user_email=owner, conversation_id=conversation_id)
    for name, data in (events or []):
        s.record(name, data)
    if done:
        s.mark_done()
    stream_registry.registry.register(s)
    return s


def _evict(*streams: Stream) -> None:
    for s in streams:
        stream_registry.registry._streams.pop(s.stream_id, None)


# ---------------------------------------------------------------------------
# Cancel endpoint
# ---------------------------------------------------------------------------

def test_cancel_fires_cancel_event() -> bool:
    async def go():
        s = _seed_stream()
        try:
            async with _client() as client:
                resp = await client.post(
                    f"/api/chat/completions/{s.stream_id}/cancel",
                    headers={"X-Munin-Email": OWNER},
                )
            return (
                resp.status_code == 204
                and s.cancel_event.is_set()
                and s.cancel_reason == "stopped by user"
            )
        finally:
            _evict(s)

    return _check("cancel: owner POST fires cancel_event, 204", asyncio.run(go()))


def test_cancel_rejects_non_owner() -> bool:
    async def go():
        s = _seed_stream()
        try:
            async with _client() as client:
                resp = await client.post(
                    f"/api/chat/completions/{s.stream_id}/cancel",
                    headers={"X-Munin-Email": INTRUDER},
                )
            return resp.status_code == 403 and not s.cancel_event.is_set()
        finally:
            _evict(s)

    return _check("cancel: non-owner gets 403, event untouched", asyncio.run(go()))


def test_cancel_unknown_stream_410() -> bool:
    async def go():
        async with _client() as client:
            resp = await client.post(
                "/api/chat/completions/deadbeef/cancel",
                headers={"X-Munin-Email": OWNER},
            )
        return resp.status_code == 410

    return _check("cancel: unknown stream id gets 410", asyncio.run(go()))


def test_cancel_done_stream_is_noop_204() -> bool:
    async def go():
        s = _seed_stream(done=True)
        try:
            async with _client() as client:
                resp = await client.post(
                    f"/api/chat/completions/{s.stream_id}/cancel",
                    headers={"X-Munin-Email": OWNER},
                )
            return resp.status_code == 204 and not s.cancel_event.is_set()
        finally:
            _evict(s)

    return _check("cancel: completed stream is a 204 no-op", asyncio.run(go()))


# ---------------------------------------------------------------------------
# active_stream on GET /api/chats/{id}
# ---------------------------------------------------------------------------

def test_get_chat_reports_active_stream() -> bool:
    async def go():
        conv = await chat_store.create_conversation(
            user_email=OWNER, persona="librarian"
        )
        s = _seed_stream(
            conversation_id=conv["id"],
            events=[("token", '{"content":"partial"}')],
        )
        try:
            async with _client() as client:
                resp = await client.get(
                    f"/api/chats/{conv['id']}",
                    headers={"X-Munin-Email": OWNER},
                )
            body = resp.json()
            active = body.get("active_stream")
            return (
                resp.status_code == 200
                and active is not None
                and active["stream_id"] == s.stream_id
                and active["done"] is False
                and active["last_seq"] == 1
            )
        finally:
            _evict(s)

    return _check(
        "GET /api/chats/{id} carries active_stream for a live turn",
        asyncio.run(go()),
    )


def test_get_chat_active_stream_null_when_none() -> bool:
    async def go():
        conv = await chat_store.create_conversation(
            user_email=OWNER, persona="librarian"
        )
        async with _client() as client:
            resp = await client.get(
                f"/api/chats/{conv['id']}",
                headers={"X-Munin-Email": OWNER},
            )
        body = resp.json()
        return (
            resp.status_code == 200
            and "active_stream" in body
            and body["active_stream"] is None
        )

    return _check(
        "GET /api/chats/{id} has active_stream: null with no live turn",
        asyncio.run(go()),
    )


def test_get_chat_active_stream_done_within_retention() -> bool:
    """A just-completed stream still in the registry must be reported
    with done: true, so a reopening client knows to reload rather than
    re-attach."""
    async def go():
        conv = await chat_store.create_conversation(
            user_email=OWNER, persona="librarian"
        )
        s = _seed_stream(conversation_id=conv["id"], done=True)
        try:
            async with _client() as client:
                resp = await client.get(
                    f"/api/chats/{conv['id']}",
                    headers={"X-Munin-Email": OWNER},
                )
            active = resp.json().get("active_stream")
            return active is not None and active["done"] is True
        finally:
            _evict(s)

    return _check(
        "GET /api/chats/{id} reports done: true within retention",
        asyncio.run(go()),
    )


# ---------------------------------------------------------------------------
# generating flag on GET /api/chats
# ---------------------------------------------------------------------------

def test_list_chats_generating_flag() -> bool:
    async def go():
        busy = await chat_store.create_conversation(
            user_email=OWNER, persona="librarian"
        )
        idle = await chat_store.create_conversation(
            user_email=OWNER, persona="librarian"
        )
        s = _seed_stream(conversation_id=busy["id"])
        try:
            async with _client() as client:
                resp = await client.get(
                    "/api/chats", headers={"X-Munin-Email": OWNER}
                )
            convs = {c["id"]: c for c in resp.json()["conversations"]}
            return (
                resp.status_code == 200
                and convs[busy["id"]]["generating"] is True
                and convs[idle["id"]]["generating"] is False
            )
        finally:
            _evict(s)

    return _check(
        "GET /api/chats flags only the generating conversation",
        asyncio.run(go()),
    )


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

TESTS = [
    test_cancel_fires_cancel_event,
    test_cancel_rejects_non_owner,
    test_cancel_unknown_stream_410,
    test_cancel_done_stream_is_noop_204,
    test_get_chat_reports_active_stream,
    test_get_chat_active_stream_null_when_none,
    test_get_chat_active_stream_done_within_retention,
    test_list_chats_generating_flag,
]


def _cleanup() -> None:
    """Close the chat_store connection and drop the throwaway DB.

    Without the close this file PASSED every test and then hung forever in
    `threading._shutdown`. `chat_store.get_db()` caches one `aiosqlite`
    connection, aiosqlite services it from a NON-daemon worker thread, and
    CPython will not finalize while such a thread is alive. Nothing printed
    after "8 passed, 0 failed", so under a timeout a fully successful run
    was indistinguishable from a test wedged on a real defect.
    See tests/README.md, "Two ways a test lies about itself".
    """
    try:
        asyncio.run(chat_store.close_db())
    except Exception:
        traceback.print_exc()
    shutil.rmtree(_DB_DIR, ignore_errors=True)


def teardown_module(module=None) -> None:  # noqa: ARG001
    """pytest's hook; `main()` handles the standalone path. Safe twice."""
    _cleanup()


def main() -> int:
    passed = 0
    failed = 0
    try:
        for t in TESTS:
            try:
                ok = t()
            except Exception:
                ok = False
                print(f"[FAIL] {t.__name__} - exception:")
                traceback.print_exc()
            passed += int(ok)
            failed += int(not ok)
        print(f"\n{passed} passed, {failed} failed")
        return 0 if failed == 0 else 1
    finally:
        _cleanup()


if __name__ == "__main__":
    sys.exit(main())
