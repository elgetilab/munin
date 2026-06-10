"""
Integration tests for the SSE resume endpoint
(GET /api/chat/completions/resume, P1 #10).

Run inside the retrieval container (it imports `main`, which pulls the
full dependency tree; deps are present in the container image):

    docker exec munin-retrieval python /app/tests/test_resume_endpoint.py

Or locally if the retrieval deps are installed:

    python backend/retrieval/tests/test_resume_endpoint.py

WHY THIS EXISTS
---------------
`test_stream_registry.py` unit-tests the in-process Stream/registry
primitives but deliberately does "no HTTP, no FastAPI". The production
gateway logs showed `/api/chat/completions/resume` failing 100% of the
time (410s + 500s) with NO test exercising the actual HTTP endpoint.
See `backend/docs/KNOWN-BUGS.md` entry #2.

APPROACH
--------
Drive the real endpoint via `httpx.ASGITransport(app=app)` but bypass
the heavy POST path: each test seeds `stream_registry.registry`
directly (exactly how the unit tests construct a Stream), then hits the
resume endpoint and asserts HTTP status + the streamed SSE body. The
app's `@app.on_event("startup")` hook (which eager-loads embedders) is
never fired because ASGITransport does not run lifespan events, so the
harness stays light. Auth is the `X-Munin-Email` forward-auth header.
"""
from __future__ import annotations

import asyncio
import sys
import traceback
from pathlib import Path

sys.path.insert(0, "/app")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx  # noqa: E402

import stream_registry  # noqa: E402
from stream_registry import MAX_LOG_EVENTS, Stream  # noqa: E402
from main import app  # noqa: E402

OWNER = "owner@test.local"  # _require_user_email lowercases; keep these lower


def _check(name: str, ok: bool, detail: str = "") -> bool:
    label = "PASS" if ok else "FAIL"
    print(f"[{label}] {name}{(' - ' + detail) if detail and not ok else ''}")
    return ok


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    )


def _seed_stream(events: list[tuple[str, str]], *, owner: str = OWNER,
                 done: bool = False) -> Stream:
    """Build, register, and pre-fill a Stream the way main.py would."""
    s = Stream(user_email=owner, conversation_id="conv-test")
    for name, data in events:
        s.record(name, data)
    if done:
        s.mark_done()
    stream_registry.registry.register(s)
    return s


async def _read_sse(client: httpx.AsyncClient, stream_id: str, *,
                    last_event_id: str | None = None, owner: str = OWNER,
                    timeout: float = 5.0,
                    on_open=None) -> tuple[int, list[dict]]:
    """Open the resume endpoint and collect SSE events until the server
    closes the stream. Returns (status_code, [{id,event,data}, ...]).

    `on_open` is an optional coroutine factory that feeds live events
    into an in-flight stream so the generator eventually completes. It is
    scheduled BEFORE the request is issued: httpx's ASGITransport buffers
    the response body to completion during the request (no incremental
    streaming), so a task that drives the stream to `done` must already
    be runnable when the app blocks on `wait_for_new` — otherwise the
    request deadlocks. A genuinely hung stream surfaces as
    asyncio.TimeoutError -> test failure, not an infinite hang.
    """
    headers = {"X-Munin-Email": owner}
    if last_event_id is not None:
        headers["Last-Event-ID"] = last_event_id
    events: list[dict] = []
    status = {"code": None}

    async def _loop() -> None:
        # Schedule the feeder before the (buffering) request — see docstring.
        bg = asyncio.create_task(on_open()) if on_open else None
        try:
            async with client.stream(
                "GET", "/api/chat/completions/resume",
                params={"stream_id": stream_id}, headers=headers,
            ) as resp:
                status["code"] = resp.status_code
                if resp.status_code != 200:
                    await resp.aread()
                    return
                cur: dict = {}
                async for line in resp.aiter_lines():
                    if line.startswith(":"):   # sse-starlette keepalive comment
                        continue
                    if line == "":             # event boundary
                        if cur:
                            events.append(cur)
                            cur = {}
                        continue
                    if line.startswith("id:"):
                        cur["id"] = line[3:].strip()
                    elif line.startswith("event:"):
                        cur["event"] = line[6:].strip()
                    elif line.startswith("data:"):
                        cur["data"] = line[5:].strip()
                if cur:
                    events.append(cur)
        finally:
            if bg:
                await bg

    await asyncio.wait_for(_loop(), timeout)
    return status["code"], events


# ---------------------------------------------------------------------------
# Success paths — the behaviour nothing currently proves works
# ---------------------------------------------------------------------------

def test_resume_completed_stream_replays_after_seq() -> bool:
    """Scenario 2: a completed stream, resumed with Last-Event-ID, replays
    strictly-newer events and then closes cleanly (200)."""
    async def go() -> bool:
        s = _seed_stream(
            [("token", "a"), ("token", "b"), ("token", "c")], done=True
        )
        async with _client() as client:
            status, evs = await _read_sse(
                client, s.stream_id, last_event_id=f"{s.stream_id}-1"
            )
        ids = [e.get("id") for e in evs]
        datas = [e.get("data") for e in evs]
        ok = (
            status == 200
            and ids == [f"{s.stream_id}-2", f"{s.stream_id}-3"]
            and datas == ["b", "c"]
        )
        return _check(
            "resume completed stream replays events after Last-Event-ID",
            ok, f"status={status} ids={ids} datas={datas}",
        )
    return asyncio.run(go())


