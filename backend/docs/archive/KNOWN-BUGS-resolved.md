# Known Bugs — RESOLVED (backend, cluster)

Frozen record of bugs 1 to 5 from `../KNOWN-BUGS.md`, moved here once
fixed. Kept rather than deleted because the diagnostic history is the
useful part: several of these entries were WRONG by the time anyone
re-read them, and that pattern is worth being able to search for.

Do NOT treat anything here as current. The live list is
[`../KNOWN-BUGS.md`](../KNOWN-BUGS.md).

| # | Bug | Resolved | Why it is worth re-reading |
|---|---|---|---|
| 1 | Deleting a conversation orphaned its `proposed_memories` | 2026-09-02 | The rows were not inert residue: they still rendered as pills and consumed the 10-slot budget. One user sat at 10/10 from deleted chats. |
| 2 | SSE stream-resume failed for every user | 2026-09-02 (closed, reopened, closed) | Three of its four factual claims were stale when re-checked. Closed on a verified round trip, then reopened hours later by a user: the check had covered a short turn and a 3s reconnect, while the buffer could not hold one real turn. Both bounds were then MEASURED rather than guessed, and the measurement inverted the assumption twice. |
| 3 | Persona switch dropped identity, faked memory loss | 2026-06-10 | The "I have no memory of previous conversations" line was pure confabulation, present in no prompt. |
| 4 | NVIDIA module drifted behind the kernel, GPUs vanished on reboot | 2026-08-31 | The guard that would have prevented it already existed in HuginSLURM and had simply never been deployed. |
| 5 | `slurmd` died permanently on a missing `/dev/nvidia0` | 2026-08-31 | No `Restart=` in the packaged unit turned a recoverable driver problem into a six-hour outage nobody was told about. |
| 6 | `build_chunk_index.py` could not use both GPUs | 2026-09-02 | The plan recorded in the entry was itself unsafe: position striding across processes silently loses papers. Hashing was implemented instead, and running two shards for the first time found a create-collection race no unit test could reach. |
| 7 | Host-only tests crashed instead of skipping | 2026-09-03 | One of the three was not host-only at all: it would have PASSED in the deployed container and had been crashing at import for months on a path that walks off the top of the filesystem. |

---


## 1. Deleting a conversation orphans its `proposed_memories` (FIXED 2026-09-02)

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

### Impact was worse than first described

Measured on the production database 2026-09-02, before the fix. The
orphans are not inert residue: `list_pending()` is USER-scoped, so a
proposal whose conversation is gone keeps rendering as an accept/reject
pill AND keeps occupying the `MAX_PENDING = 10` FIFO budget.

```
proposed_memories: 149 rows, 11 orphaned across 2 users
  user A: pending=10  orphaned=10   <- at cap, every slot from a deleted chat
  user B: pending= 1  orphaned= 1
```

So one user's entire memory-proposal feature was backed by conversations
they had deleted, and shown back to them.

Scope is exactly this one table. Every other conversation-linked table
was checked in production and had ZERO orphans: `messages` (explicit
delete), `artifacts` and `conversation_plans` (FK cascade), and
`artifact_versions` (cascade via `artifacts`).

### Fix

Explicit delete in `delete_conversation()`, scoped by `user_email` to
match the `conversations` delete beside it. **Not** an FK cascade: the
existing comment in that function explains the house position, that
SQLite's cascades do not fire per-row triggers on the child table
consistently across versions, so the explicit delete is the pattern
here.

Rows orphaned before the fix are cleaned by a one-off statement in the
`init_db()` migration block, idempotent and a no-op after the first
run. It lives at startup rather than in a maintenance script because
every deployment carries the same latent rows and nobody runs a script
on a cluster they did not personally debug. It deliberately matches
only `conversation_id IS NOT NULL`, since a NULL is not an orphan but a
proposal never tied to a conversation, and purging those would discard
live pending memories.

Rehearsed against a copy of the production database before deploying:
148 -> 137 proposed_memories, orphaned 11 -> 0, with `conversations`,
`messages`, `artifacts` and `user_memory` counts unchanged.

Tests in `tests/test_memory_proposals.py`: one that a deleted
conversation takes its proposals with it while a second conversation's
proposal and an unattached (NULL) proposal both survive, and one that
the delete is scoped to the owning user. The first fails without the
fix; the second guards the opposite direction, over-deletion.

### Follow-up

- [MOVED] **Whole-account deletion sweep.** There is no user-account
  delete path at all today, so this is a missing capability rather than
  a bug in this one. Written up in `docs/future_features.md`.
- [DONE] One-off cleanup of existing orphans, see above.
- [WONTFIX] A real `ON DELETE CASCADE` FK on
  `proposed_memories.conversation_id`. Adding one means rebuilding the
  table, and the explicit delete would have to stay anyway for the
  reason above, so the FK would buy defence in depth at the cost of a
  migration on a table the fix already covers.

