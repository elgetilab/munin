# chats.db write lock: plan

Status: IMPLEMENTED 2026-11-16 ("proceed", Q1-Q2 defaults), not yet deployed.
`tests/test_chatdb_txn.py` 9/9 (against the old stores 3/9: the FK case
holds the lock, the stale snapshot raises, a failed unit gets committed by
another coroutine, and one of six stress processes ends with 72 lock errors
stuck in a transaction); retrieval hermetic 114/114 on the CI runner.
One finding beyond the plan: a half-read cursor (fetchone on a multi-row
SELECT) pins a stale snapshot that makes even BEGIN IMMEDIATE fail at once,
so `_begin_immediate` retries a lock error that came back quickly (the
cursor goes when its coroutine moves on), and `research_store._ensure_tables`
no longer uses `executescript`, which COMMITs first. Found 2026-11-16 during the Phase 2 lab
baseline; production writes were blocked from 13:18:43 to the retrieval restart
at about 13:30 UTC. No user turn was lost: none was sent in that window.

## What happened

1. At 12:54 the baseline harness was stopped mid-turn. Retrieval kept the turn
   running as a background stream (by design: a disconnected stream is
   promoted, `stream_registry.py`).
2. At 12:56 the harness cleanup deleted that conversation. Nothing stopped the
   stream.
3. At 13:18:43 the stream's final save, `chat_store.add_message`, ran
   `INSERT INTO messages` for the deleted conversation. With
   `PRAGMA foreign_keys=ON` the INSERT failed (`IntegrityError: FOREIGN KEY
   constraint failed`), but Python's sqlite3 had already opened an implicit
   transaction and the INSERT had taken the database write lock. No code rolls
   back, so the connection stayed in that transaction and production held the
   write lock.
4. Every other process writing chats.db then failed after the 5 s busy timeout:
   the eval retrieval instance (23 errors, 4 crashed turns, the two
   `s6_relaxation` runs that never got a conversation id), and a probe from
   inside the production container. Production itself would have released the
   lock on its next successful commit, which would also have committed whatever
   the failed unit had written so far.

## Root causes (verified)

- **R1. No rollback anywhere.** 33 `commit()` sites over 8 stores (`chat_store`,
  `artifact_store`, `user_profile_store`, `memory_proposals_store`,
  `research_store`, `project_store`, `plan_store`, `memory_store`) share one
  aiosqlite connection per process. A write unit that fails after its first
  statement (constraint error, lock error, cancellation of the awaiting task)
  leaves the transaction open. Only `artifact_store` has one rollback.
- **R2. Deferred transactions go stale.** Reproduced with two processes
  driving the real `chat_store` (`scratchpad/lockrepro`, 1 run in 3): one
  process ends with every write failing (160/160 "database is locked") and
  `in_transaction=True`. Its transaction began as a read; once the other
  process commits, the snapshot is stale and SQLite refuses to upgrade it
  (SQLITE_BUSY_SNAPSHOT, which the busy timeout does not retry). With no
  rollback it never leaves that transaction: the process can no longer write at
  all until restart. This is the eval container's state from 13:18:50 on.
- **R3. Units interleave on the shared connection.** Coroutine A's INSERT and
  coroutine B's `commit()` land in the same transaction, so B commits A's half
  of a unit, and A's later failure cannot undo it.
- **R4. Deleting a chat does not stop its running turn.** A user who deletes a
  chat while a background turn runs hits the same FK failure as step 3.

## Fix

1. **`chat_store.write_txn()`**, one async context manager for every write
   unit:
   - an `asyncio.Lock` per process, so one unit at a time owns the connection's
     transaction (R3);
   - `BEGIN IMMEDIATE` at the start, so the write lock is taken up front under
     the busy timeout and a stale read snapshot can never be upgraded (R2);
   - `commit()` on success, `rollback()` on any exception including
     `CancelledError`, then re-raise (R1);
   - re-entrant through a ContextVar, so a write unit that calls another
     (e.g. delete paths) joins the outer transaction instead of deadlocking.
   Reads stay as they are (they may run while a unit is open and then see that
   process's uncommitted rows, as they can today).
2. **Convert all 33 write sites** in the 8 stores to `async with write_txn():`
   (mechanical: the statements move inside the block, the `commit()` goes).
   An AST check in the test fails if any store calls `commit()` outside
   `write_txn`, so a new write path cannot regress.
3. **Busy timeout 5 s to 15 s** (`aiosqlite.connect(timeout=...)`), so a short
   lock held by a sibling process (eval instance, maintenance script) waits
   instead of failing.
4. **Deleting a chat cancels its live turn first** (`api_delete_chat`:
   `stream_registry.find_for_conversation`, set `cancel_event`, reason
   "conversation deleted", then delete). The turn's own save then sees a
   missing conversation, logs it, and rolls back cleanly (R4).

## Tests (hermetic, new `tests/test_chatdb_txn.py`)

- An FK-failing `add_message` leaves no transaction open and a second
  connection can take the write lock at once (fails today).
- Stale snapshot: connection A reads, connection B commits, A's write unit
  succeeds (fails today about 1 in 3 under load; made deterministic).
- A task cancelled between two statements of a unit rolls the unit back.
- A failing unit in one coroutine does not get committed by another's commit.
- Nested `write_txn` does not deadlock.
- AST guard: no `commit()` outside `write_txn` in the stores.
- Delete with a live stream sets its `cancel_event`.
- Two-process stress (the repro, 2 x 20 rounds of 8 concurrent writes): zero
  lock errors, neither process left in a transaction.

## Rollout

Retrieval only (`deploy.sh retrieval`), then: the eval instance comes back with
`deploy.sh instance up sage-a1.5 --name eval --max-seqs 4`, the two
`s6_relaxation` runs are redone with `--resume`, and the baseline scorecard is
rewritten.

## Open questions (defaults taken unless you say otherwise)

| # | Question | Default |
|---|---|---|
| Q1 | Should the eval instance keep sharing production's chats.db? | Yes for now: the fix makes sharing safe, and evals delete their chats. A separate DB per instance is cleaner but changes `deploy.sh instance` and the bundle/artifact paths; logged as a follow-up. |
| Q2 | Also add a startup or health check that fails when the connection is stuck in a transaction? | No: with every write in `write_txn` the state cannot persist. |
