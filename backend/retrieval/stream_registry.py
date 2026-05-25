"""
SSE stream registry for ``Last-Event-ID`` reconnect (P1 #10).

The HTTP listener is decoupled from the work. ``stream_chat_completion``
runs against a per-request ``Stream`` object that owns:

- a monotonic per-stream sequence and a bounded event log,
- a ``listener_attached`` / ``listener_detached`` pair so multiple
  successive HTTP responses (the original POST and any GET resume)
  can attach/detach the same underlying generator without restarting it,
- a grace timer that fires ``cancel_event`` only after the listener has
  been absent for ``GRACE_S`` seconds, replacing P0 #2's immediate cancel
  on disconnect — so a brief WiFi drop or browser refresh recovers
  instead of losing the turn.

A reconnect (``GET /api/chat/completions/resume``) looks up the stream
by id, validates ``Last-Event-ID`` and the request user against the
stream's owner, replays log entries with ``seq > last_seq``, then
attaches as the new listener and streams future events live.

Tunables:

- ``GRACE_S``                 grace window before cancel after disconnect.
- ``KEEPALIVE_S``             SSE comment cadence when idle, so reverse
                              proxies don't drop the connection.
- ``MAX_LOG_EVENTS``          per-stream replay buffer cap; on overflow
                              the stream is marked ``truncated`` and
                              subsequent resumes get 410.
- ``DONE_RETENTION_S``        how long a completed stream stays in the
                              registry for late reconnects.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from typing import AsyncIterator, Optional

logger = logging.getLogger(__name__)

GRACE_S = 60.0
KEEPALIVE_S = 15.0
MAX_LOG_EVENTS = 1000
DONE_RETENTION_S = 60.0
JANITOR_INTERVAL_S = 10.0


class Stream:
    """A single chat-completion stream's identity, log, and grace state."""

    def __init__(self, user_email: str, conversation_id: Optional[str]) -> None:
        self.stream_id: str = uuid.uuid4().hex
        self.user_email: str = user_email or ""
        self.conversation_id: Optional[str] = conversation_id

        # (seq, event_name, data_json) tuples, oldest first. ``seq``
        # starts at 1 and is strictly monotonic per stream.
        self.event_log: list[tuple[int, str, str]] = []
        self._next_seq: int = 1

        # Set every time a new event lands OR done flips. Consumers
        # await this, then re-scan the log; one Event is enough (extra
        # sets while already-set are no-ops, no events get missed
        # because consumers re-read the log after waking).
        self._wake: asyncio.Event = asyncio.Event()

        # P0 #2 cancellation signal. Now fired by the grace timer
        # instead of immediately by the disconnect watchdog.
        self.cancel_event: asyncio.Event = asyncio.Event()

        # Initial state is attached: the POST request that created this
        # stream is presumed to be wiring up the first listener
        # immediately.
        self._listener_attached: asyncio.Event = asyncio.Event()
        self._listener_attached.set()
        self._listener_detached: asyncio.Event = asyncio.Event()

        self.done: bool = False
        self.truncated: bool = False
        self.completed_ts: Optional[float] = None
        self.last_disconnect_ts: Optional[float] = None

        # Owning tasks; main.py fills these in after construction.
        self.runner_task: Optional[asyncio.Task] = None
        self.grace_task: Optional[asyncio.Task] = None

    # -- log API -----------------------------------------------------------

    def record(self, event_name: str, data_json: str) -> int:
        """Append an event with a fresh seq. Caps the log; on overflow
        marks the stream truncated."""
        seq = self._next_seq
        self._next_seq += 1
        self.event_log.append((seq, event_name, data_json))
        if len(self.event_log) > MAX_LOG_EVENTS:
            # Drop oldest. A reconnect with Last-Event-ID below the new
            # head can no longer be served accurately — flip the flag
            # so the resume endpoint returns 410 rather than silently
            # skipping events.
            self.event_log.pop(0)
            self.truncated = True
        self._wake.set()
        return seq

    def replay_after(self, last_seq: int) -> list[tuple[int, str, str]]:
        """Entries strictly newer than ``last_seq``. Caller is responsible
        for checking ``truncated`` first."""
        return [e for e in self.event_log if e[0] > last_seq]

    def has_unread(self, last_seq: int) -> bool:
        if not self.event_log:
            return False
        return self.event_log[-1][0] > last_seq

    def mark_done(self) -> None:
        """The runner finished (success or cancel). Signal listeners
        and start the retention clock for the janitor."""
        self.done = True
        self.completed_ts = time.monotonic()
        self._wake.set()

    async def wait_for_new(self, last_seq: int) -> None:
        """Block until there's something to read or ``done`` flips.
        Returns when either condition is met."""
        if self.has_unread(last_seq) or self.done:
            return
        self._wake.clear()
        await self._wake.wait()

    # -- listener API ------------------------------------------------------

    def attach_listener(self) -> None:
        self._listener_detached.clear()
        self._listener_attached.set()
        self.last_disconnect_ts = None

    def detach_listener(self) -> None:
        self._listener_attached.clear()
        self._listener_detached.set()
        self.last_disconnect_ts = time.monotonic()

    @property
    def listener_attached(self) -> bool:
        return self._listener_attached.is_set()