---

## 2. SSE stream-resume endpoint failed for users in production (RESOLVED 2026-09-02)

**Severity:** ~~high~~ -> **resolved**. Closed 2026-09-02.

**Resume now works.** Verified against the live cluster on 2026-09-02:
POST a turn, abort mid-stream, reconnect with `Last-Event-ID` ->
**HTTP 200** and 2634 further events through to a terminal `done`, run
twice. That is the first recorded 200 from this endpoint; the symptom
below documents a multi-day window with none. Repeatable via
`scripts/smoke-resume.py`.

Everything this entry raised is now closed, and **three of its four
factual claims turned out to be stale** by the time anyone re-checked
them. That is the durable lesson here, more than any individual fix:

| claim | actual state |
|---|---|
| `500` "fixed in tree, pending deploy" (2026-06-10) | shipped since June. `main.py:64` has the module-scope import in repo and container. Stale for three months. |
| hard-`410` on buffer overflow | fixed by `3bea54d`: resume consults the client checkpoint, not the latched `truncated` flag |
| `GRACE_S` "60s before the turn is cancelled" | no longer cancels. `a27b4d2` made grace expiry **promote to background**; the turn completes and persists |
| windows "plausibly too short" | measured; neither loses work. See the follow-up table |

**Browser half (2026-09-02): mostly already covered, audited rather
than assumed.** A blip needs no network, it is a fetch body that ends
with no `done` event, which MSW produces exactly. Auditing what that
already exercises showed the entry was pessimistic:

| behaviour | covered by |
|---|---|
| reassembly after a drop (`'before after'` in one bubble) | `useChat.error.test.ts`, "drop after conversation event reconnects and stitches tokens into one bubble" |
| the `Last-Event-ID` checkpoint | `api.reconnect.test.ts` |
| reopen, and 410 -> transcript reload with no banner | `chatStore.background.test.ts` |
| **give-up at the end of the backoff schedule** | **`api.backoff.test.ts` (new)** |

Only the last row was missing. Every other test advances fake timers
by 1100ms, one backoff step, so the end of the `[1,2,4,8,16,30]s`
schedule had never run, and it is the path a user in a tunnel actually
hits. The new test pins that the give-up emits exactly ONE banner
rather than one per attempt, and clears the active-stream pointer so
the next mount does not chase a stream that is never coming back.
Verified to fail when that path is broken.

Two things worth recording from the audit, since they are the kind of
thing that rots quietly:

- The hook and store suites do **not** catch a removed `Last-Event-ID`
  header; only `api.reconnect.test.ts` does. Their resume fixtures
  serve the tail regardless of what is asked for, so they would pass
  against a client that resumed from the wrong place.
- A first draft of the new test also re-covered reassembly and the
  checkpoint. That was redundant with the two suites above and was
  removed rather than left to pad the count.

**Rendering (2026-09-02): checked in a browser, no defect seen.** The
operator ran the DevTools Network -> Offline round trip (per-tab, so no
real networking involved and no users disturbed) and reported it
successful.

Weight that appropriately, and this is the last claim in this entry so
it is worth being explicit: this is an operator report of one manual
observation, not a measurement anyone can re-run. It is the weakest
evidence in the entry, and deliberately so, because the alternative was
leaving the item open forever. If a rendering defect turns up later,
suspect this line first, not the machine-checked layers beneath it.

### Closed

Nothing outstanding. For a future reader, the sequence that actually
resolved this was: read the code before trusting the entry (three of
its four claims were stale), drive the real endpoint rather than the
unit tests (`scripts/smoke-resume.py`), measure the windows rather than
tune them, audit the existing frontend suites before adding to them
(most of the "browser half" was already covered), and only then ask a
human for the one thing that genuinely needed eyes.

