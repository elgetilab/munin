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

## 6. `build_chunk_index.py` cannot use both GPUs, so a full-corpus rebuild wastes half the box

**Severity:** medium (no data risk; doubles the wall-clock of every full rebuild)

### Symptom

The embed phase runs on exactly one GPU. Measured during the
2026-08-31 backfill, once the run reached the cache-hit region where
embedding is genuinely the bottleneck:

```
0, 100 %, 4200 MiB  |  1, 0 %, 4 MiB
0,  82 %, 4200 MiB  |  1, 0 %, 4 MiB
```

GPU 0 saturated, GPU 1 completely untouched. On a full-corpus rebuild
the embed phase is about 44 minutes at the measured 564 chunks/s for
~1.48M chunks, so roughly 22 minutes of a 5090 are thrown away every
time.

Note this only shows up in the cache-hit region. Where the run is
falling back to live GROBID, or grinding through papers with
unparseable PDFs, the GPU sits near 0% and is not the constraint at
all. Both regimes occur inside a single run, so a single utilisation
sample will mislead. Sample during a period when the `failed` counter
is flat and `papers` is climbing.

### Root cause

`build_chunk_index.py` builds one encoder:

```python
enc = SentenceTransformer(MODEL, device=args.device)
```

and a single process walks `todo` in order. `--device cuda:1` moves the
work to the other card but does not split it.

Running two processes side by side does **not** work as a workaround.
Each one independently rebuilds `todo` from "everything in `papers_bge`
not already in `papers_chunks`" and then walks it in the same order, so
both process the same papers. Point ids are deterministic
(`point_id(paper_id, idx)`), so the second writer overwrites the first
in place rather than duplicating rows: the index stays correct, but the
second GPU buys nothing. It is wasted work, not corruption.

### Suggested fix

Add a `--shard i/n` flag and partition `todo` deterministically, after
the existing sort and the `--limit` slice:

```python
ap.add_argument("--shard", default=None, help="i/n, process only shard i of n")
...
if args.shard:
    i, n = (int(x) for x in args.shard.split("/"))
    todo = [p for k, p in enumerate(todo) if k % n == i]
```

Partition by position rather than by hash of `paper_id`: `todo` is
already sorted tagged-first, and striding by position keeps that
priority spread evenly across shards, so a deadline stop leaves every
shard having made comparable progress on the tagged papers. Hashing
would scatter that ordering.

Then run one process per card:

```
$PY build_chunk_index.py --shard 0/2 --device cuda:0 --deadline 05:30 &
$PY build_chunk_index.py --shard 1/2 --device cuda:1 --deadline 05:30 &
```

Two caveats worth handling while in there:

- **The resume scan is paid per process.** Each one scrolls the whole
  of `papers_chunks` (1.2M points and growing) to build its `done` set,
  which is a couple of minutes each and grows with the corpus. It is
  tolerable at n=2. If the shard count ever goes higher, build the set
  once and write it to a file the shards read.
- **Both cards are only free while vLLM is down.** Under the TP=2
  profile vLLM holds `gpu:vllm:1` and `gpu:batch:1` together, so a
  two-shard run belongs in the nightly downtime window, which is
  already where the embed phase is meant to run. See
  `TP2-GPU-SHARING-EXPERIMENT.md`.

### Why it was not done during the 2026-08-31 run

The remaining work at the point the saturation was noticed was about
33 minutes on one card. Adding the flag, restarting, and paying the
resume scan twice came to roughly what it would have saved, on a job
already finishing an hour inside its window. The cost/benefit inverts
for a full rebuild, which is what this entry is for.

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