async def _grace_timer(stream: Stream) -> None:
    """Per-stream supervisor: wait for the listener to detach, then give
    GRACE_S for a reconnect. If grace expires with no reattach, fire
    ``cancel_event`` — the P0 #2 cancellation cascade in chat_service
    runs from there unchanged."""
    try:
        while not stream.done:
            await stream._listener_detached.wait()
            if stream.done:
                return
            try:
                await asyncio.wait_for(
                    stream._listener_attached.wait(), timeout=GRACE_S
                )
                # Reattached in time; loop to wait for the next detach.
                continue
            except asyncio.TimeoutError:
                logger.info(
                    "stream %s grace expired (%ds); cancelling",
                    stream.stream_id, int(GRACE_S),
                )
                stream.cancel_event.set()
                return
    except asyncio.CancelledError:
        # Janitor or shutdown cancelled us. Don't propagate.
        return


class StreamRegistry:
    """Process-wide table of in-flight + recently-completed streams."""

    def __init__(self) -> None:
        self._streams: dict[str, Stream] = {}
        self._janitor: Optional[asyncio.Task] = None

    def register(self, stream: Stream) -> None:
        self._streams[stream.stream_id] = stream

    def get(self, stream_id: str) -> Optional[Stream]:
        return self._streams.get(stream_id)

    def start_janitor(self) -> None:
        """Spawn the background cleanup task. Idempotent."""
        if self._janitor is None or self._janitor.done():
            self._janitor = asyncio.create_task(self._run_janitor())

    async def _run_janitor(self) -> None:
        while True:
            try:
                await asyncio.sleep(JANITOR_INTERVAL_S)
                now = time.monotonic()
                expired: list[str] = []
                for sid, s in self._streams.items():
                    if (
                        s.done
                        and s.completed_ts is not None
                        and now - s.completed_ts > DONE_RETENTION_S
                    ):
                        expired.append(sid)
                for sid in expired:
                    s = self._streams.pop(sid, None)
                    if s and s.grace_task and not s.grace_task.done():
                        s.grace_task.cancel()
                if expired:
                    logger.info(
                        "stream registry: evicted %d completed stream(s)",
                        len(expired),
                    )
            except asyncio.CancelledError:
                return
            except Exception:
                logger.exception("stream registry janitor loop error")


# Process-wide singleton. main.py reads/writes through this.
registry = StreamRegistry()


# ---------------------------------------------------------------------------
# SSE response helpers
# ---------------------------------------------------------------------------

def _sse_event_dict(seq: int, stream_id: str, event_name: str, data_json: str) -> dict:
    """Shape an event for ``sse_starlette.EventSourceResponse``.
    ``id`` follows ``<stream_id>-<seq>`` so Last-Event-ID is self-contained."""
    return {
        "id": f"{stream_id}-{seq}",
        "event": event_name,
        "data": data_json,
    }


async def serve_stream(
    stream: Stream, after_seq: int = 0
) -> AsyncIterator[dict]:
    """Async generator suitable for ``EventSourceResponse(...)`` body.

    Replays everything with ``seq > after_seq``, then waits for new
    events / done. Wire-level keepalives are emitted by sse-starlette's
    own ``ping`` mechanism — main.py constructs the response with
    ``ping=KEEPALIVE_S`` so this generator does not have to wake up
    periodically just to defeat reverse-proxy idle timeouts.

    Attaches/detaches as the listener around the generator's lifetime,
    which is what the grace timer keys off."""
    stream.attach_listener()
    last_seq = after_seq
    try:
        # 1. Replay anything already buffered (typical resume path; for
        # a fresh POST this is a no-op since after_seq=0 and the log is
        # filling concurrently with this loop's first iteration).
        for seq, name, data in stream.replay_after(last_seq):
            yield _sse_event_dict(seq, stream.stream_id, name, data)
            last_seq = seq

        # 2. Stream new events as they arrive.
        while True:
            if stream.done and not stream.has_unread(last_seq):
                return
            await stream.wait_for_new(last_seq)
            for seq, name, data in stream.replay_after(last_seq):
                yield _sse_event_dict(seq, stream.stream_id, name, data)
                last_seq = seq
    finally:
        stream.detach_listener()


def parse_last_event_id(header_value: Optional[str]) -> Optional[tuple[str, int]]:
    """Parse a ``Last-Event-ID: <stream_id>-<seq>`` header value into its
    parts, or None if absent / malformed."""
    if not header_value:
        return None
    raw = header_value.strip()
    if not raw:
        return None
    # stream_id is a hex uuid (no dashes in our encoding) — splitting on
    # the LAST dash is safe.
    sep = raw.rfind("-")
    if sep <= 0:
        return None
    sid = raw[:sep]
    try:
        seq = int(raw[sep + 1:])
    except ValueError:
        return None
    return sid, seq