### Symptom (as originally observed, June 2026)

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
     **[Measured 2026-09-02: confirmed, but not data loss. The turn has
     completed and persisted, so the 410 costs a transcript reload, and
     `resumeChat` already reloads rather than showing a banner.]**
   - `GRACE_S = 60.0` — only 60s of disconnect grace before the turn is
     cancelled.
     **[STALE. `a27b4d2` made grace expiry promote the stream to
     background instead: the turn keeps running, completes and
     persists. Cancellation now only happens at
     `MAX_BACKGROUND_PER_USER` or past `BACKGROUND_MAX_S`. A measured
     75s drop recovered completely.]**
   - `MAX_LOG_EVENTS = 1000` — buffer overflow flips `truncated`, after
     which resume 410s even within the time window (long tool-heavy
     turns can exceed 1000 events).
     **[STALE. `3bea54d` made resume consult the client's checkpoint
     rather than the latched flag, so an overflow no longer refuses a
     client whose checkpoint is still retained.]**

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
- [MEASURED 2026-09-02, no change needed] Reconsider the retention/grace
  **windows**. Measured against the live server rather than reasoned
  about, and the concern does not survive contact:

  | case | result |
  |---|---|
  | completed turn, resume at +70s | **410** `stream is gone` (confirms `DONE_RETENTION_S`) |
  | dropped mid-stream, resume at +75s (past `GRACE_S`) | **200**, 964 further events, answer persisted (2088 chars) |

  Neither window loses work:

  - **`GRACE_S` no longer cancels.** The description above is stale.
    Commit `a27b4d2` made grace expiry *promote the stream to
    background*, so the turn keeps running, completes and persists.
    Cancellation now only happens at `MAX_BACKGROUND_PER_USER` or past
    `BACKGROUND_MAX_S`. The 75s drop above recovered completely.
  - **`DONE_RETENTION_S` expiry is not data loss.** The turn has by
    definition completed and persisted, so the 410 costs a transcript
    reload, not an answer. The client already treats it that way:
    `resumeChat` maps 410 to a synthetic `stream_gone` and reloads,
    and only `streamChat`'s mid-stream loop shows a banner, which is
    the deliberate case of a bubble the user is watching die.

  Raising either constant would buy nothing but registry memory.
  Closing this rather than tuning it.

  (A third case, resuming a completed stream from its FINAL event id,
  returned 200 with 0 events. That is correct, nothing is newer than
  your last event, and is an artifact of how the probe chose its
  checkpoint rather than a finding. Replay from an earlier checkpoint
  is covered by the 964-event case above.)
- [DONE 2026-09-02, via `3bea54d`] Make truncation degrade gracefully
  instead of hard-410. Resume now consults the client's checkpoint
  rather than the latched `truncated` flag, so an overflow no longer
  refuses a client whose checkpoint is still retained. Raising
  `MAX_LOG_EVENTS` itself is no longer needed for correctness; revisit
  only if buffer memory becomes a concern.
- [OPEN] **Client-side UX:** on a browser refresh, repaint/preserve the
  partial assistant text rather than showing only the replayed tail
  (the caveat above). The raw-error vs graceful-retry handling is already
  in place; this is specifically about the cross-refresh repaint.
- [DONE 2026-09-02] **Server round-trip verified, and it passes.** Ran
  against the live cluster: POST a turn, abort mid-stream after 12
  events, reconnect with `Last-Event-ID`. Result **HTTP 200**, 2634
  further events delivered through to a terminal `done`. Repeated a
  second time with the same outcome. This is the first recorded 200
  from this endpoint; the entry above documents a multi-day window with
  none.

  Landed as `scripts/smoke-resume.py` so it is repeatable rather than a
  one-off. It uses a throwaway address, sends `X-Munin-Egress=off` so no
  paid or rate-limited tier is touched, and deletes the conversation it
  creates (verified: no residue).

- [OPEN] **Browser half still unverified.** The smoke drives the HTTP
  contract, not the webui. Two things it cannot cover and a human
  should, once: that the UI renders resumed content after a real
  network blip, and the cross-refresh repaint caveat above, where the
  bubble shows only the replayed tail because in-memory partial text is
  lost. The client was read end-to-end and found contract-correct on
  2026-06-10, so this is confirmation rather than investigation.

- [DONE 2026-09-02] The 60s windows are now measured; see the table
  above. No change needed.

---

## 2 (continued). REOPENED and re-closed the same day, 2026-09-02

The entry above was closed in the morning. Hours later a user reported
`Stream is no longer available on the server`, which reopened it. This
is that second chapter, kept with the first so the whole arc reads in
one place: closed on evidence, reopened by a user, closed again on
better evidence.

The lesson is not that the first close was careless. The server round
trip really had been verified. It is that "verified" answered a
narrower question than the entry's title implied: resume worked, on a
short turn, reconnecting after three seconds. The user's turn was four
and a half minutes long and reconnected after a minute, and nothing
that had been checked covered that.

### The second defect: replay buffer too small for one turn

**Severity:** medium (misleading banner; no data loss)

Closed earlier the same day, reopened hours later when a user reported
`Stream is no longer available on the server`. Historical write-up in
[`archive/KNOWN-BUGS-resolved.md`](archive/KNOWN-BUGS-resolved.md);
this is the new defect.

### What was actually wrong

Two things, and neither was the endpoint being broken. The 410s were
CORRECT refusals.

**1. `MAX_LOG_EVENTS = 1000` could not hold a single turn.** Measured:
one ~500-word answer emits **~2,600 SSE events**. So the replay buffer
rolled past a client's checkpoint within roughly 20-30 seconds of
disconnection, which is SHORTER than the 60s grace window. Any real
blip on a long tool-heavy turn therefore landed past the buffer, and
`can_resume_from()` honestly refused rather than replaying with a gap.

