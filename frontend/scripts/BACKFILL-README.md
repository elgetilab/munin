# VPS Backfill — Operator Instructions

Companion doc for `backfill_contributed.py`. Walks you through the
one-time §28 migration: push the ~4,235 PDFs already sitting at
`/mnt/uploads/complete/<email>/` on the VPS through the cluster's
ingest endpoint so they become searchable with contributor
attribution (`#zeitler`, `#corzilius`, `#deibel`).

Full backend design lives in `shared/docs/CONTRIBUTOR-INGEST.md` and
`backend/docs/archive/VPS-BACKFILL-HANDOFF.md`. You
don't need to read those to run this script — this README is
self-contained.

## 0. Where things run

| Component | Host | Role |
|---|---|---|
| This script | **VPS** (`<vps-host>`) | File shuttler — walks `/mnt/uploads/complete/` and POSTs each PDF |
| `/api/admin/ingest` endpoint | Cluster (`hugin`) | Receives the PDF, runs GROBID → BGE-large → Qdrant (`papers_bge`) + Neo4j |
| Autossh tunnel | Bridge | Already up; exposes cluster `:8080` at VPS `127.0.0.1:18080` |

Nothing model-related runs on the VPS. The script does plain HTTP
POSTs and waits for the response. Concurrency is 1 by design — the
cluster's GROBID + encoder (BGE-large) are the bottleneck, and cranking VPS
parallelism just creates an HTTP backlog.

## 1. Prerequisites

On the VPS you need:

- SSH/sudo access (root or a user who can write `/usr/local/bin/` and
  `/var/log/`, and read `/mnt/uploads/`).
- Python 3.9+ (check with `python3 --version`).
- `requests` library:
  ```bash
  sudo apt-get install -y python3-requests   # preferred
  #  or
  python3 -m pip install --user requests     # if apt package isn't available
  ```
- The **admin ingest token** (64-char hex). Pull it off the cluster:
  ```bash
  # SSH from VPS to hugin (or whoever has hugin access copies it to you)
  ssh <user>@hugin "sudo grep '^ADMIN_INGEST_TOKEN=' /opt/hugin/config/cluster.env"
  ```
- Working autossh tunnel. Sanity check:
  ```bash
  curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:18080/api/status
  # Must be 200. If not, the tunnel is down — fix that first.
  ```

## 2. Install

From your dev workstation:

```bash
scp backfill_contributed.py <user>@<vps-host>:/tmp/
scp BACKFILL-README.md <user>@<vps-host>:/tmp/
ssh <user>@<vps-host>
```

On the VPS:

```bash
sudo cp /tmp/backfill_contributed.py /usr/local/bin/
sudo chmod +x /usr/local/bin/backfill_contributed.py
# The script logs to /var/log/backfill-uploads.log by default; create
# the file so logging works without root on subsequent runs.
sudo touch /var/log/backfill-uploads.log
sudo chown root:adm /var/log/backfill-uploads.log
```

## 3. Set the token

The script reads `ADMIN_INGEST_TOKEN` from the environment. Use `-E`
with sudo so the variable survives the privilege escalation:

```bash
export ADMIN_INGEST_TOKEN=<paste the 64-char hex from the cluster>
# Confirm it's set and 64 chars long
echo "${#ADMIN_INGEST_TOKEN}"   # expect 64
```

Never commit the token anywhere. It's the machine-to-machine secret
gating the ingest endpoint.

## 4. Smoke test — 3 papers

Tiny probe against one real user. Verifies the endpoint is reachable,
auth works, and the pipeline finishes end-to-end.

```bash
sudo -E /usr/local/bin/backfill_contributed.py \
    --email contributor-c@example.org \
    --limit 3
```

Expected output:

