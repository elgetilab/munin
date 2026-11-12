# Moving Lite's data back to the cluster

While the cluster is down, users work on Lite ([lite.md](lite.md)). Their chats,
artifacts, projects, memories, Deep Research jobs and uploaded documents then
live only on the Lite host. This page merges them into the cluster before you
switch the gateway back. The same tool runs the other way (cluster to a standby
Lite) after a later failover.

The tools are `backend/scripts/maintenance/merge_state.py` (stdlib only, runs in
either retrieval container) and `fetch_lite_state.sh` beside it; their headers
document every option.

## What it does

| State | How | Safety |
|---|---|---|
| `chats.db` rows (12 tables) | `merge_state.py db` | Source snapshotted and target backed up with the SQLite backup API; one transaction; refuses on schema mismatch, differing primary-key collisions, a message index slot already taken, or orphans; FTS checked before commit |
| `user_docs/<user-hash>/<doc_id>/` | `merge_state.py files` | Never overwrites; sha256 per file; a doc id owned by another user is refused |
| Qdrant `user_docs` points | `merge_state.py qdrant` | Vector config must match; differing point ids refused; a document already indexed under other point ids is refused, so chunks are never duplicated |

Every step is a dry run until you add `--apply`, and a second run is a no-op.
Reports print counts, ids and user hashes, never message text or addresses.

Not carried over: sandbox-generated artifact files (the rows come across, but
the files lived in Lite's RAM-only sandbox and are already gone), Deep Research
checkpoints (only needed to resume an interrupted job), and the KB upload queue
(`/mnt/uploads/complete/` on the frontend, which retries by itself once the
cluster ingest endpoint answers).

## Cutover

Run on hugin as a user in the `docker` group. No sudo is needed: `docker cp`
and `docker exec` reach `/opt/munin/data` through the retrieval container.

```bash
REPO=~/PycharmProjects/munin
TOOL=$REPO/backend/scripts/maintenance/merge_state.py
VPS=<admin-user>@<vps-host>          # the frontend VPS's admin login
KEY=<path to that login's SSH key>
STAGE=~/munin-staging/lite-merge/$(date -u +%Y%m%dT%H%MZ)
```

**1. Freeze Lite.** The maintenance flag puts the chat UI on its maintenance
screen and refuses new Deep Research jobs. Wait a minute so turns in flight
finish.

```bash
echo "{\"message\": \"Moving back to the cluster\", \"since\": \"$(date -u +%FT%TZ)\"}" |
  ssh -i $KEY $VPS 'docker exec -i munin-retrieval sh -c "cat > /data/maintenance.json"'
```

**2. Fetch** the snapshot, files and Qdrant export. Read-only on the VPS.

```bash
$REPO/backend/scripts/maintenance/fetch_lite_state.sh --host $VPS --key $KEY --out $STAGE
```

Check `$STAGE/manifest.json`: row counts, `user_docs.files`, and `qdrant_export.points`
equal to `remote_points_count`.

**3. Stage on the cluster** inside the retrieval container's data mount.

```bash
docker exec munin-retrieval mkdir -p /data/merge
docker cp $STAGE munin-retrieval:/data/merge/lite
docker cp $TOOL munin-retrieval:/tmp/merge_state.py
M="docker exec munin-retrieval python /tmp/merge_state.py"
```

**4. Dry runs.** All three should exit 0 with no `refused` key.

```bash
$M db --source /data/merge/lite/chats.snapshot.db --target /data/chats.db \
      --work-dir /data/merge/work --backup-dir /data/backups/merge \
      --docs-dir /data/merge/lite/user_docs
$M files --source-dir /data/merge/lite/user_docs --target-dir /data/user_docs
$M qdrant --source /data/merge/lite/user_docs.points.jsonl
```

In the `db` report, `external.attachment_refs_without_file_in_docs_dir` should be
0. If anything is refused, read `conflict_samples` / `orphan_samples` before
reaching for a policy flag.

**5. Apply**, files and points first so no merged message points at a missing
document.

```bash
$M files --source-dir /data/merge/lite/user_docs --target-dir /data/user_docs --apply
$M qdrant --source /data/merge/lite/user_docs.points.jsonl --apply
$M db --source /data/merge/lite/chats.snapshot.db --target /data/chats.db \
      --work-dir /data/merge/work --backup-dir /data/backups/merge --apply
```

The `db` report must say `"result": "merged"`, `"replan_after_write": "no-op"`
and show `fts_after` with equal `messages` and `messages_fts`. Running any of the
three again must report `nothing to do`. The service needs no restart.

**6. Switch the gateway back** to the tunnel ([lite.md](lite.md#returning-to-the-cluster)),
then remove the staging copy. Keep the backup in `/data/backups/merge` until users
have confirmed their chats.

```bash
docker exec munin-retrieval rm -rf /data/merge /tmp/merge_state.py
```

Lite's flag stays in place; remove it (`docker exec munin-retrieval rm /data/maintenance.json`
on the VPS) only when Lite is the standby again.

**Rollback** of the chats merge: stop nothing, copy the backup over with the
backup API from inside the container:

```bash
docker exec munin-retrieval python -c "import sqlite3,sys; s=sqlite3.connect(sys.argv[1]); d=sqlite3.connect('/data/chats.db'); s.backup(d)" /data/backups/merge/chats.db.pre-merge-<stamp>.db
```

## The other direction: cluster to a standby Lite

After a failover the standby Lite already holds an older copy, so scope the merge
to what changed and let the later row win where both sides touched one:

```bash
# On hugin: snapshot the cluster DB and export new points.
docker exec -i munin-retrieval python - snapshot /data/chats.db - < $TOOL > cluster.snapshot.db
docker exec -i munin-retrieval python - qdrant-export --since <last-sync> --out - < $TOOL > points.jsonl
# On the Lite host, inside its retrieval container, after copying both in:
python /tmp/merge_state.py db --source cluster.snapshot.db --target /data/chats.db \
    --since <last-sync> --on-conflict newer-wins --apply
```

Deletions do not propagate in either direction: a conversation deleted on the
target after the source copy was taken comes back if it is in scope.

## Rehearsal (2026-11-12)

On copies of both databases, never production:

| Run | Result |
|---|---|
| Lite to cluster, dry run | 603 rows planned, 0 conflicts, 0 orphans |
| Lite to cluster, apply | 0.8 s; conversations 781 to 822, messages 5902 to 6039; FTS parity, 132/132 sampled words found |
| Same again | nothing to do, no backup taken |
| Read back through `chat_store`, `artifact_store`, `project_store`, `research_store`, `memory_proposals_store` (main and the live image) | 41/41 conversations load with the right message and artifact counts, 36/36 searchable by a word from their first user message, 1/1 project, 6/6 jobs, 14/14 proposals |
| Cluster to Lite, full | 11,644 rows; 5664/5664 sampled words found; re-run no-op |
| Qdrant, synthetic 840 points into 3000 (throwaway containers) | 1.8 s, 3840 after; re-run skips 840 identical |
| Files, synthetic 28 docs / 87 MB | 0.16 s; re-run no-op |

The cluster DB has one conversation with three assistant messages at the same
index (2026-08-27, the double-persist bug). The merge copies that as it is and
counts it as `source_duplicate_index_slots`; it only refuses on a slot taken in
the target.