I had marked this "largely addressed" by `3bea54d` when closing the
entry. That was wrong: `3bea54d` helps a client still INSIDE the
1000-event window, and on the turns people actually want back, nobody
is.

**2. `streamChat`'s mid-stream 410 raised a banner claiming the work
was gone.** That branch predates background turns. `a27b4d2` made
grace expiry PROMOTE the stream, so the turn finishes server-side and
persists regardless of reconnection.

The production evidence, three users in six minutes, is unambiguous:
every 410 arrived ~59s BEFORE its stream was promoted to background,
so the stream was alive at the time and the answer persisted in every
case.

```
a774d5d1  resume 410 10:16:30  ->  promoted to background 10:17:28
79e1a95b  resume 410 10:19:08  ->  promoted to background 10:20:07
9c451761  resume 410 10:22:28  ->  promoted to background 10:23:27
```

The reporting user's answers were all in `chat_store`, including a
6,305-char research reply saved while she was being told it was lost.

### Fix

- `stream_registry.py`: `MAX_LOG_EVENTS` 1000 -> 20000, **plus a new
  `MAX_LOG_BYTES` of 8 MB**. Raising the count alone would repeat the
  `96921b7` mistake of bounding a buffer in the wrong unit: token
  events are a few hundred bytes, but one tool result or evidence
  passage can be orders of magnitude larger, so a count says nothing
  about memory. Eviction now runs until both bounds hold.
- `webui/src/lib/api.ts`: the mid-stream 410 emits `stream_gone`
  instead of an error, matching what `resumeChat` already did. The
  store's handler resets streaming state and reloads the transcript,
  and `_attemptedResumes` guards the re-attach loop.

Tests: three in `tests/test_stream_registry.py` (byte cap evicts before
the count cap, the buffer survives a realistic ~3000-event turn, byte
accounting stays exact across eviction) and one in
`webui/src/lib/api.backoff.test.ts` (mid-stream 410 reloads, no
banner). `useChat.error.test.ts`'s 410 case was rewritten: it asserted
the old banner contract, which this change deliberately inverts.

### Verified in production 2026-09-02

Backend deployed and the reported scenario reproduced against it: drop
at event 12, wait 90s while the turn keeps generating, then resume
from that now-stale checkpoint.

```
resume from a 90s-stale checkpoint -> HTTP 200
replayed 3330 events, terminal=True
```

3,330 events is more than three times the old 1000-event buffer, so
this is precisely the case that 410'd for three users this morning.
`MAX_LOG_EVENTS=20000` / `MAX_LOG_BYTES=8MB` confirmed live, 22/22
registry tests pass against the deployed code.

### Still open

- [DONE 2026-09-02] **Frontend deployed.** Rebuilt
  (`index-B6d0kZnr.js` 2026-08-25 -> `index-Cglzjdvj.js`) and rsynced.
  Verified inside the Caddy container, which is what actually serves
  it: the new bundle is present through the `static/` directory bind
  mount, `index.html` points at it, the old bundle is pruned, and the
  "Stream is no longer available" string is gone (`stream_gone`
  present instead).

  No container rebuild or `caddy reload` was needed: `static/` is a
  DIRECTORY bind mount (`:ro`), so files resolve per request. Order
  mattered though, and the documented procedure gets it right for a
  reason: ship `index.html` plus the new bundle FIRST, prune stale
  bundles SECOND. Reversed, the live `index.html` would briefly point
  at a bundle that had just been deleted.
- [DONE 2026-09-02] **Measured, and it inverted the assumption.** The
  byte cap was sized on a guess that tool results and evidence passages
  would dominate memory. A heavy research turn (corpus search plus two
  GROBID full-text extractions) measured:

  ```
  events               11,330
  total buffered        0.35 MB   of the 8 MB cap  (4.4%)
  largest single event  4,824 B
  avg bytes/event          32 B
    thinking    0.15 MB
    token       0.05 MB
    tool_result 0.04 MB   <- what the cap was sized around
  ```

  The buffer is not a few large payloads, it is a great many tiny ones.
  At 32 B/event, 8 MB permits ~259,000 events while the COUNT permitted
  20,000: **the count binds first by more than 10x**, so the byte
  warning added earlier the same day reports a bound that will rarely
  fire, and the bound that actually binds was silent.

  Worse, the headroom was thin: that one heavy turn used 11,330 of
  20,000 events (57%). A Deep Research turn would have exceeded it,
  truncated, and started refusing reconnects again, i.e. the original
  bug one order of magnitude up.

  Changes: `MAX_LOG_EVENTS` 20000 -> **100000** (~3.2 MB at measured
  density, still under `MAX_LOG_BYTES`, so the byte cap remains a live
  backstop rather than dead code), plus a **symmetric warning when the
  count binds**, naming the seq reconnects are served from so it can be
  correlated against a client's `Last-Event-ID`.

  Both warnings are latched once per stream and logged AFTER eviction,
  so the reported seq is the one actually in effect. The regression
  test now asserts the buffer survives a **measured** 11,330-event
  turn rather than a made-up number.

  Operationally: `grep "EVENT cap"` is the line to watch;
  `grep "BYTE cap"` should stay quiet unless a turn's payloads are
  unusually large.

