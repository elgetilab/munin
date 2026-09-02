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

## 7. Host-only tests crash instead of skipping inside the container

**Severity:** low (test harness), but it costs review time and teaches
the wrong lesson

### Symptom

Some test files under `retrieval/tests/` need repo paths that do not
exist in the retrieval container, because the image ships `retrieval/`
as `/app` and nothing above it. Run there, they fail in ways that look
like product defects:

```
$ docker exec munin-retrieval python /app/tests/test_persona_prompt_split.py
  File "/app/tests/test_persona_prompt_split.py", line 22, in <module>
    _SHARED = Path(__file__).resolve().parents[3] / "shared" / "personas"
IndexError: 3
```

The same suite passes 4/4 on the host. There is no product bug here at
all: `parents[3]` from `/app/tests/` walks off the top of the
filesystem.

This actively misleads. During the 2026-09-01 search-recall work it was
reported twice as a "pre-existing failure" on the strength of a
container run, and only a host run showed both suites were green. A
test that fails for environmental reasons trains a reader to skim past
failures, which is exactly the habit that lets a real one through.

### Root cause

Three files resolve fixtures outside `/app` and handle it three
different ways:

- `tests/test_api_contract.py` — **correct**. Checks whether the paths
  resolve and prints
  `SKIP test_api_contract — BACKEND-API.md or main.py not reachable
  from this filesystem (host-only test).`
- `tests/test_persona_prompt_split.py` — crashes at import with
  `IndexError` before any test runs.
- `tests/test_config_schemas.py` — degrades to a plain `[FAIL]` when
  `/app/personas/chat.json` is absent. Note this one passes in the REAL
  deployed container, where compose mounts personas at `/app/personas`;
  it only fails in an ad-hoc `docker run` that mounts `retrieval/`
  alone. Its `_shipped_persona` helper already tries both locations,
  it just has no third branch for "neither".

### Suggested fix

Give the latter two the skip that `test_api_contract` already has: a
guard at import that prints one `SKIP` line naming the missing fixture
and exits 0. Roughly five lines each. The fixtures genuinely cannot be
present in a `retrieval/`-only mount, so skipping is the honest outcome
rather than something to engineer around.

Worth doing together with a short note in `retrieval/tests/README` (or
the run instructions in each docstring) recording that a `docker run`
mounting only `retrieval/` is not equivalent to the deployed container,
which also has `/app/config`, `/app/personas` and `/data`.
