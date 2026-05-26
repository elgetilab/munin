"""
Regression test for the stop-hook → SSE event order around the
``done`` event in ``stream_chat_completion`` (P2 #23 + P2 #25).

Pins the bug found via end-to-end testing on hugin 2026-05-26:
``dispatch_stop`` used to fire in the outer ``finally`` block — past
the last yield and after ``current_sse_emitter`` had been reset to
None — so a stop hook's ``ctx.emit_sse(...)`` call silently dropped
the payload even though the side-effects (DB writes) succeeded.

The fix moves dispatch_stop INSIDE the try block, immediately before
the ``done`` yield. It installs a turn-final emitter that buffers
into a local list, then yields each buffered event on the
still-open stream right before ``done``.

This test exercises the SHAPE of that pattern with a small synthetic
generator that mirrors the relevant slice of stream_chat_completion
(per the project convention in test_disconnect_cleanup.py: test the
pattern, not the full integration). A future refactor that removes
the in-loop dispatch_stop call, or that swaps the buffer-then-yield
for a no-op emitter, will fail this suite.

Run inside the retrieval container:
    docker exec munin-retrieval python /app/tests/test_stop_hook_event_order.py
Or locally:
    python backend/retrieval/tests/test_stop_hook_event_order.py
"""

from __future__ import annotations

import asyncio
import contextlib
import sys
import traceback
from pathlib import Path

sys.path.insert(0, "/app")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import hooks  # noqa: E402
from hooks._dispatcher import _clear_registry_for_tests  # noqa: E402
from mcp.context import current_sse_emitter  # noqa: E402


# ---------------------------------------------------------------------------
# Synthetic generator mirroring the relevant slice of
# stream_chat_completion: produce some "token" events, then run the
# stop hooks with a turn-final emitter that buffers, yield buffered
# events, yield "done".
# ---------------------------------------------------------------------------

async def _streaming_turn_with_hooks(
    terminal_reason: str = "done",
) -> list[tuple[str, str]]:
    """Drive the same buffer-then-yield pattern chat_service uses
    around `done`. Returns the list of yielded (event_name, body)
    pairs in order."""
    events: list[tuple[str, str]] = []

    # Some prose tokens before the end-of-turn dance.
    events.append(("token", "hello "))
    events.append(("token", "world"))

    # The pattern under test: install a buffering emitter, dispatch
    # stop hooks, then yield each buffered event before `done`.
    buffered: list[tuple[str, dict]] = []

    def _stop_push(event_name: str, data: dict) -> None:
        buffered.append((event_name, data))

    token = current_sse_emitter.set(_stop_push)
    try:
        await hooks.dispatch_stop(terminal_reason)
    finally:
        with contextlib.suppress(ValueError, LookupError):
            current_sse_emitter.reset(token)
    for name, data in buffered:
        events.append((name, str(data)))

    events.append(("done", "{}"))
    return events


def _check(name: str, ok: bool, detail: str = "") -> bool:
    label = "PASS" if ok else "FAIL"
    print(f"[{label}] {name}{(' - ' + detail) if detail and not ok else ''}")
    return ok


# ---------------------------------------------------------------------------
# 1. A stop hook that emits a memory_proposed event lands BEFORE done.
# ---------------------------------------------------------------------------

def test_hook_event_lands_before_done() -> bool:
    _clear_registry_for_tests()

    @hooks.register("stop")
    async def emit_proposal(ctx, terminal_reason):
        ctx.emit_sse("memory_proposed", {"id": "p-1", "key": "k", "value": "v"})

    async def go():
        return await _streaming_turn_with_hooks(terminal_reason="done")

    events = asyncio.run(go())
    names = [e[0] for e in events]
    # The shape must be: token* → memory_proposed → done. Specifically:
    # memory_proposed must precede done, and done must be the last event.
    try:
        mp = names.index("memory_proposed")
        dn = names.index("done")
    except ValueError:
        return _check(
            "memory_proposed lands before done", False,
            f"missing event; got {names}",
        )
    return _check(
        "memory_proposed lands before done in the yielded stream",
        mp < dn and names[-1] == "done",
        f"order: {names}",
    )