### Final verification, 2026-09-02 (what the close rests on)

All three checks re-run against the deployed change:

```
1. basic resume round trip   200, 1,571 events, terminal=True     PASS
2. 90s-stale checkpoint      200, 3,636 events replayed           PASS
3. heavy tool turn           12,877 events / 0.37 MB              PASS

cap warnings fired: 0        probe residue in the DB: 0
```

Check 2 is the reported failure itself, recovering cleanly.

Check 3 independently reproduces the sizing: 12,877 events / 0.37 MB
against the earlier 11,330 / 0.35 MB, from a separate run. Two samples
within ~13% means that figure is a property of the workload rather
than a fluke, and density held at 30 B/event with `thinking` still
dominating and `tool_result` at 0.03 MB. So the premise the byte cap
was built on is wrong twice over, from independent measurements.

Headroom after the change: 12.9% of `MAX_LOG_EVENTS`, 4.6% of
`MAX_LOG_BYTES`. The same turn used 64% of the old 20,000 cap.

Zero cap warnings, and unlike an empty log after a restart this
absence is evidence: it comes from the heaviest turns that can be
generated locally.

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

### Follow-up

- [DONE 2026-06-10] **Frontend persona divider.** `webui` now renders a
  reload-safe `PersonaDivider` ("now {persona}", with the delegation
  reason when present) wherever the per-message `persona` changes, in
  `components/MessageList.tsx` + `components/PersonaDivider.tsx`. Covers
  delegate_to_persona and manual switches; draws nothing for
  single-persona or legacy-NULL history. Tests in `MessageList.test.tsx`
  (4 cases). Built and deployed to the VPS (chat.muninai.org serves
  bundle `index-WcM4rAhf.js`).
- [TODO] **Manual verification.** Redo the Meitner -> Turing switch in the
  live UI and confirm (a) the receiving persona acknowledges the handoff
  instead of denying it, and (b) the divider renders at the switch point.
  Unit tests prove the marker is built/injected and the divider logic is
  correct, not that the model obeys the marker in a live turn.

---

## 4. NVIDIA kernel module drifts behind the kernel, GPUs vanish on the weekly reboot

**Severity:** high (whole-cluster GPU outage, silent until the next reboot)

### Symptom

On 2026-08-31 the Monday 05:00 reboot came back with no GPUs at all.
`nvidia-smi` reported "couldn't communicate with the NVIDIA driver",
`lsmod | grep nvidia` was empty, and only `/dev/nvidiactl` existed
(a stale node, no `/dev/nvidia0` or `/dev/nvidia1`). vLLM never
restarted, so chat was down from 05:02 until the driver was repaired
by hand at 10:55. See bug 5 for why this became a full outage rather
than a degraded one.

The failure is silent while the machine stays up: the driver keeps
working from the already-loaded module, so nothing looks wrong until
the next reboot, which on this box is an automated weekly cron.

### Root cause

The kernel moved forward and the NVIDIA kernel module did not.

- 2026-08-21, `unattended-upgrade` installed `linux-image-7.0.0-30-generic`
  and moved the `linux-generic-hwe-24.04` metapackage from `7.0.0-29`
  to `7.0.0-30` (`/var/log/apt/history.log`).
- `linux-modules-nvidia-580-open-generic-hwe-24.04` stayed at
  `7.0.0-28.28~24.04.1`. After the upgrade, `find /lib/modules -name 'nvidia*.ko'`
  had builds only for `7.0.0-28-generic` and `6.17.0-35-generic`.
- The 05:00 reboot booted `7.0.0-30-generic`, which has no `nvidia.ko`.

`/var/log/unattended-upgrades/unattended-upgrades.log` shows the whole
nvidia-580 stack (driver, module packages, all the `libnvidia-*`) as
"kept back because a related package is kept back or due to local
apt_preferences(5)". The module package is version-locked to
`nvidia-driver-580-open`, which had a pending `580.159.03 -> 580.173.02`
upgrade, so the two move together or not at all.

The exact reason apt held it back is **not** pinned down. Two candidates,
neither confirmed:

