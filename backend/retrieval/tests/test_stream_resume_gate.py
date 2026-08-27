"""
Unit tests for the resume gate in stream_registry.Stream.

WHY THIS EXISTS. A user reported "stream is no longer available on this server"
appearing constantly while simply waiting for output. Root cause: `record()`
caps the replay buffer at MAX_LOG_EVENTS (1000) and, on the first overflow,
latches `truncated = True` for the life of the stream. The resume endpoint
refused on that flag alone, without ever consulting the client's
`Last-Event-ID` (which it parsed sixteen lines LATER). So any turn longer than
the cap became permanently unresumable, even for a client reconnecting one
event behind the head.

MEASURED 2026-08-27 over that user's 169 assistant turns: median ~2,061 SSE
events per turn against a 1,000 cap, p90 ~6,554, and 78% of turns over the cap.

These tests pin the distinction the fix turns on: `truncated` means "the buffer
overflowed at some point", NOT "this client's checkpoint was lost".

    docker exec munin-retrieval python /app/tests/test_stream_resume_gate.py
"""

from __future__ import annotations

import sys
import traceback

sys.path.insert(0, "/app")
sys.path.insert(0, ".")

import stream_registry as sr  # noqa: E402


def _check(name: str, ok: bool, detail: str = "") -> bool:
    print(("[PASS] " if ok else "[FAIL] ") + name + (f"  {detail}" if detail and not ok else ""))
    return ok


def _stream_with(n_events: int) -> sr.Stream:
    s = sr.Stream(user_email="u@example.com", conversation_id="c1")
    for i in range(n_events):
        s.record("token", '{"content":"x"}')
    return s


# --- the regression itself --------------------------------------------------

def test_overflowed_stream_still_resumable_near_head() -> bool:
    """THE bug. A turn well past the cap, with a client one event behind the
    head, must be servable. This returned 410 before the fix."""
    s = _stream_with(sr.MAX_LOG_EVENTS + 500)
    assert s.truncated, "precondition: the buffer must have overflowed"
    head = s._next_seq - 1
    return _check("overflowed stream resumable from one behind head",
                  s.can_resume_from(head - 1) is True)


def test_overflowed_stream_resumable_exactly_at_oldest() -> bool:
    """Boundary: the client holds the event just before the oldest retained
    one, so everything it still needs is present."""
    s = _stream_with(sr.MAX_LOG_EVENTS + 500)
    oldest = s.oldest_retained_seq()
    return _check("resumable when checkpoint == oldest_retained - 1",
                  s.can_resume_from(oldest - 1) is True)


def test_checkpoint_below_buffer_is_refused() -> bool:
    """The case the 410 is actually FOR: the client's checkpoint fell out of
    the buffer, so replaying would silently skip events."""
    s = _stream_with(sr.MAX_LOG_EVENTS + 500)
    oldest = s.oldest_retained_seq()
    return _check("checkpoint below the buffer is refused",
                  s.can_resume_from(oldest - 2) is False)


def test_replay_from_zero_refused_after_overflow() -> bool:
    """after_seq=0 means 'replay everything'. Once anything has been dropped
    that is a promise we cannot keep, so it must still 410."""
    s = _stream_with(sr.MAX_LOG_EVENTS + 500)
    return _check("replay-from-zero refused once truncated",
                  s.can_resume_from(0) is False)


# --- the untruncated path must be completely unchanged ---------------------

def test_short_stream_always_resumable() -> bool:
    s = _stream_with(10)
    ok = (not s.truncated
          and s.can_resume_from(0) is True
          and s.can_resume_from(5) is True
          and s.can_resume_from(10) is True)
    return _check("short stream resumable from anywhere", ok)


def test_exactly_at_cap_not_truncated() -> bool:
    """Off-by-one guard: the cap is the number retained, so exactly
    MAX_LOG_EVENTS must NOT trip truncation."""
    s = _stream_with(sr.MAX_LOG_EVENTS)
    return _check("exactly MAX_LOG_EVENTS does not truncate",
                  s.truncated is False and s.can_resume_from(0) is True)


def test_one_past_cap_truncates() -> bool:
    s = _stream_with(sr.MAX_LOG_EVENTS + 1)
    return _check("one past the cap truncates", s.truncated is True)


# --- accessor behaviour -----------------------------------------------------

def test_oldest_retained_seq_tracks_the_head() -> bool:
    s = _stream_with(sr.MAX_LOG_EVENTS + 250)
    # 250 dropped, seqs start at 1, so the oldest retained is 251.
    return _check("oldest_retained_seq tracks drops",
                  s.oldest_retained_seq() == 251, f"got {s.oldest_retained_seq()}")


def test_empty_log_reports_zero() -> bool:
    s = sr.Stream(user_email="u@example.com", conversation_id="c1")
    return _check("empty log -> oldest_retained_seq 0 and resumable",
                  s.oldest_retained_seq() == 0 and s.can_resume_from(0) is True)


def test_buffer_never_exceeds_cap() -> bool:
    s = _stream_with(sr.MAX_LOG_EVENTS + 5000)
    return _check("buffer size stays capped",
                  len(s.event_log) == sr.MAX_LOG_EVENTS, f"got {len(s.event_log)}")


TESTS = [
    test_overflowed_stream_still_resumable_near_head,
    test_overflowed_stream_resumable_exactly_at_oldest,
    test_checkpoint_below_buffer_is_refused,
    test_replay_from_zero_refused_after_overflow,
    test_short_stream_always_resumable,
    test_exactly_at_cap_not_truncated,
    test_one_past_cap_truncates,
    test_oldest_retained_seq_tracks_the_head,
    test_empty_log_reports_zero,
    test_buffer_never_exceeds_cap,
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
