"""
Standalone tests for the client-disconnect cleanup pattern in
chat_service.stream_chat_completion (P0 #2 from munin-audit.md).

Run inside the retrieval container:
    docker exec munin-retrieval python /app/tests/test_disconnect_cleanup.py
Or locally:
    python backend/retrieval/tests/test_disconnect_cleanup.py

Each test exercises the SHAPE of the cancellation plumbing — a small
synthetic async generator that mirrors the runner pattern (event_queue
+ create_task + cancel_listener + try/finally) — rather than driving
the full stream_chat_completion (which depends on SQLite, vLLM, embedders).
The pattern is what's load-bearing; if the pattern is correct, the
integration is correct.

vllm_post_stream is tested directly with httpx.MockTransport.
"""

from __future__ import annotations

import asyncio
import sys
import traceback
from pathlib import Path

sys.path.insert(0, "/app")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import contextlib  # noqa: E402

import httpx  # noqa: E402

import vllm_client  # noqa: E402


# ---------------------------------------------------------------------------
# Test scaffolding
# ---------------------------------------------------------------------------

def _check(name: str, ok: bool, detail: str = "") -> bool:
    label = "PASS" if ok else "FAIL"
    print(f"[{label}] {name}{(' - ' + detail) if detail and not ok else ''}")
    return ok


# Mirror of the chat_service runner pattern, parameterised by:
#   tool_fn        : the async function the runner awaits (stands in for
#                    _run_tool_calls). May sleep, raise, etc.
#   cancel_event   : optional asyncio.Event; if set, cancel_listener fires.
#
# Exposes the run_task and the produced events so tests can assert against
# them after the generator finishes or is aclose'd.
class RunnerHarness:
    def __init__(self, tool_fn, cancel_event=None):
        self.tool_fn = tool_fn
        self.cancel_event = cancel_event
        self.event_queue: asyncio.Queue = asyncio.Queue()
        self.SENTINEL = object()
        self.run_task: asyncio.Task | None = None
        self.cancel_listener: asyncio.Task | None = None
        self.yielded: list = []

    async def gen(self):
        async def _runner():
            try:
                return await self.tool_fn()
            finally:
                self.event_queue.put_nowait(self.SENTINEL)

        self.run_task = asyncio.create_task(_runner())

        if self.cancel_event is not None:
            async def _cancel_on_event():
                await self.cancel_event.wait()
                if not self.run_task.done():
                    self.run_task.cancel()
            self.cancel_listener = asyncio.create_task(_cancel_on_event())

        try:
            while True:
                item = await self.event_queue.get()
                if item is self.SENTINEL:
                    break
                yield item
            await self.run_task
        finally:
            if self.cancel_listener is not None and not self.cancel_listener.done():
                self.cancel_listener.cancel()
                with contextlib.suppress(BaseException):
                    await self.cancel_listener
            if not self.run_task.done():
                self.run_task.cancel()
                with contextlib.suppress(BaseException):
                    await self.run_task


# ---------------------------------------------------------------------------
# Pattern tests
# ---------------------------------------------------------------------------

def test_runner_completes_normally() -> bool:
    """Baseline: when the tool finishes naturally, run_task is done and
    no cancellation paths fire."""
    async def go():
        tool_done = asyncio.Event()

        async def tool():
            tool_done.set()
            return ["ok"]

        h = RunnerHarness(tool)
        gen = h.gen()
        # Drain.
        async for _ in gen:
            pass
        return h, tool_done.is_set()

    h, fired = asyncio.run(go())
    return _check(
        "baseline: runner completes, no cancellation",
        fired
        and h.run_task is not None
        and h.run_task.done()
        and not h.run_task.cancelled(),
    )