- The manual upgrade required **removing**
  `linux-modules-nvidia-580-open-6.17.0-35-generic` ("1 to remove" in the
  apt transaction). unattended-upgrades does not perform removals by
  default, which would hold back the entire dependency set.
- `Unattended-Upgrade::Allowed-Origins` in
  `/etc/apt/apt.conf.d/50unattended-upgrades` lists `${distro_codename}`
  and `${distro_codename}-security` but not `${distro_codename}-updates`.
  This is weaker evidence: the driver is published to *both*
  `noble-updates/restricted` and `noble-security/restricted`, so origin
  filtering alone should not have blocked it.

### Immediate repair (applied 2026-08-31)

```
sudo apt-get install -y linux-modules-nvidia-580-open-7.0.0-30-generic
sudo modprobe nvidia && sudo modprobe nvidia_uvm
sudo systemctl start nvidia-persistenced
sudo nvidia-smi
```

21 packages upgraded, driver now 580.173.02, both RTX 5090s back. No
reboot was needed because no old module was loaded to conflict with.

### Suggested fix

Prefer a guard that does not depend on diagnosing apt's behaviour,
because the failure mode is "kernel and module disagree" regardless of
which apt rule caused it.

**Primary: refuse to reboot into a kernel with no NVIDIA module.**
Add a pre-flight check to `/opt/hugin/scripts/maintenance/safe-reboot.sh`
before it stops SLURM. Resolve the kernel that will actually boot (the
newest installed `linux-image-*`, not `uname -r`, which is the *running*
one) and confirm a module exists for it:

```bash
next_kernel=$(ls -1 /lib/modules | sort -V | tail -1)
if ! ls /lib/modules/"$next_kernel"/kernel/nvidia-*/nvidia.ko >/dev/null 2>&1; then
    echo "ABORT: no NVIDIA module for $next_kernel; not rebooting"
    exit 1
fi
```

Aborting the reboot leaves a working cluster and a loud log line, which
is strictly better than a silent GPU-less boot. Pair it with the
Sunday 04:00 `reboot-warning.sh` run so the warning fires a day early
and there is time to install the module before Monday.

**Secondary: stop the drift at the source.** Once the hold-back reason
is confirmed, either allow `${distro_id}:${distro_codename}-updates` in
`Allowed-Origins`, or add an explicit weekly
`apt-get install linux-modules-nvidia-580-open-$(ls -1 /lib/modules | sort -V | tail -1)`
step ahead of the reboot. The guard above is what makes the outage
impossible; this only reduces how often the guard has to fire.

### Verification

`ls /lib/modules/$(uname -r)/kernel/nvidia-*/nvidia.ko` should exist,
and `nvidia-smi` should list both cards after any reboot.

### Update 2026-08-31: the guard already existed, undeployed

The "suggested fix" above was already written and committed, in the OTHER
repo. `HuginSLURM/scripts/maintenance/lib-reboot.sh` defines
`check_reboot_safety()`, and `safe-reboot.sh` wires it in with an abort, a
`wall` broadcast and `exit 2`. It resolves the next-boot kernel and refuses
to reboot when its NVIDIA modules are missing, which is this outage exactly.

It never reached the machine. The live `/opt/cluster/scripts/maintenance/
safe-reboot.sh` predates that work and `lib-reboot.sh` was not on the box at
all, so the guard could not fire. **This was a deploy gap, not a missing
feature.**

Correcting the record: an earlier version of this entry said `slurm.conf`
was untracked. That was wrong, and came from running `git ls-files` in munin
only. HuginSLURM owns and tracks `slurm.conf`, `gres.conf`, `cgroup.conf` and
the maintenance scripts, and deploys them with its own
`deploy.sh config|scripts`.

Fixed in HuginSLURM `b5f334c`, which also backports two live-only changes
that deploying would otherwise have REVERTED (`MaxCPUsPerNode=12` and the
tp2-aware job cancel), and switches the module probe from `dpkg-query` to
`modinfo -k` so DKMS sites are not blocked.

[DONE 2026-09-01] Deployed via `HuginSLURM/deploy.sh scripts`, and the
guard was run read-only against the live machine afterwards:
`status=ok-same`, current and next-boot kernel both `7.0.0-30-generic`.
A later change (`833ced5`) also gave the guard an off-machine alert, so
a skipped reboot now reaches Discord instead of only `wall`.

---

## 5. `slurmd` dies permanently when `/dev/nvidia0` is missing at boot

**Severity:** high (turns a recoverable GPU problem into a whole-day cluster outage)

### Symptom

After the 2026-08-31 reboot, `slurmd.service` was `failed` and stayed
failed for the next six hours. The node showed as
`DOWN+DRAIN+NOT_RESPONDING` with `SlurmdStartTime=None`, and the 06:00
cron-submitted vLLM job (972) sat `PENDING` with
`Reason=PartitionConfig` because no node ever registered.

