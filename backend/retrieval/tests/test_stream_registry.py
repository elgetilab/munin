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
    MAX_LOG_BYTES,
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


def test_buffer_byte_cap_evicts_before_the_event_cap() -> bool:
    """A few huge events must not pin megabytes just because the COUNT is low.

    Raising MAX_LOG_EVENTS to 20k without a byte bound would be the 96921b7
    mistake again: sizing a buffer in the wrong unit. Most events are a few
    hundred bytes of token JSON, but one tool result or evidence passage can
    be orders of magnitude larger.
    """
    s = Stream(user_email="u@x", conversation_id="c1")
    big = "x" * (1024 * 1024)          # 1 MB per event
    for _ in range(12):                # 12 MB total, well under MAX_LOG_EVENTS
        s.record("token", big)
    ok = (
        len(s.event_log) < 12                       # evicted on bytes, not count
        and s._log_bytes <= MAX_LOG_BYTES           # bound actually holds
        and s.truncated                             # and it says so
    )
    return _check("byte cap evicts before the event cap", ok,
                  f"events={len(s.event_log)} bytes={s._log_bytes}")


def test_buffer_holds_a_realistic_turn() -> bool:
    """The regression that let three users hit 410 in six minutes.

    A ~500-word answer measures at ~2,600 SSE events. At the old cap of 1000
    the buffer could not hold a single turn, so a client that dropped for
    20-30s came back to a checkpoint already rolled past and got a legitimate
    410. The buffer has to outlast one turn or it is not a reconnect buffer.
    """
    s = Stream(user_email="u@x", conversation_id="c1")
    # 11,330 is the MEASURED size of a heavy research turn (corpus search plus
    # two GROBID full-text extractions) on 2026-09-02. A buffer that cannot
    # hold one of those cannot serve a reconnect on the turns people most want
    # back, which is the bug this file exists to prevent recurring.
    for _ in range(11330):
        s.record("token", '{"content":"word "}')
    ok = not s.truncated and s.can_resume_from(5)
    return _check("buffer survives a measured 11,330-event research turn", ok,
                  f"truncated={s.truncated} events={len(s.event_log)}")


def test_count_cap_warns_once_when_the_count_binds() -> bool:
    """The count is the bound that actually binds, so it must announce itself.

    Measured 2026-09-02: a heavy turn averages ~32 bytes per event, so 8 MB
    permits ~259,000 events while MAX_LOG_EVENTS permits far fewer. The byte
    warning added earlier that day therefore reports a bound that will almost
    never fire; without this one, the failure mode that DOES occur is silent,
    which is how the original 1000-event bug survived months.
    """
    import logging

    class _Capture(logging.Handler):
        def __init__(self):
            super().__init__()
            self.msgs = []

        def emit(self, record):
            self.msgs.append(record.getMessage())

    reg_log = logging.getLogger("stream_registry")
    cap = _Capture()
    reg_log.addHandler(cap)
    old_level = reg_log.level
    reg_log.setLevel(logging.WARNING)
    try:
        s = Stream(user_email="u@x", conversation_id="c1")
        for _ in range(MAX_LOG_EVENTS + 40):
            s.record("token", '{"c":"x"}')
        count_warnings = [m for m in cap.msgs if "EVENT cap" in m]
        byte_warnings = [m for m in cap.msgs if "BYTE cap" in m]
    finally:
        reg_log.removeHandler(cap)
        reg_log.setLevel(old_level)

    ok = len(count_warnings) == 1 and not byte_warnings and s.truncated
    return _check("count cap warns exactly once, without a byte false-alarm",
                  ok, f"count={len(count_warnings)} byte={len(byte_warnings)}")


