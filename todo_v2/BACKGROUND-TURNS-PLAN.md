# Background turn completion ("close the tab, the answer still lands")

Status: Phases A+B IMPLEMENTED 2026-07-25 (committed, not deployed);
C/D pending. Live verification for B after VPS deploy: curl the
resume endpoint through the gateway with Accept: text/event-stream
and confirm incremental delivery (keepalives arriving every ~15s
rather than one buffered body at stream end). Defaults confirmed: MAX_BACKGROUND_PER_USER=2,
BACKGROUND_MAX_S=30min, cap cancels the new stream, Stop keeps the
save-always partial persist. Follow-up noted during A: the save-always
marker text "client disconnected before completion" now really means
"cancelled" (Stop / cap / runaway); revisit the wording alongside
Phase C's Stop rework, tests assert the current string.

## Goal

A user asks a question, closes the tab (or loses the device), and the
turn still runs to completion server-side and is persisted. When they
reopen the chat: if the turn is still running they re-attach to the
live stream; if it finished while they were away, the answer is simply
in the transcript.

## Where we start from (verified against code, 2026-07-24)

The P1 #10 architecture already decouples work from the connection:

- The turn runs as a detached task writing into a per-stream event log
  (`backend/retrieval/main.py:1479`, `_run_chat_into_log`). The SSE
  response is only a reader (`stream_registry.serve_stream`).
- Persistence is completion-time (`chat_service.py:2638`) with a
  shielded save-always `finally` for interruptions
  (`chat_service.py:2751`).
- The only thing that kills a turn after tab close is the grace timer:
  `stream_registry._grace_timer` fires `cancel_event` after
  `GRACE_S = 60` seconds without a listener
  (`stream_registry.py:149-171`).
- A full Last-Event-ID resume path exists:
  `GET /api/chat/completions/resume` (`main.py:1490`), frontend
  reconnect loop in `webui/src/lib/api.ts` (`streamChat`,
  `_attemptResume`, `resumeChat`), mount-time resume in
  `webui/src/hooks/useChatLifecycle.ts`. The resume pointer lives in
  sessionStorage (`munin.active_stream`), which dies with the tab.

Two non-obvious findings that shape the plan:

1. **Stop depends on the grace timer today.** The Stop button only
   aborts the client fetch (`chatStore.stopGenerating`); the backend
   keeps generating until grace expires. Removing the grace cancel
   without adding an explicit cancel endpoint would make Stop a no-op.
2. **The gateway buffers the resume GET.** `frontend/gateway/main.py`
   decides streaming by `stream: true` in the request BODY
   (`main.py:674-678`); the resume GET has no body, so it falls into
   the buffered branch (`main.py:736-776`) and blocks until the
   upstream stream ends, under the client's 300 s timeout. Works today
   only because resumed streams are short-lived; breaks for a
   re-attach that watches a long turn.

## Design decisions

- **The in-memory registry stays the source of truth for "turn in
  progress".** No new DB status column. The registry lives in the same
  process as the chats API, so `GET /api/chats/{id}` can consult it
  directly. A durable status column would go stale on process death,
  and on restart the turn is dead anyway (save-always persists the
  partial on SIGTERM). This avoids a schema migration entirely.
- **Grace expiry promotes to background instead of cancelling**,
  bounded by a per-user cap and a wall-clock hard cap (safety net on
  top of the existing tool-turn budget).
- **Explicit cancel becomes an endpoint**, used by Stop.
- **Reopen path is server-truth driven**: `GET /api/chats/{id}` tells
  the client about an in-flight stream; localStorage is only a hint
  for auto-navigation on app mount. If re-attach 410s (evicted or
  truncated log), the client falls back to reloading the conversation,
  where the persisted answer lives.
- Ephemeral chats stay excluded (nothing is persisted, so background
  completion is meaningless there).

## Phase A: backend, background completion (core)

All in `backend/retrieval/`.