```
2026-04-20 16:24:03 INFO  backfill starting: 418 pending, concurrency=1, timeout=900s, dry_run=False, limit=3
2026-04-20 16:24:03 INFO  filter: email=contributor-c@example.org
2026-04-20 16:24:15 INFO  [1/418] rate=0.08/s eta=85.3min OK contributor-c@example.org <- paper_a.pdf (12.2s, doi=10.1038/...)
2026-04-20 16:24:27 INFO  [2/418] rate=0.08/s eta=85.6min OK contributor-c@example.org <- paper_b.pdf (11.8s, doi=10.1126/...)
2026-04-20 16:24:45 INFO  [3/418] rate=0.08/s eta=83.0min OK contributor-c@example.org <- paper_c.pdf (18.3s, doi=10.1016/...)
2026-04-20 16:24:45 INFO  hit --limit 3, stopping early
2026-04-20 16:24:45 INFO  done. ingested=3 skipped=0 failed=0 elapsed=0.7min
```

- `OK` — paper is in Qdrant + Neo4j, searchable.
- `SKIP` — pipeline ran but quality-filtered the paper (non-research
  content, empty title, etc.). Still moved to `processed/` because
  "the cluster has seen it".
- `FAIL HTTP503:saturated` — cluster's ingest pipeline is at its
  concurrency cap (`INGEST_CONCURRENCY`, default 4). Script sleeps
  for the response's `Retry-After` seconds (clamped 5-600), leaves
  the file in `complete/` for the next pass. **Not an error — this
  is graceful backpressure.** Expect occasional bursts during
  heavy upload activity.
- `FAIL` (other) — HTTP non-2xx, timeout, or network error. File
  stays in `complete/` so you can re-run later.

If the 3 probes all succeed, you're clear to proceed.

## 5. Dress rehearsal — full Zeitler mailbox (418 files)

```bash
sudo -E /usr/local/bin/backfill_contributed.py \
    --email contributor-c@example.org
```

Expected duration: 2-4 hours at concurrency 1 (per-paper time
dominated by GROBID + the encoder, typically 10-30 s each).

Progress is written to both stdout and `/var/log/backfill-uploads.log`.
Tail it from a second SSH session:

```bash
tail -f /var/log/backfill-uploads.log
```

You can safely `Ctrl+C` and restart — the script is resumable
(successful papers have already been moved out of `complete/`).

## 6. Full backfill — the other two groups

Once Zeitler is done and looks clean:

```bash
sudo -E /usr/local/bin/backfill_contributed.py --all
```

`--all` processes every non-admin mailbox sequentially. Admin
`admin@example.org` is hard-coded in `ADMIN_SKIPLIST` and skipped.

Remaining files:
- `contributor-a@example.org` — 1,819 PDFs (~3.0 GB)
- `contributor-b@example.org` — 1,998 PDFs (~7.6 GB)

Expected total duration: 12-24 hours at concurrency 1. Run inside
`tmux` or `screen` so an SSH disconnect doesn't kill it:

```bash
tmux new -s backfill
# run the command
# detach with Ctrl-b d
# reattach: tmux attach -t backfill
```

## 7. Monitor progress (from the cluster or anywhere with access)

Tag catalog reflects the state of the corpus in real-time. Run from
the cluster or anywhere that can reach the retrieval service:

```bash
# On hugin — paper counts per group tick up
watch -n 30 'curl -s http://127.0.0.1:8080/api/tags \
    | jq ".groups[] | {slug, paper_count}"'
```

Output should march upward:

```
{"slug": "zeitler",    "paper_count": 120}
{"slug": "corzilius",  "paper_count": 0}
{"slug": "deibel",     "paper_count": 0}
```

## 8. When it's done

On the cluster, trigger a one-off §15 rebuild so the ~4,235 new
papers get their cluster / topic assignments (the next nightly run
at 03:00 local would catch them otherwise):

```bash
# On hugin:
sudo systemctl start munin-embedding-map.service
sudo journalctl -fu munin-embedding-map.service   # watch until Deactivated
```

Sanity-check a real group browse after the rebuild:

```bash
# Any host that can reach the retrieval API
curl -s 'http://127.0.0.1:8080/api/tags/group/corzilius/papers?limit=5' | jq .
```