def test_byte_cap_warns_once_and_only_when_bytes_bind() -> bool:
    """The byte cap must announce itself, exactly once, and not cry wolf.

    Which bound binds is the operationally interesting question: MAX_LOG_EVENTS
    is sized for token events, so the byte cap binding instead means the turn
    carries large payloads and is resumable across a much shorter window than
    20000 events suggests. Nothing reported that when the cap was introduced,
    which is how the 2026-09-02 incident (a bound too small for real turns)
    went unnoticed for months.

    Once per stream, not per eviction: the loop runs on most record() calls
    after the buffer fills, so per-eviction logging would emit thousands of
    lines a turn and get filtered, which is the same as no signal.
    """
    import logging

    class _Capture(logging.Handler):
        def __init__(self):
            super().__init__()
            self.msgs = []

        def emit(self, record):
            self.msgs.append(record.getMessage())

    reg_log = logging.getLogger("stream_registry")
    cap = _Capture()
    reg_log.addHandler(cap)
    old_level = reg_log.level
    reg_log.setLevel(logging.WARNING)
    try:
        # Bytes bind: 12 x 1 MB is far under MAX_LOG_EVENTS.
        s_big = Stream(user_email="u@x", conversation_id="c1")
        for _ in range(14):
            s_big.record("token", "x" * (1024 * 1024))
        byte_warnings = [m for m in cap.msgs if "BYTE cap" in m]

        # Count binds: small events, so the byte cap must stay silent.
        cap.msgs.clear()
        s_many = Stream(user_email="u@x", conversation_id="c1")
        for _ in range(MAX_LOG_EVENTS + 50):
            s_many.record("token", '{"c":"x"}')
        false_alarms = [m for m in cap.msgs if "BYTE cap" in m]
    finally:
        reg_log.removeHandler(cap)
        reg_log.setLevel(old_level)

    ok = len(byte_warnings) == 1 and not false_alarms and s_many.truncated
    return _check("byte cap warns exactly once, and not when the count binds",
                  ok, f"byte={len(byte_warnings)} false={len(false_alarms)}")


def test_bytes_tracked_exactly_across_eviction() -> bool:
    """_log_bytes is maintained incrementally, so a drift bug would silently
    shrink the buffer over a long turn until it stopped serving reconnects."""
    s = Stream(user_email="u@x", conversation_id="c1")
    for i in range(500):
        s.record("token", f'{{"content":"{i}"}}')
    expected = sum(len(d) + len(n) for _q, n, d in s.event_log)
    return _check("byte accounting matches the retained log",
                  s._log_bytes == expected,
                  f"tracked={s._log_bytes} actual={expected}")


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

def test_grace_promotes_to_background() -> bool:
    """Background turns: grace expiry with cap room must PROMOTE the
    stream (background=True, keeps running), not cancel it. This
    replaces the pre-background-turns test that asserted cancellation
    here."""
    async def go():
        orig = stream_registry.GRACE_S
        stream_registry.GRACE_S = 0.1
        try:
            s = Stream(user_email="u@x", conversation_id="c1")
            s.attach_listener()
            grace = asyncio.create_task(_grace_timer(s))
            s.detach_listener()
            await asyncio.sleep(0.3)
            promoted = s.background and not s.cancel_event.is_set()
            grace.cancel()
            return promoted
        finally:
            stream_registry.GRACE_S = orig

    return _check(
        "grace expiry promotes to background instead of cancelling",
        asyncio.run(go()),
    )


def test_grace_does_not_fire_if_reattached() -> bool:
    """Reattach during the grace window: no cancel, no promotion."""
    async def go():
        orig = stream_registry.GRACE_S
        stream_registry.GRACE_S = 0.2
        try:
            s = Stream(user_email="u@x", conversation_id="c1")
            s.attach_listener()
            grace = asyncio.create_task(_grace_timer(s))
            s.detach_listener()
            await asyncio.sleep(0.05)
            s.attach_listener()
            await asyncio.sleep(0.3)
            ok = not s.cancel_event.is_set() and not s.background
            grace.cancel()
            return ok
        finally:
            stream_registry.GRACE_S = orig

    return _check(
        "reattach within the grace window prevents cancellation",
        asyncio.run(go()),
    )