1. `stream_registry.py`
   - New tunables: `BACKGROUND_MAX_S` (wall-clock hard cap for a
     listenerless turn, default 30 min) and
     `MAX_BACKGROUND_PER_USER` (default 2).
   - `Stream` gains `background: bool = False` and
     `started_ts: float`.
   - `_grace_timer`: on grace expiry, instead of firing
     `cancel_event`, check the per-user count of other in-flight
     background streams. Under the cap: set `background = True`, log,
     and continue supervising until `BACKGROUND_MAX_S` from
     `started_ts`, then cancel (runaway guard). At the cap: keep
     today's behavior (cancel), so an abusive pattern degrades to the
     status quo.
   - `attach_listener` / `detach_listener` become refcounted (int
     counter, not boolean events' last-writer-wins). Today a second
     tab attaching and detaching can mark the stream listenerless
     while the first tab still reads. Small, contained change; the
     grace timer keys off "count == 0".
   - `StreamRegistry.find_for_conversation(conversation_id,
     user_email)`: linear scan of `_streams` (registry holds tens of
     entries at most) returning the newest matching in-flight stream.
2. `main.py`
   - `POST /api/chat/completions/{stream_id}/cancel`: validates owner,
     fires `cancel_event`, returns 204. 404/410 for unknown streams.
   - `GET /api/chats/{conversation_id}` (`main.py:651`): add
     `active_stream: {"stream_id": ..., "done": bool, "last_seq": N}
     | null` derived from `find_for_conversation`.
   - Optional (decide at review): `GET /api/chats` list gains a
     boolean `generating` per conversation for a sidebar indicator.
3. `chat_service.py`: no changes required for the happy path. The
   `_cancelled()` early-outs (skip wrap-up at 2560, skip auto-title at
   2660) only trigger when `cancel_event` fires, which now means an
   explicit Stop or a cap/runaway cancel, exactly when skipping is
   right. Verify this reasoning in review, change nothing.
4. Tests (`backend/retrieval/tests/`)
   - Extend `test_disconnect_cleanup.py`: grace expiry no longer
     cancels under the cap; does cancel at the cap; hard cap fires.
   - New: cancel endpoint (owner, non-owner 403, unknown 410).
   - New: `find_for_conversation` and the `active_stream` field.
   - Listener refcount: two attach, one detach, timer does not start.

Ships alone with real user value: answers land even before any
frontend work, because reopening a conversation already re-fetches the
transcript, and the completed message is in it.

## Phase B: gateway, stream the resume GET

`frontend/gateway/main.py`.

1. Widen the streaming detection (`main.py:674`):
   `is_streaming = body_flag or "text/event-stream" in
   request.headers.get("accept", "")`. The webui already sends
   `Accept: text/event-stream` on resume (`api.ts:906`), so no client
   change is needed. Path-independent, so any future SSE GET benefits.
2. No timeout change: with the streaming branch, the 300 s httpx
   timeout acts per-read, and the backend's 15 s SSE keepalives defeat
   it (same reason the POST path works today).
3. The streaming branch's usage extraction (`stream_and_log`) is
   harmless for resumes; the `done` event replays with usage, and
   chat/completions paths are quota-exempt anyway. No change.

Deploys via the usual VPS rsync + compose restart.

## Phase C: frontend, reopen and re-attach

All in `frontend/webui/src/`.

1. Durable pointer: move `munin.active_stream` from sessionStorage to
   localStorage (`lib/api.ts:779-803`). Keep the same shape. It now
   survives tab close; it is only a navigation hint, server truth
   wins. Clear it on `done`/`error` as today, and additionally clear
   it when a resume 410s.
2. `loadConversation` (`stores/chatStore.ts:243`): after `fetchChat`,
   if the response carries `active_stream` with `done == false` and no
   stream is already being consumed, kick the existing resume path
   (`sendMessage('', ..., {streamId})` with no lastEventId, so the
   full log replays and the in-flight bubble reconstructs from
   events). Guard against double-attach when the mount-time resume
   already fired.
3. `useChatLifecycle.ts`: unchanged logic, now reading localStorage.
   Covers "reopen the app on the home screen" by navigating to the
   pending conversation; `loadConversation` then does the real work.
4. 410 fallback: in the reopen/resume path, a `gone` outcome triggers
   `loadConversation` (refetch) instead of the current error banner.
   The persisted message (complete or save-always partial) renders.
   Mid-stream WiFi-blip resumes keep the error banner as today.
5. Stop: `stopGenerating` (`chatStore.ts:912`) additionally calls the
   new cancel endpoint (fire-and-forget) before aborting the fetch.
6. UI affordances
   - While a reopened conversation has a live background turn: the
     normal streaming UI simply resumes (replay drives it), nothing
     new needed.
   - Optional, with the Phase A list flag: a small "generating"
     indicator on the sidebar conversation row.
   - Input stays disabled while a stream is being consumed (existing
     behavior), which also prevents double turns on a pending
     conversation.

## Phase D: contract + docs + rollout

1. `shared/docs/BACKEND-API.md`: document the cancel endpoint, the
   `active_stream` field on `GET /api/chats/{id}`, the new grace
   semantics on §4.8/4.8a (grace no longer cancels, background caps),
   and the localStorage note in the `conversation` event row.
2. `shared/docs/DECISIONS.md`: record "registry is the source of truth
   for in-progress turns, no DB status column" and why Stop needed a
   real endpoint.
3. Rollout order: backend (A) first, it is strictly safer than status
   quo; then gateway (B); then webui (C). Each independently
   deployable; A alone already delivers the core promise.

## Risks and mitigations

- **Unwatched vLLM burn**: bounded by `MAX_BACKGROUND_PER_USER`,
  `BACKGROUND_MAX_S`, and the existing tool-turn budget. Stop becomes
  more immediate than today (endpoint vs 60 s grace), a strict
  improvement.
- **Log truncation on long background turns** (`MAX_LOG_EVENTS =
  1000`): re-attach 410s, fallback reload shows the persisted result.
  Acceptable; do not grow the buffer.
- **Two tabs on one conversation**: refcounted listeners make
  concurrent readers safe (both receive events; the log replay model
  already supports multiple readers).
- **Service restart**: unchanged from today (SIGTERM cancels tasks,
  save-always shields the partial write).

## Open questions (defaults applied unless overridden)

1. `MAX_BACKGROUND_PER_USER = 2`, `BACKGROUND_MAX_S = 30 min`. Sane?
2. Sidebar "generating" indicator: in scope (small) or defer?
3. At the per-user cap, cancel the NEW stream's background promotion
   (default, simple) or evict the oldest background stream instead?
4. Should explicit Stop also skip the save-always partial persist?
   Default: no, keep persisting the partial with the marker.

## Estimate

- Phase A: ~1 day incl. tests.
- Phase B: ~1 hour.
- Phase C: ~1 day incl. manual test pass (close-tab matrix: reopen
  while running, reopen after done, reopen after eviction, Stop).
- Phase D: ~1-2 hours.
