# Known Bugs — backend (cluster)

Running list of confirmed bugs that are not yet fixed. Each entry
should carry enough detail (symptom, root cause, file references) to
pick up and fix without a rediscovery pass. Remove an entry when the
fix lands; reference it from the commit.

---

## 1. Deleting a conversation orphans its `proposed_memories`

**Severity:** medium (data retention / privacy)

### Symptom

After a user deletes a conversation, the model-proposed memories that
were extracted from that conversation remain in `chats.db`. They are
no longer reachable from any conversation (the parent row is gone) but
still hold the extracted content, which can include personal details
(research topics, identity hints, local filesystem paths). A user who
deletes a chat reasonably expects its derived data to go with it; for
a privacy-conscious user who clears their history, these rows are
exactly the data they meant to remove.

### Root cause

`delete_conversation()` in `backend/retrieval/chat_store.py` (around
line 663) removes rows explicitly rather than relying on cascade:

- `DELETE FROM messages WHERE conversation_id = ?`
- `DELETE FROM conversations WHERE id = ? AND user_email = ?`

`artifacts` are cleaned up implicitly because that table has an
`ON DELETE CASCADE` foreign key to `conversations` and the connection
runs with `PRAGMA foreign_keys=ON`.

`proposed_memories` (created in `chat_store.py` around line 177) has a
plain `conversation_id TEXT` column with **no foreign key**, so the
cascade never applies, and `delete_conversation()` never deletes from
it explicitly. The rows are therefore left behind as orphans.

### Suggested fix

Add an explicit delete inside `delete_conversation()`, alongside the
existing `messages` delete:

```python
await db.execute(
    "DELETE FROM proposed_memories WHERE conversation_id = ?",
    (conversation_id,),
)
```

Follow-ups to consider while in here:

- **Whole-account deletion sweep.** If/when a user-account delete path
  exists (or as a maintenance task), purge `proposed_memories`,
  `rejected_memory_keys`, `user_profiles`, `user_memory`, `projects`,
  and `artifacts` by `user_email`, not just per-conversation rows.
- **One-off cleanup of existing orphans.** Rows already orphaned by
  past deletes will not be reached by the code fix. A short migration
  can drop `proposed_memories` whose `conversation_id` no longer
  matches any `conversations.id`:
  ```sql
  DELETE FROM proposed_memories
  WHERE conversation_id IS NOT NULL
    AND conversation_id NOT IN (SELECT id FROM conversations);
  ```
- Decide whether `proposed_memories.conversation_id` should gain a
  real `ON DELETE CASCADE` FK for defence in depth. Note the existing
  comment in `delete_conversation()` about not relying on SQLite
  cascades for trigger ordering, so keep the explicit delete even if a
  FK is added.

---

## 2. SSE stream-resume endpoint fails for users in production

**Severity:** high (core UX / streaming reliability)

**Status (2026-06-10):** the `500` mode is ROOT-CAUSED and fixed in the
working tree (see below); pending deploy + commit. The `410` retention-
window concerns remain open.

### Symptom