def test_grace_cancels_at_background_cap() -> bool:
    """A user already at MAX_BACKGROUND_PER_USER gets the pre-background
    behavior: grace expiry cancels. Seeds the process-wide registry
    (which _grace_timer consults) and cleans it up."""
    async def go():
        orig = stream_registry.GRACE_S
        stream_registry.GRACE_S = 0.1
        seeded: list[Stream] = []
        try:
            for i in range(stream_registry.MAX_BACKGROUND_PER_USER):
                other = Stream(user_email="u@x", conversation_id=f"bg{i}")
                other.background = True
                stream_registry.registry.register(other)
                seeded.append(other)
            s = Stream(user_email="u@x", conversation_id="c1")
            s.attach_listener()
            grace = asyncio.create_task(_grace_timer(s))
            s.detach_listener()
            try:
                await asyncio.wait_for(s.cancel_event.wait(), timeout=0.5)
                cancelled = True
            except asyncio.TimeoutError:
                cancelled = False
            grace.cancel()
            return (
                cancelled
                and not s.background
                and s.cancel_reason == (
                    "cancelled: too many background turns running"
                )
            )
        finally:
            stream_registry.GRACE_S = orig
            for other in seeded:
                stream_registry.registry._streams.pop(other.stream_id, None)

    return _check(
        "grace expiry at the per-user background cap still cancels",
        asyncio.run(go()),
    )


def test_background_hard_cap_cancels() -> bool:
    """A listenerless background stream must be cancelled once it is
    older than BACKGROUND_MAX_S (runaway guard)."""
    async def go():
        orig_grace = stream_registry.GRACE_S
        orig_max = stream_registry.BACKGROUND_MAX_S
        stream_registry.GRACE_S = 0.05
        stream_registry.BACKGROUND_MAX_S = 0.2
        try:
            s = Stream(user_email="u@x", conversation_id="c1")
            s.attach_listener()
            grace = asyncio.create_task(_grace_timer(s))
            s.detach_listener()
            await asyncio.sleep(0.1)
            was_promoted = s.background
            try:
                await asyncio.wait_for(s.cancel_event.wait(), timeout=0.5)
                cancelled = True
            except asyncio.TimeoutError:
                cancelled = False
            grace.cancel()
            return (
                was_promoted
                and cancelled
                and s.cancel_reason == (
                    "cancelled: background time limit reached"
                )
            )
        finally:
            stream_registry.GRACE_S = orig_grace
            stream_registry.BACKGROUND_MAX_S = orig_max

    return _check(
        "listenerless background stream is cancelled at BACKGROUND_MAX_S",
        asyncio.run(go()),
    )


def test_reattach_clears_background() -> bool:
    """Re-attaching to a background stream must clear the flag (freeing
    the cap slot) and keep the stream alive; a later detach re-enters
    the grace cycle."""
    async def go():
        orig = stream_registry.GRACE_S
        stream_registry.GRACE_S = 0.1
        try:
            s = Stream(user_email="u@x", conversation_id="c1")
            s.attach_listener()
            grace = asyncio.create_task(_grace_timer(s))
            s.detach_listener()
            await asyncio.sleep(0.2)
            promoted = s.background
            s.attach_listener()
            cleared = not s.background
            # Detach again: the timer loops and re-promotes.
            s.detach_listener()
            await asyncio.sleep(0.3)
            repromoted = s.background and not s.cancel_event.is_set()
            grace.cancel()
            return promoted and cleared and repromoted
        finally:
            stream_registry.GRACE_S = orig

    return _check(
        "reattach clears background; next detach re-promotes",
        asyncio.run(go()),
    )


# ---------------------------------------------------------------------------
# Listener refcount (two tabs on one stream)
# ---------------------------------------------------------------------------

