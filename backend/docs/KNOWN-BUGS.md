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

- [TODO] **Alerting.** Neither failure surfaced anywhere. A check that
  the node is not `DOWN`/`NOT_RESPONDING`, and that vLLM answers after
  the 06:00 start window, would have caught this at 06:05 rather than
  at 10:44 when a human went looking. `shared/docs/MONITORING.md` is
  the place for it.

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