Note for future triage: `PartitionConfig` here is purely the down-node
symptom. It is easy to misread as a resource-limit problem (the
`vllm-serving` partition has `MaxCPUsPerNode=4` while
`start-vllm-service-tp2.sh` asks for `--cpus-per-task=12`), but that is
a red herring. A `srun --test-only` probe at 4 CPUs with no GRES fails
identically, and jobs 969 to 971 ran all week with 12 CPUs.

### Root cause

`gres.conf` declares GPUs by device file:

```
NodeName=hugin Name=gpu Type=batch File=/dev/nvidia0
NodeName=hugin Name=gpu Type=vllm  File=/dev/nvidia1
```

With the NVIDIA driver missing (bug 4), those nodes never appear.
`slurmd` waits about 19 seconds and then exits fatally:

```
05:02:27 slurmd: error: Waiting for gres.conf file /dev/nvidia0
05:02:46 slurmd: fatal: can't stat gres.conf file /dev/nvidia0: No such file or directory
05:02:46 systemd[1]: slurmd.service: Failed with result 'exit-code'.
```

`/usr/lib/systemd/system/slurmd.service` has no `Restart=` directive,
which means `Restart=no`. One fatal exit at boot and the daemon is
down until a human notices. Nothing retried, and nothing alerted.

### Suggested fix

Add a restart policy via a drop-in (not by editing the packaged unit,
which apt will overwrite), at
`/etc/systemd/system/slurmd.service.d/restart.conf`:

```ini
[Unit]
After=nvidia-persistenced.service
Wants=nvidia-persistenced.service
StartLimitIntervalSec=600
StartLimitBurst=10

[Service]
Restart=on-failure
RestartSec=30
```

Then `systemctl daemon-reload`.

This covers the ordinary case where the driver is present but the device
nodes are not yet created when `slurmd` starts: ten retries over ten
minutes is far longer than the driver needs, and the node registers on
its own.

It deliberately does **not** paper over a genuinely absent driver. With
no module installed, `slurmd` still exhausts its burst and stops, but
the state is then an obvious repeated-failure trail in
`systemctl status slurmd` rather than a single fatal line hours in the
past. Bug 4's pre-reboot guard is what prevents that case; this drop-in
handles the timing race.

### Follow-up