def test_listener_refcount_second_tab() -> bool:
    """Two listeners attached, one detaches: the stream must still count
    as attached and the grace cycle must not engage. Only when the last
    listener detaches does promotion happen."""
    async def go():
        orig = stream_registry.GRACE_S
        stream_registry.GRACE_S = 0.1
        try:
            s = Stream(user_email="u@x", conversation_id="c1")
            s.attach_listener()   # tab 1
            s.attach_listener()   # tab 2
            grace = asyncio.create_task(_grace_timer(s))
            s.detach_listener()   # tab 2 closes
            await asyncio.sleep(0.3)
            still_attached = s.listener_attached
            untouched = not s.background and not s.cancel_event.is_set()
            s.detach_listener()   # tab 1 closes
            await asyncio.sleep(0.3)
            promoted = s.background and not s.cancel_event.is_set()
            grace.cancel()
            return still_attached and untouched and promoted
        finally:
            stream_registry.GRACE_S = orig

    return _check(
        "refcounted listeners: one of two detaching does not start grace",
        asyncio.run(go()),
    )


# ---------------------------------------------------------------------------
# Registry lookup helpers (background turns)
# ---------------------------------------------------------------------------

def test_registry_count_background() -> bool:
    async def go():
        r = StreamRegistry()
        a = Stream(user_email="u@x", conversation_id="c1")
        a.background = True
        b = Stream(user_email="u@x", conversation_id="c2")
        b.background = True
        b.mark_done()          # done: must not count
        c = Stream(user_email="u@x", conversation_id="c3")  # not background
        d = Stream(user_email="v@x", conversation_id="c4")
        d.background = True    # other user: must not count
        for s in (a, b, c, d):
            r.register(s)
        return (
            r.count_background("u@x") == 1
            and r.count_background("u@x", exclude_stream_id=a.stream_id) == 0
            and r.count_background("v@x") == 1
        )

    return _check(
        "count_background filters done/other-user/excluded streams",
        asyncio.run(go()),
    )


def test_registry_find_for_conversation() -> bool:
    async def go():
        r = StreamRegistry()
        old = Stream(user_email="u@x", conversation_id="c1")
        old.started_ts -= 100.0  # force ordering
        new = Stream(user_email="u@x", conversation_id="c1")
        other_user = Stream(user_email="v@x", conversation_id="c1")
        for s in (old, new, other_user):
            r.register(s)
        found = r.find_for_conversation("c1", "u@x")
        none_for_wrong_user = r.find_for_conversation("c1", "w@x") is None
        none_for_unknown = r.find_for_conversation("nope", "u@x") is None
        return (
            found is new and none_for_wrong_user and none_for_unknown
        )

    return _check(
        "find_for_conversation returns newest owned stream only",
        asyncio.run(go()),
    )


def test_registry_in_flight_conversation_ids() -> bool:
    async def go():
        r = StreamRegistry()
        live = Stream(user_email="u@x", conversation_id="c1")
        finished = Stream(user_email="u@x", conversation_id="c2")
        finished.mark_done()
        ephemeral = Stream(user_email="u@x", conversation_id=None)
        for s in (live, finished, ephemeral):
            r.register(s)
        return r.in_flight_conversation_ids("u@x") == {"c1"}

    return _check(
        "in_flight_conversation_ids: live, owned, non-ephemeral only",
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
    test_buffer_byte_cap_evicts_before_the_event_cap,
    test_buffer_holds_a_realistic_turn,
    test_bytes_tracked_exactly_across_eviction,
    test_byte_cap_warns_once_and_only_when_bytes_bind,
    test_count_cap_warns_once_when_the_count_binds,
    test_wait_for_new_returns_immediately_when_done,
    test_serve_replays_then_streams_live,
    test_serve_after_seq_skips_replayed,
    test_serve_attaches_and_detaches,
    test_grace_promotes_to_background,
    test_grace_does_not_fire_if_reattached,
    test_grace_cancels_at_background_cap,
    test_background_hard_cap_cancels,
    test_reattach_clears_background,
    test_listener_refcount_second_tab,
    test_registry_count_background,
    test_registry_find_for_conversation,
    test_registry_in_flight_conversation_ids,
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