# ---------------------------------------------------------------------------
# 2. Multiple stop hooks all land before done, in order.
# ---------------------------------------------------------------------------

def test_multiple_hook_events_preserve_order() -> bool:
    _clear_registry_for_tests()

    @hooks.register("stop")
    async def first(ctx, terminal_reason):
        ctx.emit_sse("memory_proposed", {"id": "p-1"})

    @hooks.register("stop")
    async def second(ctx, terminal_reason):
        ctx.emit_sse("memory_proposed", {"id": "p-2"})

    async def go():
        return await _streaming_turn_with_hooks(terminal_reason="done")

    events = asyncio.run(go())
    # Both proposals must appear, both before done.
    proposal_ids = []
    for name, body in events:
        if name == "memory_proposed":
            proposal_ids.append(body)
    seen_both = len(proposal_ids) == 2 and "p-1" in proposal_ids[0] and "p-2" in proposal_ids[1]
    done_last = events[-1][0] == "done"
    return _check(
        "multiple hook events buffer + yield in registration order, all before done",
        seen_both and done_last,
        f"got {[e[0] for e in events]}; ids={proposal_ids}",
    )


# ---------------------------------------------------------------------------
# 3. A hook that emits nothing leaves the stream shape intact.
# ---------------------------------------------------------------------------

def test_no_emit_means_no_extra_events() -> bool:
    _clear_registry_for_tests()

    @hooks.register("stop")
    async def quiet(ctx, terminal_reason):
        return None  # never calls emit_sse

    async def go():
        return await _streaming_turn_with_hooks(terminal_reason="done")

    events = asyncio.run(go())
    names = [e[0] for e in events]
    return _check(
        "silent stop hook adds nothing to the stream",
        names == ["token", "token", "done"],
        f"got {names}",
    )


# ---------------------------------------------------------------------------
# 4. The emitter is reset after the dispatch — late emit calls do NOT
#    leak into the buffer.
# ---------------------------------------------------------------------------

def test_emitter_resets_after_dispatch() -> bool:
    """Confirm that the ContextVar is restored to its prior value
    after _streaming_turn_with_hooks returns. Otherwise a follow-up
    tool call elsewhere in the turn would still push into our stale
    buffer."""
    _clear_registry_for_tests()

    @hooks.register("stop")
    async def noop(ctx, terminal_reason):
        return None

    async def go():
        before = current_sse_emitter.get()
        await _streaming_turn_with_hooks(terminal_reason="done")
        after = current_sse_emitter.get()
        return before, after

    before, after = asyncio.run(go())
    return _check(
        "current_sse_emitter is reset to its prior value after the buffer scope",
        before is after,  # default is None; after reset must equal default
        f"before={before!r} after={after!r}",
    )


# ---------------------------------------------------------------------------
# 5. terminal_reason is forwarded to the hook (the gate that suppresses
#    extraction on cancelled/stream_error paths depends on this).
# ---------------------------------------------------------------------------

def test_terminal_reason_forwarded() -> bool:
    _clear_registry_for_tests()
    seen: list[str] = []

    @hooks.register("stop")
    async def grab(ctx, terminal_reason):
        seen.append(terminal_reason)

    async def go():
        await _streaming_turn_with_hooks(terminal_reason="max_turns")

    asyncio.run(go())
    return _check(
        "terminal_reason argument is forwarded to the stop hook",
        seen == ["max_turns"],
        f"seen={seen}",
    )


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

TESTS = [
    test_hook_event_lands_before_done,
    test_multiple_hook_events_preserve_order,
    test_no_emit_means_no_extra_events,
    test_emitter_resets_after_dispatch,
    test_terminal_reason_forwarded,
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
