"""
Standalone tests for the SSE stream registry (stream_registry.py, P1 #10).

Run inside the retrieval container:
    docker exec munin-retrieval python /app/tests/test_stream_registry.py
Or locally:
    python backend/retrieval/tests/test_stream_registry.py

Drives Stream + serve_stream + the grace timer directly with a small
in-process harness; no HTTP, no FastAPI.
"""

from __future__ import annotations

import asyncio
import sys
import traceback
from pathlib import Path

sys.path.insert(0, "/app")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import stream_registry  # noqa: E402
from stream_registry import (  # noqa: E402
    MAX_LOG_EVENTS,
    Stream,
    StreamRegistry,
    _grace_timer,
    parse_last_event_id,
    serve_stream,
)


def _check(name: str, ok: bool, detail: str = "") -> bool:
    label = "PASS" if ok else "FAIL"
    print(f"[{label}] {name}{(' - ' + detail) if detail and not ok else ''}")
    return ok


# ---------------------------------------------------------------------------
# Log behaviour
# ---------------------------------------------------------------------------

def test_record_assigns_monotonic_seq() -> bool:
    async def go():
        s = Stream(user_email="u@x", conversation_id="c1")
        a = s.record("token", '{"content":"a"}')
        b = s.record("token", '{"content":"b"}')
        c = s.record("token", '{"content":"c"}')
        return (a, b, c) == (1, 2, 3) and [e[0] for e in s.event_log] == [1, 2, 3]

    return _check(
        "record() assigns monotonic seqs starting at 1",
        asyncio.run(go()),
    )


def test_replay_after_filters_strict() -> bool:
    async def go():
        s = Stream(user_email="u@x", conversation_id="c1")
        for i in range(5):
            s.record("token", f'{{"i":{i}}}')
        return [e[0] for e in s.replay_after(3)] == [4, 5]

    return _check(
        "replay_after(N) returns seqs strictly greater than N",
        asyncio.run(go()),
    )


def test_buffer_cap_marks_truncated() -> bool:
    async def go():
        s = Stream(user_email="u@x", conversation_id="c1")
        for _ in range(MAX_LOG_EVENTS + 5):
            s.record("token", '{"x":1}')
        head_seq = s.event_log[0][0]
        tail_seq = s.event_log[-1][0]
        return (
            len(s.event_log) == MAX_LOG_EVENTS
            and s.truncated is True
            and head_seq > 1   # oldest got dropped
            and tail_seq == MAX_LOG_EVENTS + 5
        )

    return _check(
        "overflowing the buffer drops oldest and flips truncated",
        asyncio.run(go()),
    )


def test_wait_for_new_returns_immediately_when_done() -> bool:
    """wait_for_new must wake on mark_done() so serve_stream can exit
    its read loop cleanly when the runner finishes."""
    async def go():
        s = Stream(user_email="u@x", conversation_id="c1")
        s.mark_done()
        # Should not block.
        await asyncio.wait_for(s.wait_for_new(0), timeout=0.5)
        return True

    try:
        return _check(
            "wait_for_new returns promptly after mark_done",
            asyncio.run(go()),
        )
    except asyncio.TimeoutError:
        return _check(
            "wait_for_new returns promptly after mark_done", False, "blocked"
        )


# ---------------------------------------------------------------------------
# serve_stream
# ---------------------------------------------------------------------------

async def _collect(stream: Stream, after_seq: int = 0, timeout: float = 0.5) -> list[dict]:
    out: list[dict] = []
    gen = serve_stream(stream, after_seq=after_seq)
    try:
        while True:
            try:
                evt = await asyncio.wait_for(gen.__anext__(), timeout=timeout)
            except (StopAsyncIteration, asyncio.TimeoutError):
                break
            out.append(evt)
    finally:
        await gen.aclose()
    return out


def test_serve_replays_then_streams_live() -> bool:
    """A serve_stream started AFTER some events are buffered should
    replay them; further records should stream live to the same listener."""
    async def go():
        s = Stream(user_email="u@x", conversation_id="c1")
        s.record("conversation", '{"id":"c1"}')
        s.record("token", '{"content":"hi"}')

        gen = serve_stream(s, after_seq=0)
        a = await asyncio.wait_for(gen.__anext__(), timeout=0.5)
        b = await asyncio.wait_for(gen.__anext__(), timeout=0.5)
        # Now record more; live consumer should see them.
        s.record("token", '{"content":"!"}')
        s.mark_done()
        c = await asyncio.wait_for(gen.__anext__(), timeout=0.5)
        # Drain.
        try:
            await asyncio.wait_for(gen.__anext__(), timeout=0.5)
        except StopAsyncIteration:
            pass
        await gen.aclose()
        return [
            a["event"], b["event"], c["event"],
            a["id"], b["id"], c["id"],
        ]

    out = asyncio.run(go())
    sid = None
    return _check(
        "serve_stream replays buffered events then yields live ones",
        out[:3] == ["conversation", "token", "token"]
        and all(eid.endswith(f"-{i}") for i, eid in enumerate(out[3:], start=1)),
        f"out={out}",
    )