## 9. Troubleshooting

### Every POST returns HTTP 401

Token mismatch. Re-export `ADMIN_INGEST_TOKEN` and re-run. Remember
`sudo -E` preserves env variables; without `-E`, sudo strips them.

### Every POST returns HTTP 503 `Admin ingest not configured`

The **cluster** side doesn't have `ADMIN_INGEST_TOKEN` set in its
environment. Fix on hugin:

```bash
# On hugin
sudo grep '^ADMIN_INGEST_TOKEN=' /opt/hugin/config/cluster.env
# If missing or empty, add a line. Then:
sudo ./deploy.sh retrieval    # rebuilds container so env reloads
```

### Connection refused / 000 / network errors

The autossh tunnel is down. On hugin:

```bash
sudo systemctl status munin-tunnel.service
sudo systemctl restart munin-tunnel.service
```

Re-run the backfill — it picks up where it left off.

### Lots of HTTP 504 timeouts

GROBID is backed up. Likely the cluster took a restart or GROBID is
OOM'ing on an unusual PDF. Options:

- Pause the backfill (Ctrl+C), check GROBID health on hugin:
  `docker logs --tail 50 munin-grobid`
- Let it keep running — failures stay in `complete/` and you can
  re-run the script after fixing GROBID.

### Lots of `SKIP` with reason "no Qdrant point"

The pipeline's quality filter is rejecting papers. Common causes:

- Scanned / image-only PDFs (no OCR path).
- Papers with no DOI that also have sparse metadata.
- Very short editorial / errata content.

Check the `log_tail` in the log file for the specific reason. These
still move to `processed/` — they're "seen", just not indexed.

### The script crashes mid-run

All successful work is already committed (files moved to
`processed/`). Re-run the same command — it picks up the remainder
automatically. Check `/var/log/backfill-uploads.log` for the final
state of the previous run.

## 10. Configuration reference

Environment variables (all optional except `ADMIN_INGEST_TOKEN`):

| Var | Default | Purpose |
|---|---|---|
| `ADMIN_INGEST_TOKEN` | _unset_ | **Required.** Bearer token shared with cluster |
| `CLUSTER_INGEST_URL` | `http://127.0.0.1:18080/api/admin/ingest` | Override if tunnel lives elsewhere |
| `PIPELINE_TIMEOUT` | `900` | Per-paper HTTP timeout in seconds |
| `COMPLETE_DIR` | `/mnt/uploads/complete` | Inbox walked by the script |
| `PROCESSED_DIR` | `/mnt/uploads/processed` | Success destination |
| `LOG_PATH` | `/var/log/backfill-uploads.log` | File log (stdout always goes to terminal) |

CLI flags (mutually exclusive between `--email` / `--all`):

| Flag | Purpose |
|---|---|
| `--email <addr>` | Only this uploader's files |
| `--all` | Every non-admin user under `COMPLETE_DIR` |
| `--dry-run` | Walk and log, don't POST, don't move |
| `--limit N` | Stop after N successful ingests (smoke tests) |

## 11. What happens to future uploads

This script is a **one-time migration**. Once the 4,235 existing
files are in, the long-term flow is different:

```
user → Uppy → tusd → /mnt/uploads/staging
                         ↓ post-finish
                 hook_service.py moves → /mnt/uploads/complete/<email>/
                         ↓ hook_service POSTs (new code, not yet live)
                 /api/admin/ingest → pipeline → Qdrant + Neo4j
                         ↓ 200 OK
                 hook_service moves → /mnt/uploads/processed/<email>/
```

The archived `backend/docs/archive/VPS-BACKFILL-HANDOFF.md` carries the
`hook_service.py` patch sketch (a ~30-line `push_to_cluster` helper
that does exactly what this script does, but one paper at a time as
they arrive). That's the piece to land on the VPS side after the
one-time backfill is done — or in parallel, in which case new
uploads go straight to the cluster while this script drains the
historical backlog.
