# Paper Pipeline Automation — Operator Reference

Two systemd units keep the paper corpus growing and healthy without
operator SSH sessions:

- **`munin-paper-pipeline.service`** — watcher daemon that ingests
  any PDF dropped into `/opt/munin/data/papers/pdf/` through the
  full GROBID → CrossRef → SPECTER → Qdrant + Neo4j pipeline.
- **`munin-paper-cleanup.timer`** + `.service` — nightly
  `repair-and-clean` sweep that re-enriches metadata and removes
  papers that no source can confirm.

Both run as `root` on the cluster head from a dedicated venv at
`/opt/munin/services/pipeline/venv`. Neither touches vLLM, so they
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

`paper_cleanup.py repair-and-clean` walks a batch of papers (default
200 per run), re-fetches metadata from OpenAlex / Semantic Scholar /
CrossRef, and updates the Neo4j `Paper` node + Qdrant payload with
fresh data. If **all three** sources fail to confirm the paper
exists (wrong DOI, retracted, never-existed), the paper is removed
from Qdrant + Neo4j + the PDF on disk — capped at 20 removals per
run so a bad-source day can't nuke the corpus.

- `--max-check 200` — papers reviewed per run
- `--auto-remove` — enables the removal path
- `--limit 20` — hard cap on removals
- Walk order: age of the last enrichment. Stale papers first.

Runtime: ~5-10 min at default settings. §27 parallelization (on the
reliability backlog) will take the full-corpus sweep from 8 h → 2-4 h;
at that point we can crank `--max-check` way up without exceeding the
nightly window.

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
4. Installs + daemon-reloads + enables + restarts both systemd
   units.

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

### Trigger cleanup on-demand

```bash
# Run cleanup now (service is a oneshot; you run it, it exits)
sudo systemctl start munin-paper-cleanup.service
sudo journalctl -fu munin-paper-cleanup.service

# When is the next scheduled run?
systemctl list-timers munin-paper-cleanup.timer
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
curl -s http://127.0.0.1:6333/collections/papers | jq '.result.points_count'
# Sample twice over a minute — if growing, ingest is working.
```

### Cleanup log

```bash
sudo journalctl -u munin-paper-cleanup.service --since "yesterday"
```

Expect one block per night. "Ingested 0, updated 187, removed 3"
or similar at the end is normal. Many more removals than usual
flags a data-quality incident upstream (OpenAlex outage, bulk
API migration) — investigate before assuming papers were bad.

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
- **§27 paper_cleanup parallelization** (reliability backlog) will
  speed up the nightly sweep. Until then, `--max-check 200 --limit 20`
  keeps per-run time bounded.

## Tunables (all in the `.service` unit via `Environment=`)

Watcher:
| Var | Default | What |
|---|---|---|
| `WATCH_POLL_SECS` | `60` | Seconds between `pdf/*.pdf` scans. Min 1. |
| `QDRANT_HOST` / `QDRANT_PORT` | 127.0.0.1 / 6333 | Vector DB |
| `NEO4J_URI` | `bolt://127.0.0.1:7687` | Graph DB |
| `NEO4J_PASSWORD` | (cluster.env) | Graph DB auth |
| `GROBID_URL` | `http://127.0.0.1:8070` | PDF parser |
| `ADMIN_EMAIL` | `admin@muninai.org` | UA string for polite pools on OpenAlex / CrossRef |
| `SEMANTIC_SCHOLAR_API_KEY` | (cluster.env) | S2 enrichment rate |

Cleanup: same as watcher. Adjust `--max-check` / `--limit` by
editing `munin-paper-cleanup.service` and `systemctl daemon-reload`.

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
- **Cleanup cadence assumes serial run time.** Once §27 parallelizes,
  bump `--max-check` in the service file and watch the run time.
- **The watcher never cleans up its markers.** A processed marker
  for a deleted paper lingers forever; harmless (just skips a
  non-existent PDF) but consumes inode. Add a periodic broom if
  ever a concern — not today.
