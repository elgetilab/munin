"""
SSE stream registry for ``Last-Event-ID`` reconnect (P1 #10).

The HTTP listener is decoupled from the work. ``stream_chat_completion``
runs against a per-request ``Stream`` object that owns:

- a monotonic per-stream sequence and a bounded event log,
- a ``listener_attached`` / ``listener_detached`` pair so multiple
  successive HTTP responses (the original POST and any GET resume)
  can attach/detach the same underlying generator without restarting it,
- a grace timer that supervises listenerless streams. After ``GRACE_S``
  seconds without a listener the stream is promoted to *background*
  (the turn keeps running to completion and persists normally, so a
  closed tab still gets its answer) rather than cancelled. Promotion
  is bounded: at most ``MAX_BACKGROUND_PER_USER`` concurrent background
  streams per user (beyond that, grace expiry cancels as it used to),
  and a listenerless stream is hard-cancelled ``BACKGROUND_MAX_S``
  after it started as a runaway guard. Explicit cancellation is now an
  endpoint (``POST /api/chat/completions/{stream_id}/cancel``), used
  by the frontend Stop button.

A reconnect (``GET /api/chat/completions/resume``) looks up the stream
by id, validates ``Last-Event-ID`` and the request user against the
stream's owner, replays log entries with ``seq > last_seq``, then
attaches as a listener and streams future events live. Listeners are
refcounted, so two tabs can watch the same stream; the grace timer
only engages when the count drops to zero.

Tunables:

- ``GRACE_S``                 grace window after disconnect before the
                              stream is promoted to background (or
                              cancelled, if the user is at the cap).
- ``BACKGROUND_MAX_S``        wall-clock cap for a listenerless stream,
                              measured from stream creation. Backstop on
                              top of the tool-turn budget.
- ``MAX_BACKGROUND_PER_USER`` concurrent background streams allowed per
                              user before grace expiry falls back to
                              cancelling.
- ``KEEPALIVE_S``             SSE comment cadence when idle, so reverse
                              proxies don't drop the connection.
- ``MAX_LOG_EVENTS``          per-stream replay buffer cap in EVENTS.
- ``MAX_LOG_BYTES``           the same buffer capped in BYTES, since one
                              tool result can outweigh a thousand tokens.
                              On overflow of either the stream is marked
                              ``truncated``; a resume is then served only
                              if the client's checkpoint is still inside
                              the retained window (``can_resume_from``).
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
BACKGROUND_MAX_S = 1800.0
MAX_BACKGROUND_PER_USER = 2
KEEPALIVE_S = 15.0
# Replay buffer bounds. BOTH are enforced; whichever binds first wins.
#
# 1000 events was far too small to be a reconnect buffer at all. Measured
# 2026-09-02: a single ~500-word answer emits ~2,600 SSE events, so the buffer
# could not hold even one turn, and a client that dropped for ~20-30s came back
# to find its checkpoint rolled past. can_resume_from() then correctly refused
# with 410 rather than silently skipping content, which is honest but meant
# resume could not work on exactly the long tool-heavy turns people most want
# back. Three users hit this in six minutes before it was raised.
#
# The byte cap exists because raising the count alone is the bug from 96921b7
# (chunk upserts sized by paper count rather than request bytes): most events
# are a few hundred bytes of token JSON, but a tool result or an evidence
# passage can be orders of magnitude larger, so a pure count bound says nothing
# about memory. ~8 MB per live stream is the real ceiling; at
# MAX_BACKGROUND_PER_USER=2 that is bounded per user too.
MAX_LOG_EVENTS = 20000
MAX_LOG_BYTES = 8 * 1024 * 1024
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

        # P0 #2 cancellation signal. Fired by the explicit cancel
        # endpoint (Stop button), by grace expiry when the user is at
        # the background cap, or by the BACKGROUND_MAX_S runaway guard.
        self.cancel_event: asyncio.Event = asyncio.Event()

        # Listeners are refcounted so concurrent readers (two tabs on
        # one conversation) don't corrupt the attached/detached state:
        # the events below track the count's zero/nonzero transitions
        # only. The count starts at 0 (serve_stream attaches the first
        # listener) but the events start in the "attached" state so the
        # grace timer doesn't begin a grace window before the creating
        # POST has wired up its listener.
        self._listener_count: int = 0
        self._listener_attached: asyncio.Event = asyncio.Event()
        self._listener_attached.set()
        self._listener_detached: asyncio.Event = asyncio.Event()

        # True while the stream is running with no listener past the
        # grace window (the closed-tab case). Cleared on reattach.
        # Counts against MAX_BACKGROUND_PER_USER.
        self.background: bool = False
        self.started_ts: float = time.monotonic()

        # Why cancel_event fired, set just before .set() at each cancel
        # site (Stop endpoint, background cap, runaway guard). The
        # save-always marker in chat_service reads this so a stopped
        # turn's transcript says "stopped by user" instead of blaming a
        # client disconnect. None when the event never fired or for a
        # plain aclose/shutdown.
        self.cancel_reason: Optional[str] = None

        self.done: bool = False
        self.truncated: bool = False
        # Running size of event_log, kept incrementally so record() stays O(1).
        self._log_bytes: int = 0
        self.completed_ts: Optional[float] = None
        self.last_disconnect_ts: Optional[float] = None

        # Owning tasks; main.py fills these in after construction.
        self.runner_task: Optional[asyncio.Task] = None
        self.grace_task: Optional[asyncio.Task] = None

    # -- log API -----------------------------------------------------------

    def oldest_retained_seq(self) -> int:
        """Lowest seq still in the replay buffer, or 0 when it is empty.

        This is what decides whether a reconnect is servable. `truncated`
        only says the buffer overflowed AT SOME POINT in the stream's life;
        it says nothing about whether THIS client's checkpoint was lost.
        A long turn overflows early and stays flagged forever, so gating on
        the flag refuses reconnects the buffer could still serve perfectly.
        """
        return self.event_log[0][0] if self.event_log else 0

    def can_resume_from(self, after_seq: int) -> bool:
        """True when everything strictly after `after_seq` is still retained.

        `after_seq == 0` means "replay from the beginning", which is only
        honest if nothing has been dropped.
        """
        if not self.truncated:
            return True
        if after_seq <= 0:
            return False
        # The client has event `after_seq`; it needs after_seq+1 onward.
        return after_seq + 1 >= self.oldest_retained_seq()

    def record(self, event_name: str, data_json: str) -> int:
        """Append an event with a fresh seq. Caps the log; on overflow
        marks the stream truncated."""
        seq = self._next_seq
        self._next_seq += 1
        self.event_log.append((seq, event_name, data_json))
        self._log_bytes += len(data_json) + len(event_name)
        # Drop oldest until BOTH bounds hold. A reconnect with Last-Event-ID
        # below the new head can no longer be served accurately, so flip the
        # flag; can_resume_from() then decides per client whether that
        # particular checkpoint survived, rather than refusing everyone.
        while self.event_log and (
            len(self.event_log) > MAX_LOG_EVENTS
            or self._log_bytes > MAX_LOG_BYTES
        ):
            _seq, _name, _data = self.event_log.pop(0)
            self._log_bytes -= len(_data) + len(_name)
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

    @property
    def last_seq(self) -> int:
        """Seq of the most recently recorded event (0 if none yet)."""
        return self._next_seq - 1

    # -- listener API ------------------------------------------------------

    def attach_listener(self) -> None:
        self._listener_count += 1
        self._listener_detached.clear()
        self._listener_attached.set()
        self.last_disconnect_ts = None
        # A watched stream is not background; freeing the cap slot on
        # reattach keeps the per-user count honest.
        self.background = False

    def detach_listener(self) -> None:
        self._listener_count = max(0, self._listener_count - 1)
        if self._listener_count == 0:
            self._listener_attached.clear()
            self._listener_detached.set()
            self.last_disconnect_ts = time.monotonic()

    @property
    def listener_attached(self) -> bool:
        return self._listener_count > 0


async def _grace_timer(stream: Stream) -> None:
    """Per-stream supervisor: wait for the last listener to detach, then
    give GRACE_S for a reconnect. If grace expires with no reattach, the
    stream is promoted to background and keeps running (the closed-tab
    case: the turn completes and persists, the answer is there on
    reopen). Grace expiry only cancels when the user is already at
    MAX_BACKGROUND_PER_USER, and a listenerless stream is cancelled
    outright once it is older than BACKGROUND_MAX_S — the P0 #2
    cancellation cascade in chat_service runs from ``cancel_event``
    unchanged in both fallback paths."""
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
                pass

            # Grace expired with no listener. Promote to background if
            # the user has cap room, else fall back to cancelling.
            others = registry.count_background(
                stream.user_email, exclude_stream_id=stream.stream_id
            )
            if others >= MAX_BACKGROUND_PER_USER:
                logger.info(
                    "stream %s grace expired (%ds) at background cap "
                    "(%d); cancelling",
                    stream.stream_id, int(GRACE_S), others,
                )
                stream.cancel_reason = (
                    "cancelled: too many background turns running"
                )
                stream.cancel_event.set()
                return

            stream.background = True
            logger.info(
                "stream %s grace expired (%ds); promoted to background "
                "(user has %d other background stream(s))",
                stream.stream_id, int(GRACE_S), others,
            )

            # Supervise the background stream until a listener reattaches
            # (attach_listener clears ``background``; loop back to
            # waiting for the next detach) or the wall-clock runaway
            # guard fires.
            remaining = BACKGROUND_MAX_S - (
                time.monotonic() - stream.started_ts
            )
            if remaining > 0:
                try:
                    await asyncio.wait_for(
                        stream._listener_attached.wait(), timeout=remaining
                    )
                    continue
                except asyncio.TimeoutError:
                    pass
            if stream.done:
                return
            logger.warning(
                "stream %s exceeded BACKGROUND_MAX_S (%ds) with no "
                "listener; cancelling",
                stream.stream_id, int(BACKGROUND_MAX_S),
            )
            stream.cancel_reason = (
                "cancelled: background time limit reached"
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

    def count_background(
        self, user_email: str, exclude_stream_id: Optional[str] = None
    ) -> int:
        """In-flight background streams owned by ``user_email``. Linear
        scan; the registry holds at most tens of entries."""
        return sum(
            1
            for s in self._streams.values()
            if s.user_email == user_email
            and not s.done
            and s.background
            and s.stream_id != exclude_stream_id
        )

    def in_flight_conversation_ids(self, user_email: str) -> set:
        """Conversation ids with a live (not done) stream owned by
        ``user_email``. Feeds the sidebar's "generating" flag."""
        return {
            s.conversation_id
            for s in self._streams.values()
            if not s.done
            and s.user_email == user_email
            and s.conversation_id
        }

    def find_for_conversation(
        self, conversation_id: str, user_email: str
    ) -> Optional[Stream]:
        """Newest stream for a conversation, owned by ``user_email``.
        Includes recently-completed streams still under DONE_RETENTION_S
        (callers read ``stream.done`` to tell the cases apart); returns
        None once the janitor has evicted everything for the id."""
        candidates = [
            s
            for s in self._streams.values()
            if s.conversation_id == conversation_id
            and s.user_email == user_email
        ]
        if not candidates:
            return None
        return max(candidates, key=lambda s: s.started_ts)

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