`GET /api/chat/completions/resume` (the `Last-Event-ID` reconnect path,
P1 #10) does not recover dropped streams in practice. In the production
gateway usage log over an observed multi-day window, **every** resume
call failed: a mix of `410 Gone` and `500` responses, with no `200`
successes recorded. When a chat stream drops (WiFi blip, reverse-proxy
timeout, browser refresh, tab backgrounded), the client's reconnect
attempt fails and the in-flight answer is lost. The visible result to
users is a truncated reply or a client-side "Error in input stream" /
"got stuck after a few lines" message. Some users report it; an unknown
number simply absorb the broken turns silently, so the report volume
understates the real frequency.

### Two distinct failure modes

1. **`410 Gone`** — `api_chat_completions_resume()`
   (`backend/retrieval/main.py` around line 1360) returns 410 when the
   `Stream` is missing or `truncated`. This is "working as designed,"
   but the design windows may be far too short for real reconnect
   patterns. See the tunables in `backend/retrieval/stream_registry.py`:
   - `DONE_RETENTION_S = 60.0` — a completed stream is only resumable
     for 60s before the janitor evicts it. A user who refreshes or
     returns to a tab after a minute gets a legitimate-but-useless 410.
   - `GRACE_S = 60.0` — only 60s of disconnect grace before the turn is
     cancelled.
   - `MAX_LOG_EVENTS = 1000` — buffer overflow flips `truncated`, after
     which resume 410s even within the time window (long tool-heavy
     turns can exceed 1000 events).

2. **`500`** — **ROOT-CAUSED + FIXED (2026-06-10).** `NameError: name
   'EventSourceResponse' is not defined`. The symbol was imported only
   *locally inside the POST handler* (`api_chat_completions`), not at
   module scope, so the separate `api_chat_completions_resume()` handler
   raised on every success path (the `return EventSourceResponse(...)`
   at the end). This is why the POST stream worked in production while
   *every* successful resume 500'd, and why no resume call ever returned
   200. Fix: hoist `from sse_starlette.sse import EventSourceResponse`
   to the module-level imports in `main.py` (and drop the now-redundant
   local import). Found by the new integration test on its first run.

### Why the old tests didn't catch it

`backend/retrieval/tests/test_stream_registry.py` passed, but it only
unit-tests the in-process `Stream` / `registry` primitives (monotonic
seq, replay filtering, buffer-cap → `truncated`, grace timer fire /
no-fire on reattach, `parse_last_event_id`, janitor eviction) and never
touched the HTTP endpoint where the `NameError` lived. So green unit
tests meant "the building blocks work," not "resume works for users."

**Now covered:** `backend/retrieval/tests/test_resume_endpoint.py`
drives the real endpoint via `httpx.ASGITransport`, seeding the registry
directly. Its success-path cases (completed replay / no-Last-Event-ID /
in-flight live continuation) only pass if the endpoint actually builds
its `EventSourceResponse`, so they would have caught this immediately
(and did, on first run). The 410/403/401 branch cases lock the
gone/forbidden/unauthenticated behaviour.

So green unit tests here mean "the building blocks work in isolation,"
not "resume works for users." Confirmed via git history: as of writing,
`stream_registry.py` has only its original feature commit (`410c9a5`),
i.e. no later fix has landed, so the production failures run against the
current code.

### Frontend: no fix needed (verified contract-correct 2026-06-10)

The webui resume client was checked end-to-end and needs **no change**
for the 500 fix; it was only ever blocked by the server. For a future
reader, so this does not get re-investigated:

- The SSE contract matches on both sides. Backend emits the `conversation`
  event carrying `stream_id` / `id` / `ephemeral`
  (`chat_service.py` ~1638) and tags every event `id: <stream_id>-<seq>`.
  The client captures `stream_id` and tracks `lastEventId` from those
  `id:` lines (`frontend/webui/src/lib/api.ts` ~752, ~763), then resumes
  via `GET /chat/completions/resume?stream_id=...` with the
  `Last-Event-ID` header (~820). It already has a backoff reconnect loop
  (`RECONNECT_BACKOFF_MS`) and a 410 -> "no longer available" path.
- The client already degraded gracefully on the production 500
  (`if (!res.ok) return 'error'` -> "Stream resume failed; please retry"
  banner), which is the failure users were seeing. With the server fixed
  it now falls through to consume the resumed stream. **No webui rebuild
  / redeploy is required** for this fix.
- Caveat (genuine UX limitation, NOT caused by this bug): on a browser
  *refresh*, in-memory partial text is lost and the server replays only
  events newer than the persisted `lastEventId`, so the bubble repaints
  only the tail of the message, not the whole thing. Mid-stream blips
  (no refresh) are seamless because the rendered text stays in memory.
  Folded into the client-UX follow-up below.

### Suggested fix / next steps

- [DONE] Re-investigate the **500**, get a real traceback -> it was a
  `NameError` (see failure mode 2 above), now fixed.
- [DONE] Add an **integration test** for the end-to-end success path
  plus the 410/403/401 branches: `tests/test_resume_endpoint.py`.
- [OPEN] Reconsider the retention/grace **windows** — `DONE_RETENTION_S`
  and `GRACE_S` at 60s are plausibly too short for real refresh/return
  patterns; weigh longer windows against registry memory growth.
- [OPEN] Consider raising or removing the `MAX_LOG_EVENTS = 1000` cap for
  long tool-heavy turns, or make truncation degrade gracefully (replay
  from the oldest retained event) instead of hard-410.
- [OPEN] **Client-side UX:** on a browser refresh, repaint/preserve the
  partial assistant text rather than showing only the replayed tail
  (the caveat above). The raw-error vs graceful-retry handling is already
  in place; this is specifically about the cross-refresh repaint.
- [TODO] **Manual verification:** resume has never succeeded in prod, so
  the client success branch has never run against a live server. Do one
  manual round-trip (start a long answer, drop WiFi a few seconds, and
  separately refresh the tab) to confirm the full loop now works.

---

## 3. Persona switch dropped identity + faked memory loss (FIXED 2026-06-10)

**Severity:** medium (core UX / trust)

### Symptom

Switching persona mid-conversation (via `delegate_to_persona`, or a
manual UI persona switch) made the receiving persona deny the switch and
claim it had lost the conversation. Reported chat `14ded1f1`
(2026-06-09): user starts with Meitner (chat), asks to switch to code;
Turing takes over but then insists *"I've been Turing the whole time"*
and *"I don't have access to previous conversations... fresh session."*

### Root cause

The conversation history was in context the whole time, so this was not
literal memory loss. Three gaps combined:

1. No handoff signal reached the model. The `delegated` / `persona_changed`
   SSE events went to the UI only; nothing in the model's context said a
   switch had occurred.
2. No per-message persona attribution. The `messages` table had no
   `persona` column and `conversation.persona` was overwritten to the
   new persona, erasing the switch boundary. The receiving persona read
   the source persona's earlier turns as its own.
3. The "no memory of previous conversations" line was a pure
   confabulation (it exists nowhere in the prompts) the system prompt
   did nothing to prevent.

### Fix

- `messages` gains a nullable `persona` column (additive migration;
  legacy rows stay NULL). Every message records its authoring persona,
  so a delegating turn stores the user turn under the source persona and
  the assistant turn under the target.
- `personas.persona_handoff_note()` builds a marker, injected two ways:
  `assemble_context()` prepends it when stored history spans personas
  (active persona passed explicitly, covering manual switches too), and
  the `delegate_to_persona` hot-path appends a "you just took over from
  {source}" note to the receiving persona's system prompt. It returns
  None for single-persona / legacy-NULL history, so old chats are
  unaffected.
- Base ambient prompt line clarifies the model can read the full current
  conversation (kills the "no memory" confabulation).
- `delegate_to_persona` guidance made more eager: hand off sustained /
  non-trivial work to its specialist and hand back when the conversation
  returns to the original persona's strength, rather than delegating only
  as a last resort. 1-hop-per-turn budget unchanged.
- Tests: `tests/test_persona_handoff.py` (9 cases) — note logic,
  assemble_context injection (positive + same-persona + legacy-NULL
  negatives), persona save/load round-trip.

### Open follow-up

- [OPEN] **Frontend persona divider.** The per-message `persona` field is
  now returned by the conversation API; the webui should render a visible
  "switched to {persona}" boundary in the message list. Deliberately
  deferred from the backend change.
- [TODO] **Manual verification.** Redo the Meitner -> Turing switch in the
  live UI and confirm the receiving persona acknowledges the handoff
  instead of denying it (unit tests prove the marker is built/injected,
  not that the model obeys it).
