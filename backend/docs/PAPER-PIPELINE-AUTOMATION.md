# Paper Pipeline Automation — Operator Reference

**Note (2026-05-15):** the operator cheatsheet now lives at
[`../scripts/pipeline/INGEST.md`](../scripts/pipeline/INGEST.md).
This file remains as deep automation reference for the watcher
daemon's ingestion flow specifically. For "what runs when, what do
I run by hand" start with INGEST.md.

Three systemd services + one timer keep the paper corpus growing
and healthy without operator SSH sessions:

- **`munin-paper-pipeline.service`** — watcher daemon that ingests
  any PDF dropped into `/opt/munin/data/papers/pdf/` through the
  full GROBID → CrossRef → BGE-large → Qdrant (`papers_bge`) + Neo4j pipeline. The
  state sidecar + Qdrant payload mirror are written by
  `_dispose_post_pipeline` (Phase B of the 2026-05-13 consolidation);
  failures land in `pdf/quarantine/` instead of being retried every
  poll.
- **`munin-paper-detect.service`** — Phase F continuous detection
  daemon. Runs `paper_cleanup.py sweep` in a paced loop (15-min
  cycles, 5 records/kind/cycle) over four detection kinds. Replaced
  the nightly `munin-paper-cleanup.timer` so cleanup runs as a
  continuous trickle rather than a once-a-day batch. Since 2026-10-04
  it runs `sweep --no-quarantine` (detection only) until the pending
  DOI filename repair is applied; see the comment in
  `backend/config/munin-paper-detect.service`.
- **`munin-paper-reattribute.timer`** + `.service` (04:30 daily) —
  backfills group attribution after `contributors.yml` updates.

All run as `root` on the cluster head from a dedicated venv at
`/opt/munin/services/pipeline/venv`. None touch vLLM, so they
run safely 24/7 including the 02:00-06:00 vLLM-down window.

## Ingestion flow with the daemon

```
                    ┌─────────────────────────────────┐
                    │ /opt/munin/data/papers/pdf/     │
                    │ (main PDF home)                 │
                    └───────────┬─────────────────────┘
                                │
               ┌────────────────┼─────────────────────┐
               │                │                     │
     ┌─────────▼─────────┐  ┌──▼────────────┐  ┌─────▼───────────┐
     │ /api/admin/ingest │  │ manual scp /  │  │ future crawler  │
     │ (VPS contributor) │  │ operator drop │  │ (not yet live)  │
     └─────────┬─────────┘  └──┬────────────┘  └─────┬───────────┘
               │               │                      │
               │ pdf/inbox/    │                      │
               │ +sidecar      │                      │
               ▼               │                      │
      paper_pipeline.py        │                      │
      --single (subprocess)    │                      │
               │               │                      │
               │ writes marker │ no marker            │ no marker
               │ + moves pdf   │                      │
               ▼               ▼                      ▼
           pdf/doi_X.pdf + processed/doi_X.json       (nothing yet)
                            │
                            │  watcher polls every WATCH_POLL_SECS (60s default)
                            ▼
            munin-paper-pipeline.service: for each *.pdf in
            pdf/ without a marker, run paper_pipeline.process_pdf
            → writes marker, leaves pdf in place
```

The **processed marker** (`/opt/munin/data/papers/processed/doi_X.json`)
is the single source of truth for "this PDF has been ingested".
`/api/admin/ingest` now writes one alongside the successful move,
so the watcher doesn't re-ingest contributor uploads.

Papers that arrive via manual `scp` or (future) crawler drop into
`pdf/` get picked up by the watcher on its next 10-second poll.

## What the cleanup does

The old nightly `paper_cleanup.py repair-and-clean` run (and its
`munin-paper-cleanup.{service,timer}` units) was retired in Phase F
of the 2026-05-13 consolidation; `deploy.sh pipeline` disables and
deletes those units if it finds them. Cleanup is now
`paper_cleanup.py sweep`, run continuously by
`munin-paper-detect.service`: a paced loop over `detect` for the
kinds in `DETECT_KINDS` (`metadata-mismatch`, `short`, `orphan`,
`metadata-unverifiable`), 5 records per kind every 15 minutes.
Flagged records are quarantined (moved to `pdf/quarantine/`, triaged
with `paper_cleanup.py review`), never deleted, and with
`--no-quarantine` (the current setting) they are only reported.
Details and tunables: [`../scripts/pipeline/INGEST.md`](../scripts/pipeline/INGEST.md).

## Deploy

```bash
sudo ./deploy.sh pipeline
```

This mode now:

1. Syncs `paper_pipeline.py`, `paper_cleanup.py`, `requirements.txt`
   to `/opt/cluster/scripts/pipeline/`.
2. Creates `/opt/munin/services/pipeline/venv/` if missing and
   installs requirements (`qdrant-client`, `neo4j`,
   `sentence-transformers`, `torch`, `requests`, `pypdf`).
3. Creates on-disk directories: `pdf/inbox/`, `pdf/skipped/`,
   `pdf/failed/`, `papers/processed/`.
4. Installs + daemon-reloads + enables + restarts the watcher and
   detect services, arms `munin-paper-reattribute.timer`, and removes
   the retired `munin-paper-cleanup` units if present.

