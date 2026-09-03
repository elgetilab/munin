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