def test_serve_after_seq_skips_replayed() -> bool:
    """A resume with after_seq=N must not re-emit seqs <= N."""
    async def go():
        s = Stream(user_email="u@x", conversation_id="c1")
        for i in range(5):
            s.record("token", f'{{"i":{i}}}')
        s.mark_done()
        events = await _collect(s, after_seq=3)
        return [e["id"].split("-")[-1] for e in events]

    seqs = asyncio.run(go())
    return _check(
        "serve_stream(after_seq=3) replays only seqs 4 and 5",
        seqs == ["4", "5"],
        f"seqs={seqs}",
    )


def test_serve_attaches_and_detaches() -> bool:
    """The listener-attached state must flip while serve_stream is
    consuming and detach when the generator closes."""
    async def go():
        s = Stream(user_email="u@x", conversation_id="c1")
        s.record("token", '{"x":1}')
        attached_during = []
        gen = serve_stream(s, after_seq=0)
        await asyncio.wait_for(gen.__anext__(), timeout=0.5)
        attached_during.append(s.listener_attached)
        await gen.aclose()
        attached_after = s.listener_attached
        return attached_during == [True] and attached_after is False

    return _check(
        "serve_stream attaches on entry and detaches on close",
        asyncio.run(go()),
    )


# ---------------------------------------------------------------------------
# Grace timer
# ---------------------------------------------------------------------------

def test_grace_fires_cancel_after_window() -> bool:
    """With a tiny grace window, an unreattached detach must fire
    cancel_event."""
    async def go():
        orig = stream_registry.GRACE_S
        stream_registry.GRACE_S = 0.1
        try:
            s = Stream(user_email="u@x", conversation_id="c1")
            grace = asyncio.create_task(_grace_timer(s))
            s.detach_listener()
            try:
                await asyncio.wait_for(s.cancel_event.wait(), timeout=0.5)
                fired = True
            except asyncio.TimeoutError:
                fired = False
            grace.cancel()
            return fired
        finally:
            stream_registry.GRACE_S = orig

    return _check(
        "grace timer fires cancel_event after the window expires",
        asyncio.run(go()),
    )


def test_grace_does_not_fire_if_reattached() -> bool:
    """Reattach during the grace window must cancel the cancel."""
    async def go():
        orig = stream_registry.GRACE_S
        stream_registry.GRACE_S = 0.2
        try:
            s = Stream(user_email="u@x", conversation_id="c1")
            grace = asyncio.create_task(_grace_timer(s))
            s.detach_listener()
            await asyncio.sleep(0.05)
            s.attach_listener()
            await asyncio.sleep(0.3)
            fired = s.cancel_event.is_set()
            grace.cancel()
            return fired is False
        finally:
            stream_registry.GRACE_S = orig

    return _check(
        "reattach within the grace window prevents cancellation",
        asyncio.run(go()),
    )


# ---------------------------------------------------------------------------
# parse_last_event_id
# ---------------------------------------------------------------------------

def test_parse_last_event_id_basic() -> bool:
    assert parse_last_event_id(None) is None
    assert parse_last_event_id("") is None
    assert parse_last_event_id("   ") is None
    assert parse_last_event_id("notaseq") is None
    assert parse_last_event_id("abc123-notanint") is None
    assert parse_last_event_id("abc123-42") == ("abc123", 42)
    # The stream id is a 32-char hex uuid; rfind on '-' picks the last
    # one so any '-' inside the id would still parse, just less likely.
    return _check("parse_last_event_id handles valid + invalid inputs", True)


# ---------------------------------------------------------------------------
# StreamRegistry
# ---------------------------------------------------------------------------

def test_registry_register_get() -> bool:
    async def go():
        r = StreamRegistry()
        s = Stream(user_email="u@x", conversation_id="c1")
        r.register(s)
        return r.get(s.stream_id) is s and r.get("nope") is None

    return _check("registry register/get round-trips", asyncio.run(go()))


def test_registry_janitor_drops_completed_streams() -> bool:
    async def go():
        orig_int = stream_registry.JANITOR_INTERVAL_S
        orig_ret = stream_registry.DONE_RETENTION_S
        stream_registry.JANITOR_INTERVAL_S = 0.05
        stream_registry.DONE_RETENTION_S = 0.1
        try:
            r = StreamRegistry()
            s = Stream(user_email="u@x", conversation_id="c1")
            r.register(s)
            r.start_janitor()
            s.mark_done()
            # Wait long enough for retention + a janitor pass.
            await asyncio.sleep(0.35)
            evicted = r.get(s.stream_id) is None
            if r._janitor:
                r._janitor.cancel()
            return evicted
        finally:
            stream_registry.JANITOR_INTERVAL_S = orig_int
            stream_registry.DONE_RETENTION_S = orig_ret

    return _check(
        "janitor evicts streams DONE_RETENTION_S after completion",
        asyncio.run(go()),
    )


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

TESTS = [
    test_record_assigns_monotonic_seq,
    test_replay_after_filters_strict,
    test_buffer_cap_marks_truncated,
    test_wait_for_new_returns_immediately_when_done,
    test_serve_replays_then_streams_live,
    test_serve_after_seq_skips_replayed,
    test_serve_attaches_and_detaches,
    test_grace_fires_cancel_after_window,
    test_grace_does_not_fire_if_reattached,
    test_parse_last_event_id_basic,
    test_registry_register_get,
    test_registry_janitor_drops_completed_streams,
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
