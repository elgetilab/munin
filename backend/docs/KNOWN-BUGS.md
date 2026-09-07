# Known Bugs — backend (cluster)

Running list of confirmed bugs that are not yet fixed. Each entry
should carry enough detail (symptom, root cause, file references) to
pick up and fix without a rediscovery pass.

When a fix lands, replace the entry with a short stub pointing at
[`archive/KNOWN-BUGS-resolved.md`](archive/KNOWN-BUGS-resolved.md), and
move the full write-up there. Numbering is NEVER reused or renumbered:
commits and other docs refer to these by number, and #2 must keep
meaning the SSE resume bug forever. Stubs stay so those references
still resolve.

The history is kept rather than deleted on purpose. Several archived
entries were factually WRONG by the time anyone re-read them, claiming
work was pending that had shipped months earlier, and that pattern is
more useful to be able to search for than the individual fixes.

## Open entries: none (2026-09-07)

Every numbered entry below is a stub for a resolved bug. That is a
statement about this file, not about the system: it means nothing is
currently WRITTEN DOWN as broken. Bugs 2, 6 and 8 are the reminder of
what that is worth. Entry 2 was closed on a verified round trip and
reopened hours later by a user whose turn was longer than anything the
check had covered; entry 6's own recorded plan turned out to be unsafe
when someone finally implemented it; and entry 8 had been failing 100%
of the time for two months while this file said there was nothing
wrong, because the failure was caught by an `except` and written to a
log nobody was reading.

So an empty list is a good place to add to, not evidence there is
nothing to find. Entry 8 in particular was found by grepping the
service log for warnings, not by a user reporting it, and it is the
kind of thing that only ever surfaces that way.

---

## 1. Deleting a conversation orphans its `proposed_memories` (RESOLVED 2026-09-02)

Fixed: explicit delete in `delete_conversation()` plus a one-off purge
of the 11 pre-existing orphans. Full write-up in
[`archive/KNOWN-BUGS-resolved.md`](archive/KNOWN-BUGS-resolved.md).
Tests: `retrieval/tests/test_memory_proposals.py`.

---

## 2. SSE stream-resume endpoint failed for users in production (RESOLVED 2026-09-02)

Closed, reopened by a user the same day, closed again on measurement.
Two defects: a replay buffer that could not hold one turn, and a
mid-stream 410 reported as lost work when the answer was in fact still
being written. Both fixed and deployed, both halves verified live.

Re-runnable check: `scripts/smoke-resume.py`. Full write-up, including
the four stale claims the entry accumulated and the measurements that
finally sized the bounds, in
[`archive/KNOWN-BUGS-resolved.md`](archive/KNOWN-BUGS-resolved.md).

---

## 3. Persona switch dropped identity + faked memory loss (RESOLVED 2026-06-10)

Fixed via per-message `persona` attribution + a handoff marker. Full
write-up in [`archive/KNOWN-BUGS-resolved.md`](archive/KNOWN-BUGS-resolved.md).

---

## 4. NVIDIA kernel module drifts behind the kernel (RESOLVED 2026-08-31)

The pre-flight guard already existed in HuginSLURM and was simply never
deployed; deploying it plus `modinfo -k` hardening closed it. Lives in
`HuginSLURM/scripts/maintenance/lib-reboot.sh`. Full write-up in
[`archive/KNOWN-BUGS-resolved.md`](archive/KNOWN-BUGS-resolved.md).

---

## 5. `slurmd` dies permanently when `/dev/nvidia0` is missing (RESOLVED 2026-08-31)

Fixed by `HuginSLURM/config/slurmd-restart.conf`
(`Restart=on-failure`). Full write-up in
[`archive/KNOWN-BUGS-resolved.md`](archive/KNOWN-BUGS-resolved.md).

---

## 6. `build_chunk_index.py` could not use both GPUs (RESOLVED 2026-09-02)

`--shard i/n` added, sharding on a hash of `paper_id`. The position
striding this entry originally specified was unsafe across processes.
Full write-up in
[`archive/KNOWN-BUGS-resolved.md`](archive/KNOWN-BUGS-resolved.md).
Tests: `scripts/pipeline/test_shard_partition.py`.

---

## 7. Host-only tests crash instead of skipping inside the container (RESOLVED 2026-09-03)

Two files gained skips; a third turned out not to be host-only at all
and was a genuinely broken test. See
[`retrieval/tests/README.md`](../retrieval/tests/README.md) for which
environment to run tests in, and
[`archive/KNOWN-BUGS-resolved.md`](archive/KNOWN-BUGS-resolved.md) for
the write-up.

---

## 8. Attachments silently disabled the router for two months (RESOLVED 2026-09-07)

A turn carrying an image or a document sends a content LIST, and three
call sites read it as a string. The router's TypeError was swallowed by
its own `try/except`, so every such turn ran on the default profile and
persisted `persona = NULL`. Fixed by `content_text()` /
`replace_typed_text()` plus a persona-id fallback that no longer uses
`pin_id`. Full write-up in
[`archive/KNOWN-BUGS-resolved.md`](archive/KNOWN-BUGS-resolved.md).
Tests: `retrieval/tests/test_multimodal_turn_text.py`.