- [DONE 2026-08-31] **Alerting.** Neither failure surfaced anywhere.
  Added `scripts/vllm/check-vllm-health.sh` plus
  `munin-vllm-health.timer`, hourly at `:15`, installed by
  `deploy.sh vllm`. It skips the 02:00-06:00 downtime window, refuses
  to resubmit when the node is `DOWN` (a job would only pend, which is
  this incident's shape), restarts once when the node is healthy and no
  job exists, and holds a one-hour cooldown so a crashlooping vLLM is
  not fed back to SLURM every hour. Alerts go to the log and `wall`,
  plus `MUNIN_ALERT_WEBHOOK` from `cluster.env` when set, since `wall`
  reaches only logged-in terminals and nobody was logged in that
  morning. Documented in `shared/docs/MONITORING.md`.

### Update 2026-08-31: implemented in HuginSLURM

`config/slurmd-restart.conf` in HuginSLURM `b5f334c`, installed by
`deploy.sh config` to `/etc/systemd/system/slurmd.service.d/restart.conf`.
It lives in `deploy_config` rather than `setup/03-slurm-install.sh` because
setup runs once per node and would never reach an already-provisioned
cluster, which is the case that actually needed it.

Also worth recording here: `slurm-auto-resume.service` failed the same
morning with `Dependency failed`, because it declares
`Requires=slurmd.service`. So even a driver fix would have left the node
drained until someone resumed it by hand. The restart policy addresses the
cause; the dependency is worth revisiting if slurmd ever stays down for a
reason the drop-in cannot retry past.

[DONE 2026-09-01] Deployed via `HuginSLURM/deploy.sh config`; `systemctl show slurmd` confirms `Restart=on-failure`, `RestartUSec=30s`, `StartLimitBurst=10`.

---

## 6. `build_chunk_index.py` could not use both GPUs (RESOLVED 2026-09-02)

`--shard i/n` added. One process per GPU:

```
$PY build_chunk_index.py --shard 0/2 --device cuda:0 --deadline 05:30 &
$PY build_chunk_index.py --shard 1/2 --device cuda:1 --deadline 05:30 &
```

### Sharding on a hash, NOT on position

The plan in this entry called for striding by POSITION in `todo`,
explicitly preferring it over hashing so the tagged-first ordering
stayed spread across shards. **That design was unsafe and was not
implemented.**

`todo` is derived from `done`, which is scrolled at startup. Two shards
never start at the same instant, so the second one scans a `done` that
already contains what the first has written and builds a DIFFERENT
`todo`. `k % n == 0` over one list is not complementary to
`k % n == 1` over another, so position striding produces silent gaps
and silent duplicates: the run reports success and the missing papers
surface weeks later as evidence that cannot be retrieved.

Hashing `paper_id` has no such coupling. Shard membership is a property
of the paper, fixed regardless of when a shard starts or what is
already indexed, so coverage is disjoint and complete by construction.
It also keeps the property position striding was chosen for, because
tagged papers distribute evenly and each shard still sorts its own
subset tagged-first.

### A race the unit tests could not have found

Running the shards in parallel for the first time killed one at
startup: both see the collection missing and both POST a create, and
Qdrant 409s the loser. On a real rebuild the collection IS fresh, so
this would have fired every single time. `create_collection` now
tolerates losing that race. Worth noting the sequence, since it is the
argument for the live test: unit tests passed, the real corpus
partition check passed, and only actually running two processes at
once surfaced it.

### Verification

- `test_shard_partition.py`: exact partition for n=1..8, balance within
  5%, membership is a pure function of `paper_id`, `--shard 0/1` is a
  no-op.
- Against the REAL 68,869-row corpus: disjoint and complete at n=2/3/4,
  spread <= 1.6%.
- Live, both GPUs in parallel into a scratch collection: 5 papers each,
  zero overlap, every paper attributable to exactly one shard.
  `papers_chunks` untouched (1,426,195 points, green).

### Notes for the next rebuild

- `--limit` is PER SHARD. A global limit would need shards to agree on
  one ordering, which is the coupling sharding exists to avoid.
- GROBID's pool caps at 10, so two shards at `--concurrency 4` is 8 and
  fine; lower it at n=3+.
- The `done` scan is paid per process (~1.4M points each). Tolerable at
  n=2; build it once to a file if the shard count ever goes higher.
- Both GPUs are only free while vLLM is down, so a sharded run belongs
  in the nightly window it was designed for.

---

## 7. Host-only tests crash instead of skipping inside the container (RESOLVED 2026-09-03)

### One of the three was not a host-only test at all

The entry framed all three as host-only tests needing a skip. Running
them in the REAL deployed container showed that is wrong for the one
that mattered:

| file | in the deployed container, before | should be |
|---|---|---|
| `test_api_contract` | `SKIP` cleanly | correct, the model |
| `test_config_schemas` | **10 passed** | correct; only an ad-hoc mount failed |
| `test_persona_prompt_split` | **`IndexError: 3`** | **should PASS** |

`test_persona_prompt_split` is not host-only. `/app/personas` exists in
the deployed container, `PERSONAS_DIR` defaults to exactly that, and
all three persona JSONs are there. It crashed on
`Path(__file__).resolve().parents[3]`, which walks off the top of the
filesystem from `/app/tests/` (parents there is exactly
`['/app/tests', '/app', '/']`), raising at IMPORT before the
`is_dir()` guard meant to handle a missing repo could run.

So it was not a test that could not run in the container. It was a
test that would have passed and never got the chance, for months.
Adding the skip the entry asked for would have papered over that and
permanently lost container-side coverage of the prompt-split safety
property.

### Fix

- `test_persona_prompt_split.py`: resolve the repo path defensively
  (`parents[3] if len(parents) > 3`) so it falls through to the
  `/app/personas` default instead of raising. **Plus** a skip in
  `_main()` for the case where no persona source exists anywhere,
  because without it the fixed version emits four failures reading
  "chat not loaded", which is the same misleading-failure problem in a
  new costume.
- `test_config_schemas.py`: the missing third branch. `_shipped_persona`
  raised `FileNotFoundError` when neither location resolved, surfacing
  as a bare `[FAIL]`. Now one `SKIP` line.
- Both skips live in `_main()`, not at import, so pytest collection is
  unaffected. `test_api_contract.py` already did it there.
- `retrieval/tests/README.md`: the root cause, which is that three
  environments exist and only two are obvious. An ad-hoc
  `docker run -v retrieval:/app` looks like the container and is not.
  Also records the `--network container:` trap, which silently removes
  the DNS that resolves `qdrant` so index-touching tests take a
  "collection absent" branch and assert against `None`.

### Verified: three files x three environments

```
                             host          deployed      ad-hoc mount
test_api_contract            OK            SKIP          SKIP
test_persona_prompt_split    4 passed      4 passed      SKIP
test_config_schemas          10 passed     10 passed     SKIP
```

No failures anywhere, and every non-run is an explicit SKIP naming the
path it wanted. That matrix is what nobody had run: it is what turned
this entry from "add two skips" into "one of these is a real broken
test".