Idempotent on re-run. Safe to re-run after pulling updates.

## Operate

### Watch the daemon

```bash
# Follow live
sudo journalctl -fu munin-paper-pipeline.service

# One-shot: last 100 lines
sudo journalctl -u munin-paper-pipeline.service -n 100 --no-pager

# Is it actually alive?
systemctl status munin-paper-pipeline.service
```

### Pause / resume / restart

```bash
# Pause for a bit (e.g. during manual admin work)
sudo systemctl stop munin-paper-pipeline.service

# Resume
sudo systemctl start munin-paper-pipeline.service

# Disable across reboots (rare — the point of this feature is to
# run forever)
sudo systemctl disable munin-paper-pipeline.service
```

### Trigger a detection cycle on-demand

```bash
# One sweep cycle by hand, detection only
sudo /opt/munin/services/pipeline/venv/bin/python3 \
    /opt/cluster/scripts/pipeline/paper_cleanup.py sweep --once --no-quarantine

# The daemon itself
systemctl status munin-paper-detect.service
```

### Manual drop → watched ingest

Drop a PDF into the main dir and wait up to `WATCH_POLL_SECS`
(default 60 s):

```bash
sudo cp ~/some-paper.pdf /opt/munin/data/papers/pdf/
# Wait up to 60s (poll) + pipeline runtime (~20s) then:
sudo ls /opt/munin/data/papers/processed/ | grep -F some-paper
# If you see a .json marker, it was ingested. Log confirms:
sudo journalctl -u munin-paper-pipeline.service --since "2 min ago"
```

## Health checks

### Qdrant growing?

```bash
curl -s http://127.0.0.1:6333/collections/papers_bge | jq '.result.points_count'
# Sample twice over a minute — if growing, ingest is working.
```

### Detection log

```bash
sudo journalctl -u munin-paper-detect.service --since "yesterday"
```

Expect one block per 15-minute cycle. A sudden jump in flagged
records usually means a data-quality incident upstream (OpenAlex
outage, bulk API migration): investigate before assuming the papers
were bad.

### What's in the three inbox siblings right now?

```bash
ls /opt/munin/data/papers/pdf/inbox/   | wc -l   # in-flight admin-ingest
ls /opt/munin/data/papers/pdf/skipped/ | wc -l   # quality-filtered
ls /opt/munin/data/papers/pdf/failed/  | wc -l   # pipeline errors
```

Steady-state inbox count should be 0-2 (papers currently being
processed by /api/admin/ingest). If it grows, the admin-ingest
endpoint is having trouble. Skipped + failed grow slowly over
time; inspect with `jq .reason {uuid}.skip_info.json`.

## Known interactions

- **`/api/admin/ingest`** writes a processed marker after success so
  the watcher skips. If someone bypasses the admin-ingest path and
  drops a PDF directly into `pdf/`, the watcher picks it up on its
  next poll.
- **Nightly §15 embedding-map** runs at 01:30. New papers ingested
  earlier in the night get cluster assignments that same night.
  Papers ingested after 01:30 wait until the next night.

## Tunables (all in the `.service` unit via `Environment=`)

Watcher:
| Var | Default | What |
|---|---|---|
| `WATCH_POLL_SECS` | `60` | Seconds between `pdf/*.pdf` scans. Min 1. |
| `QDRANT_HOST` / `QDRANT_PORT` | 127.0.0.1 / 6333 | Vector DB |
| `NEO4J_URI` | `bolt://127.0.0.1:7687` | Graph DB |
| `NEO4J_PASSWORD` | (cluster.env) | Graph DB auth |
| `GROBID_URL` | `http://127.0.0.1:8070` | PDF parser |
| `ADMIN_EMAIL` | none in the unit; set in cluster.env. `paper_pipeline.py` and `paper_cleanup.py` fall back to `MUNIN_CONTACT_EMAIL`, and to the placeholder `admin@example.com` only if neither is set | mailto in the User-Agent for polite pools on OpenAlex / CrossRef |
| `SEMANTIC_SCHOLAR_API_KEY` | (cluster.env) | S2 enrichment rate |

Detect daemon: same connection settings, plus `DETECT_KINDS`,
`DETECT_PER_CYCLE_LIMIT`, `DETECT_CYCLE_PACE_SECS` and
`DETECT_GROBID_PACE_SECS` in `munin-paper-detect.service`; edit, then
`systemctl daemon-reload` and restart the service.

## Known limitations

- **60-second poll interval** (default) is env-configurable via
  `WATCH_POLL_SECS`. Drop it to 10 during a big batch session;
  raise it to 300+ on a mostly-idle corpus to save cycles.
  Requires a `systemctl restart munin-paper-pipeline.service` after
  editing the unit file to take effect.
- **No concurrent watcher instances.** `Restart=on-failure`
  prevents double-firing, but if you manually `./paper_pipeline.py
  --watch` as well, you'll get race conditions on the markers.
  Don't.
- **The watcher never cleans up its markers.** A processed marker
  for a deleted paper lingers forever; harmless (just skips a
  non-existent PDF) but consumes inode. Add a periodic broom if
  ever a concern — not today.