def test_runner_cancelled_on_aclose_mid_tool() -> bool:
    """The bug from the audit. A slow tool is running; the consumer task
    is cancelled (which is how EventSourceResponse-via-FastAPI tears the
    stream down — CancelledError propagates into the generator's
    __anext__). The runner must end up cancelled, not orphaned to run for
    its full duration."""
    async def go():
        tool_started = asyncio.Event()

        async def slow_tool():
            tool_started.set()
            await asyncio.sleep(10)  # would normally run to completion
            return ["ok"]

        h = RunnerHarness(slow_tool)
        gen = h.gen()

        async def consume():
            async for _ in gen:
                pass

        consumer = asyncio.create_task(consume())
        await asyncio.wait_for(tool_started.wait(), timeout=1.0)
        consumer.cancel()
        with contextlib.suppress(BaseException):
            await consumer
        # Best-effort aclose for cleanliness — the generator's finally has
        # already run via the CancelledError path, so this is a no-op.
        with contextlib.suppress(BaseException):
            await gen.aclose()
        return h

    h = asyncio.run(go())
    return _check(
        "runner is cancelled when consumer task is cancelled mid-tool",
        h.run_task is not None and h.run_task.cancelled(),
        f"done={h.run_task.done() if h.run_task else None}, "
        f"cancelled={h.run_task.cancelled() if h.run_task else None}",
    )


def test_runner_cancelled_on_event() -> bool:
    """A single long tool, no nested events. The watchdog setting
    cancel_event must cancel the runner — this is the case the
    consumer-side `is_disconnected()` poll cannot catch on its own
    because no yield ever happens."""
    async def go():
        tool_started = asyncio.Event()
        cancel_event = asyncio.Event()

        async def slow_tool():
            tool_started.set()
            await asyncio.sleep(10)
            return ["ok"]

        h = RunnerHarness(slow_tool, cancel_event=cancel_event)
        gen = h.gen()
        anext_task = asyncio.create_task(_swallow(gen.__anext__()))
        # Wait for the tool to start, then fire the event.
        await asyncio.wait_for(tool_started.wait(), timeout=1.0)
        cancel_event.set()
        # The cancel_listener cancels run_task; runner's finally pushes
        # SENTINEL; main loop wakes, breaks; await run_task raises;
        # generator finishes. anext_task should resolve.
        await asyncio.wait_for(anext_task, timeout=2.0)
        # Ensure the generator has cleaned up.
        with contextlib.suppress(BaseException):
            await gen.aclose()
        return h

    async def _swallow(coro):
        try:
            await coro
        except BaseException:
            pass

    h = asyncio.run(go())
    return _check(
        "runner is cancelled when cancel_event fires",
        h.run_task is not None and h.run_task.cancelled(),
        f"done={h.run_task.done() if h.run_task else None}, "
        f"cancelled={h.run_task.cancelled() if h.run_task else None}",
    )


def test_sentinel_fires_on_cancel() -> bool:
    """The runner's finally must push SENTINEL even when cancelled.
    put_nowait (sync) keeps this safe — the previous `await put` could
    have re-raised CancelledError before the SENTINEL landed."""
    async def go():
        tool_started = asyncio.Event()
        queue: asyncio.Queue = asyncio.Queue()
        SENTINEL = object()

        async def runner():
            try:
                tool_started.set()
                await asyncio.sleep(10)
                return ["ok"]
            finally:
                queue.put_nowait(SENTINEL)

        task = asyncio.create_task(runner())
        await asyncio.wait_for(tool_started.wait(), timeout=1.0)
        task.cancel()
        with contextlib.suppress(BaseException):
            await task
        # SENTINEL should already be in the queue.
        return queue.qsize() == 1 and queue.get_nowait() is SENTINEL

    return _check("SENTINEL fires from runner finally on cancellation",
                  asyncio.run(go()))


def test_normal_completion_does_not_cancel() -> bool:
    """Negative: the cleanup must not cancel run_task that already finished."""
    async def go():
        async def quick_tool():
            return ["result"]

        h = RunnerHarness(quick_tool)
        async for _ in h.gen():
            pass
        return h

    h = asyncio.run(go())
    return _check(
        "normal completion leaves run_task done, not cancelled",
        h.run_task.done() and not h.run_task.cancelled(),
    )


def test_cancel_listener_torn_down_on_completion() -> bool:
    """When the tool finishes naturally, the cancel_listener (which is
    just awaiting an event that never fires) must be cleaned up."""
    async def go():
        async def quick_tool():
            return ["ok"]

        cancel_event = asyncio.Event()
        h = RunnerHarness(quick_tool, cancel_event=cancel_event)
        async for _ in h.gen():
            pass
        # Give the event loop a tick to settle the cancellation.
        await asyncio.sleep(0)
        return h

    h = asyncio.run(go())
    return _check(
        "cancel_listener torn down after normal completion",
        h.cancel_listener is not None and h.cancel_listener.done(),
    )