def test_resume_without_last_event_id_replays_from_zero() -> bool:
    """Scenario 2b: browser refresh that lost its Last-Event-ID -> after_seq
    defaults to 0, so the whole completed buffer is replayed."""
    async def go() -> bool:
        s = _seed_stream([("token", "a"), ("done", "{}")], done=True)
        async with _client() as client:
            status, evs = await _read_sse(client, s.stream_id)
        ids = [e.get("id") for e in evs]
        ok = status == 200 and ids == [f"{s.stream_id}-1", f"{s.stream_id}-2"]
        return _check(
            "resume without Last-Event-ID replays from seq 0",
            ok, f"status={status} ids={ids}",
        )
    return asyncio.run(go())


def test_resume_in_flight_replays_then_streams_live() -> bool:
    """Scenario 1: the load-bearing case. A still-running stream, resumed
    mid-flight, must replay the buffered tail past Last-Event-ID AND then
    deliver live events as they land, closing on mark_done."""
    async def go() -> bool:
        s = _seed_stream([("token", "a"), ("token", "b"), ("token", "c")])

        async def feed() -> None:
            await asyncio.sleep(0.05)
            s.record("token", "d")     # seq 4
            s.record("token", "e")     # seq 5
            s.mark_done()

        async with _client() as client:
            status, evs = await _read_sse(
                client, s.stream_id, last_event_id=f"{s.stream_id}-1",
                on_open=feed,
            )
        ids = [e.get("id") for e in evs]
        datas = [e.get("data") for e in evs]
        ok = (
            status == 200
            and ids == [f"{s.stream_id}-{n}" for n in (2, 3, 4, 5)]
            and datas == ["b", "c", "d", "e"]
        )
        return _check(
            "resume in-flight replays buffered tail then streams live",
            ok, f"status={status} ids={ids} datas={datas}",
        )
    return asyncio.run(go())


# ---------------------------------------------------------------------------
# Gone / forbidden branches — the production 410s + the ownership guard
# ---------------------------------------------------------------------------

def test_resume_unknown_stream_returns_410() -> bool:
    """Scenario 3: unknown/evicted stream_id (the janitor-eviction 410 that
    dominates production once DONE_RETENTION_S elapses)."""
    async def go() -> bool:
        async with _client() as client:
            resp = await client.get(
                "/api/chat/completions/resume",
                params={"stream_id": "deadbeefdeadbeefdeadbeefdeadbeef"},
                headers={"X-Munin-Email": OWNER},
            )
        body = resp.json()
        # FastAPI nests HTTPException detail under "detail":
        #   {"detail": {"error": {"message": ...}}}
        envelope = body.get("detail", body)
        ok = resp.status_code == 410 and "error" in envelope
        return _check(
            "resume unknown stream_id returns 410 + error envelope",
            ok, f"status={resp.status_code} body={body}",
        )
    return asyncio.run(go())


def test_resume_truncated_stream_returns_410() -> bool:
    """Scenario 4: buffer overflowed past MAX_LOG_EVENTS -> truncated -> 410
    (the long tool-heavy turn mode)."""
    async def go() -> bool:
        s = _seed_stream([])
        for i in range(MAX_LOG_EVENTS + 5):   # force truncation
            s.record("token", str(i))
        stream_registry.registry.register(s)
        ok_flag = s.truncated
        async with _client() as client:
            resp = await client.get(
                "/api/chat/completions/resume",
                params={"stream_id": s.stream_id},
                headers={"X-Munin-Email": OWNER},
            )
        ok = ok_flag and resp.status_code == 410
        return _check(
            "resume truncated stream returns 410",
            ok, f"truncated={ok_flag} status={resp.status_code}",
        )
    return asyncio.run(go())


def test_resume_other_users_stream_returns_403() -> bool:
    """Scenario 5: user B must never resume user A's stream."""
    async def go() -> bool:
        s = _seed_stream([("token", "a")], owner="alice@test.local")
        async with _client() as client:
            resp = await client.get(
                "/api/chat/completions/resume",
                params={"stream_id": s.stream_id},
                headers={"X-Munin-Email": "intruder@test.local"},
            )
        ok = resp.status_code == 403
        return _check(
            "resume another user's stream returns 403",
            ok, f"status={resp.status_code}",
        )
    return asyncio.run(go())


def test_resume_missing_auth_returns_401() -> bool:
    """No forward-auth header -> 401 before any registry lookup."""
    async def go() -> bool:
        s = _seed_stream([("token", "a")], done=True)
        async with _client() as client:
            resp = await client.get(
                "/api/chat/completions/resume",
                params={"stream_id": s.stream_id},
            )
        ok = resp.status_code == 401
        return _check(
            "resume without X-Munin-Email returns 401",
            ok, f"status={resp.status_code}",
        )
    return asyncio.run(go())


# ---------------------------------------------------------------------------
# Scenario 6 — production 500 regression. ROOT-CAUSED 2026-06-10.
# ---------------------------------------------------------------------------
# The production 500s on /api/chat/completions/resume (gateway log
# 2026-06-08T13:00/13:01, 2026-06-03T10:17) were a NameError: the
# endpoint returned `EventSourceResponse(...)` but that symbol was only
# imported LOCALLY inside the POST handler (api_chat_completions), not at
# module level, so the separate resume handler crashed on every success
# path. Fixed by hoisting the import to module scope in main.py.
#
# No dedicated regression test is needed: the success-path tests above
# (completed / no-Last-Event-ID / in-flight) only return 200 if the
# endpoint constructs its EventSourceResponse, so they fail loudly if the
# import is ever dropped again.


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

TESTS = [
    test_resume_completed_stream_replays_after_seq,
    test_resume_without_last_event_id_replays_from_zero,
    test_resume_in_flight_replays_then_streams_live,
    test_resume_unknown_stream_returns_410,
    test_resume_truncated_stream_returns_410,
    test_resume_other_users_stream_returns_403,
    test_resume_missing_auth_returns_401,
]


def main() -> int:
    passed = 0
    failed = 0
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


if __name__ == "__main__":
    sys.exit(main())