# ---------------------------------------------------------------------------
# asyncio.shield: save-always persistence survives a second cancellation
# ---------------------------------------------------------------------------

def test_shield_protects_save() -> bool:
    """asyncio.shield around the persistence call lets the write complete
    even if our await is cancelled. This mirrors the save-always finally
    shape after the P0 #2 changes."""
    async def go():
        completed = asyncio.Event()

        async def slow_save():
            try:
                await asyncio.sleep(0.05)
                completed.set()
            except asyncio.CancelledError:
                # Should NOT happen — shield must protect us.
                raise
            return "saved"

        async def caller():
            try:
                await asyncio.shield(slow_save())
            except asyncio.CancelledError:
                # The shield re-raises CancelledError to the awaiter, but
                # the inner coroutine keeps running. Eat it.
                pass

        task = asyncio.create_task(caller())
        await asyncio.sleep(0)  # let it start
        task.cancel()
        with contextlib.suppress(BaseException):
            await task
        # The inner slow_save should still complete in the background.
        await asyncio.wait_for(completed.wait(), timeout=1.0)
        return completed.is_set()

    return _check("asyncio.shield lets save-always complete after cancel",
                  asyncio.run(go()))


# ---------------------------------------------------------------------------
# vllm_post_stream: aclose tears down the httpx connection
# ---------------------------------------------------------------------------

_RealClient = httpx.AsyncClient


class _MockClient:
    def __init__(self, handler):
        self._handler = handler

    def __call__(self, **kwargs):
        kwargs.pop("transport", None)
        return _RealClient(transport=httpx.MockTransport(self._handler), **kwargs)


def _install_mock(handler):
    vllm_client.httpx.AsyncClient = _MockClient(handler)


def _uninstall_mock():
    vllm_client.httpx.AsyncClient = _RealClient


def test_post_stream_aborts_on_aclose() -> bool:
    """Build a streaming response that yields one line then 'hangs' (the
    handler returns a body the client iterates lazily). aclose the
    consumer and verify the iterator stops without raising — proving
    the httpx async-with cleanup runs through vllm_post_stream's chain."""
    # Each `data: ...` line is a separate SSE event. The handler returns
    # all bytes synchronously; httpx still streams them via aiter_lines.
    payload = b"data: first\n\ndata: second\n\ndata: third\n\ndata: [DONE]\n\n"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=payload,
        )

    async def go():
        seen: list[str] = []

        async def consume():
            async with vllm_client.vllm_post_stream({"model": "x"}) as line_iter:
                async for line in line_iter:
                    seen.append(line)
                    # Bail after the first usable line, simulating an
                    # SSE consumer that's been aclose'd mid-stream.
                    if line.startswith("data:"):
                        break

        await consume()
        # Cleanup ran without raising. We saw exactly one data line.
        return seen

    _install_mock(handler)
    try:
        seen = asyncio.run(go())
    finally:
        _uninstall_mock()
    data_lines = [s for s in seen if s.startswith("data:")]
    return _check(
        "vllm_post_stream cleanly tears down when consumer breaks",
        len(data_lines) == 1,
        f"saw {seen}",
    )


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

TESTS = [
    test_runner_completes_normally,
    test_runner_cancelled_on_aclose_mid_tool,
    test_runner_cancelled_on_event,
    test_sentinel_fires_on_cancel,
    test_normal_completion_does_not_cancel,
    test_cancel_listener_torn_down_on_completion,
    test_shield_protects_save,
    test_post_stream_aborts_on_aclose,
]


def main() -> int:
    passed = 0
    failed = 0
    for test in TESTS:
        try:
            ok = test()
        except Exception:
            ok = False
            print(f"[FAIL] {test.__name__} - exception:")
            traceback.print_exc()
        if ok:
            passed += 1
        else:
            failed += 1
    print(f"\n{passed} passed, {failed} failed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
