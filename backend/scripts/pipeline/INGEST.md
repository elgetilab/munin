# Paper ingest and cleanup — operator reference

Single entry point for everything ingest-related: how PDFs flow into
the corpus, what runs automatically, what the operator runs by hand.
Replaces six previous quick-reference docs.

For deeper context:
- [`PAPER_CRAWLER.md`](PAPER_CRAWLER.md) — crawler internals (citation
  harvesting, Sci-Hub, queue DB).
- [`../../docs/PAPER-INGEST-AUDIT.md`](../../docs/PAPER-INGEST-AUDIT.md)
  — the 2026-05-12 audit that introduced the ingest-time
  title-similarity guard and the `find-metadata-mismatch` tool.
- [`../../docs/PIPELINE-CONSOLIDATION-PLAN.md`](../../docs/PIPELINE-CONSOLIDATION-PLAN.md)
  — the in-progress consolidation (state machine, unified CLI). The
  end-state described there isn't fully shipped yet.

## What this directory is for

`backend/scripts/pipeline/` contains the cluster-side code that
turns PDFs into searchable corpus entries. The active pieces:

| File | Purpose |
|---|---|
| `paper_pipeline.py` | The ingest engine. PDF → GROBID → CrossRef → SPECTER → Qdrant + Neo4j. Run as `--single <pdf>` for one PDF, `--watch` as a long-running systemd service. |
| `paper_crawler.py` | Citation-based PDF acquisition. arXiv first, Sci-Hub fallback. Maintains a SQLite queue of pending downloads. |
| `paper_cleanup.py` | All cleanup, repair, detection, and remediation subcommands. |

## Ingest paths

Two ways PDFs enter the system. Both end up calling the same
`process_pdf()` core in `paper_pipeline.py`, but the wrapper around
them differs.

### Path A — Upload (`/api/admin/ingest`)

```
upload.muninai.org (tusd) → frontend/upload/hook_service → POST /api/admin/ingest
   ↓
/opt/munin/data/papers/pdf/inbox/<uuid>.pdf  +  <uuid>.contributor.json
   ↓
subprocess: paper_pipeline.py --single inbox/<uuid>.pdf
   ↓
on success: PDF moved to /papers/pdf/doi_<doi>.pdf, processed marker written
on quality fail / pipeline crash / null-DOI:
       PDF moved to /papers/pdf/quarantine/ + *.state.json sidecar
       describing what went wrong (Phase B of 2026-05-13 consolidation;
       legacy skipped/ and failed/ dirs migrated by Phase C)
```

### Path B — Crawler / operator drop (watcher)

```
paper_crawler.py crawl  →  /opt/munin/data/papers/pdf/doi_<doi>.pdf
(or operator manually drops a doi_*.pdf file in the same dir)
   ↓
munin-paper-pipeline.service polls every 60s, runs process_pdf
on any file without /opt/munin/data/papers/processed/<stem>.json
   ↓
on success: marker written to /papers/processed/, *.state.json
            written next to the PDF
on failure: PDF moved to /papers/pdf/quarantine/ + *.state.json
            (Phase B of 2026-05-13 consolidation: previously the
            watcher would leave failed PDFs in place and retry
            every poll; both ingest paths now quarantine symmetrically)
```

## Services and timers running automatically

Today, on hugin:

| Unit | Schedule | What it does |
|---|---|---|
| `munin-paper-pipeline.service` | always on | `--watch` loop, polls `/papers/pdf/` every 60s for unprocessed PDFs |
| `munin-paper-cleanup.timer` | 04:00 daily | Runs `paper_cleanup.py detect --kinds metadata-unverifiable --auto-quarantine --limit 20 --max-check 200`. Records no source can confirm are quarantined (soft action, reversible via `review`). |
| `munin-paper-reattribute.timer` | 04:30 daily | Runs `paper_cleanup.py reattribute`. Backfills group attribution after `contributors.yml` updates. |
| `munin-embedding-map.timer` | 01:30 daily | Rebuilds the 2D paper-embedding map + HDBSCAN clusters |

`munin-paper-cleanup` is the **only auto-remove path** that runs
without operator action. It caps at 20 removals per night to keep a
bad metadata-source day from emptying the corpus.

To inspect what's enabled:

```bash
systemctl list-timers 'munin-*'
systemctl status munin-paper-pipeline.service
journalctl -u munin-paper-pipeline.service -f          # tail the watcher
journalctl -u munin-paper-cleanup.service --since today
```

## Prerequisites

Both Path A (admin/ingest in retrieval container) and Path B (host
watcher) need:

- **Qdrant** running on `127.0.0.1:6333` (Docker, in
  `munin-qdrant` container)
- **Neo4j** running on `127.0.0.1:7687` (Docker,
  `munin-neo4j`). Authentication needs `NEO4J_PASSWORD` in the
  process env; `/opt/hugin/config/cluster.env` carries it.
- **GROBID** running on `127.0.0.1:8070` (Docker, `munin-grobid`)

Sanity check:

```bash
docker ps --filter "name=munin-" --format "{{.Names}}: {{.Status}}"
curl -sf http://127.0.0.1:8070/api/isalive    # GROBID
curl -sf http://127.0.0.1:6333/readyz         # Qdrant
```

## Common operator tasks

All commands below run as root (or via `sudo`) because the venv is
root-owned and the data dir requires elevated writes.

### Drop a PDF into the corpus manually

Name it `doi_<doi>.pdf` (with `/` replaced by `_`), place it in
`/opt/munin/data/papers/pdf/`, and the watcher picks it up within
60s. No restart needed.

### Process a single PDF on demand

```bash
sudo bash -c 'set -a && source /opt/hugin/config/cluster.env && set +a && \
    /opt/munin/services/pipeline/venv/bin/python3 \
    /opt/cluster/scripts/pipeline/paper_pipeline.py \
    --single /opt/munin/data/papers/pdf/doi_10.1234_example.pdf'
```

The `set -a` is mandatory: the pipeline needs `NEO4J_PASSWORD` (and
others) exported to the subprocess. Without it Neo4j auth fails
silently — that gotcha used to live in a separate `quick_fix_neo4j.md`.

### Crawl new citations

See [`PAPER_CRAWLER.md`](PAPER_CRAWLER.md) for the full surface.
Common ones:

```bash
sudo bash -c 'source /opt/hugin/config/cluster.env && \
    /opt/munin/services/pipeline/venv/bin/python3 \
    /opt/cluster/scripts/pipeline/paper_crawler.py crawl --max 100 --delay 10'
sudo .../paper_crawler.py add-seed arxiv:1706.03762
sudo .../paper_crawler.py status
```

### Inspect a DOI

```bash
sudo .../paper_cleanup.py verify-doi 10.1016/0021-9991(77)90112-7
```

Aggregates metadata from OpenAlex / Semantic Scholar / CrossRef and
prints what each source has. Useful when something looks wrong in
search results.

### Remove a single paper everywhere

```bash
sudo .../paper_cleanup.py remove --doi 10.1234/example                  # commits
sudo .../paper_cleanup.py remove --doi 10.1234/example --dry-run        # previews
sudo .../paper_cleanup.py remove --doi 10.1234/example --no-blocklist   # don't add to blocklist
```

Wipes Qdrant point + Neo4j paper node + SQLite queue row + PDF file.
Default behaviour adds the DOI to `blocklist.txt` so a future crawl
doesn't reintroduce it.

### Bulk remove

```bash
sudo .../paper_cleanup.py bulk-remove --file dois_to_drop.txt           # one DOI per line
```

### Detect bad / suspicious / orphaned records (`detect`)

Phase D of the 2026-05-13 consolidation merged five legacy
subcommands into one. The single entry point dispatches to per-kind
detectors and supports a soft `--auto-quarantine` action that moves
flagged records to `pdf/quarantine/` instead of deleting them.

```bash
# Title-vs-PDF mismatches (the 2026-05-12 audit's territory)
sudo .../paper_cleanup.py detect --kinds metadata-mismatch \
    --limit 100 --report-out /var/log/cluster-admin/mm.csv

# Same, auto-quarantine severity>=high
sudo .../paper_cleanup.py detect --kinds metadata-mismatch \
    --limit 100 --auto-quarantine

# Low-quality (OpenAlex check): short / retracted / no-abstract
sudo .../paper_cleanup.py detect --kinds low-quality \
    --max-check 500 --auto-quarantine --limit 50

# Short PDFs (page-count check)
sudo .../paper_cleanup.py detect --kinds short --min-pages 3 \
    --auto-quarantine --limit 20

# Metadata-unverifiable (multi-source: OpenAlex + S2 + CrossRef all fail)
sudo .../paper_cleanup.py detect --kinds metadata-unverifiable \
    --max-check 500 --auto-quarantine --limit 50

# Orphans (live Qdrant records whose PDF is gone from disk)
sudo .../paper_cleanup.py detect --kinds orphan --limit 100
```

Multiple kinds in one invocation:

```bash
sudo .../paper_cleanup.py detect \
    --kinds short,metadata-mismatch --auto-quarantine --limit 30
```

`--auto-quarantine` replaces the legacy destructive `--auto-remove`.
Quarantined records:
- Stay on disk (moved to `/opt/munin/data/papers/pdf/quarantine/`).
- Keep their Neo4j citation-graph node (citation links intact).
- Have their Qdrant point deleted (so paper_search doesn't surface them).
- Get a state sidecar describing the reason + audit findings.
- Are reviewed manually via `paper_cleanup.py review` (Phase E, planned).

### Review the quarantine queue (`review`)

Phase E (2026-05-13) introduced the human-in-the-loop step.
`detect --auto-quarantine` and the nightly timer move flagged
records into `pdf/quarantine/`; `review` walks them and prompts
keep / reject / skip per record.

```bash
sudo .../paper_cleanup.py review                       # interactive
sudo .../paper_cleanup.py review --limit 20            # cap session size
sudo .../paper_cleanup.py review --non-interactive     # print-only sweep
sudo .../paper_cleanup.py review --dry-run             # try without writing
```

Per-record menu:

```
  k  keep        Move PDF back to /papers/pdf/ + reset state;
                 watcher re-ingests through the hardened pipeline.
                 If the underlying issue persists, the record gets
                 re-quarantined and shows up again next pass.
  r  reject      Mark state=rejected. PDF stays in quarantine/ for
                 audit. Future detect runs skip this record.
  s  skip        Defer; leave state unchanged.
  o  open        xdg-open the PDF.
  d  details     Print the full state sidecar.
  ?  help        Print this menu.
  q  quit        Stop the session.
```

Decisions append a history entry to the state sidecar with
`via: "review"`, so the audit trail captures who triaged what when.

Quarantined records whose state is already `rejected` are NOT shown
again on subsequent `review` sessions; the operator only sees the
fresh queue.

### Removed subcommands (Phase E)

Six legacy entries were retired on 2026-05-13:

| Removed | Use instead |
|---|---|
| `find-low-quality` | `detect --kinds low-quality` |
| `find-short` | `detect --kinds short` |
| `find-metadata-mismatch` | `detect --kinds metadata-mismatch` |
| `repair-and-clean` | `detect --kinds metadata-unverifiable --auto-quarantine` |
| `repair-auto` | `detect --kinds metadata-unverifiable --auto-quarantine` |
| `scan-processed` | `detect --kinds orphan` |

Their underlying functions are still in the module (called by
`detect`), only the CLI surface is gone.

### Find PDFs whose stored title doesn't match their content (audit detail)

Added 2026-05-12 by the ingest audit. Paced 30s/GROBID call by
default; resume-safe via `_inspected_at` payload marker. See
[`../../docs/PAPER-INGEST-AUDIT.md`](../../docs/PAPER-INGEST-AUDIT.md)
for the full audit story. The `--queue-for-reingest` flag (distinct
from `--auto-quarantine`) is for re-running flagged records through
the hardened pipeline:

```bash
sudo .../paper_cleanup.py detect --kinds metadata-mismatch \
    --limit 100 --queue-for-reingest \
    --reingest-log /var/log/cluster-admin/reingest.csv
sudo .../paper_cleanup.py reingest-queue --limit 50 --pace 30
```

### Backfill contributors who got added to the allowlist late

Replaces the previous standalone `reattribute_unknown.py`. Walks
Qdrant for papers tagged `group_slug: "unknown"` whose uploader email
IS now in `config/contributors.yml`, and rewrites those entries with
the proper group + display fields.

```bash
sudo .../paper_cleanup.py reattribute --dry-run    # preview
sudo .../paper_cleanup.py reattribute              # commit
sudo .../paper_cleanup.py reattribute --no-neo4j   # Qdrant only, skip graph
```

Idempotent. Safe to re-run after every `contributors.yml` change.

## Quality filters at ingest time

The pipeline rejects papers whose metadata is incompatible with the
"real research paper" definition:

| Variable (env, in `cluster.env`) | Default | Effect |
|---|---|---|
| `MIN_PAGE_COUNT` | `3` | Reject PDFs with fewer pages |
| `REQUIRE_ABSTRACT_OR_REFS` | `true` | Reject if both abstract and reference list are empty |
| `OPENALEX_API_KEY` | (empty) | Optional; higher OpenAlex rate limit if set |
| `ADMIN_EMAIL` | `admin@example.com` | User-Agent for API calls (be polite) |

Editorials, retractions, single-page introductions, and book reviews
also get filtered out by title-pattern matching in `process_pdf()`.

## Where things land on disk

```
/opt/munin/data/papers/
├── pdf/                                live corpus, ~67k PDFs
│   ├── doi_*.pdf
│   ├── doi_*.state.json                Phase B state sidecar (live)
│   ├── inbox/                          admin/ingest staging
│   │   └── <uuid>.pdf + <uuid>.contributor.json
│   └── quarantine/                     Phase B unified quarantine
│       ├── <doi_*|uuid>.pdf
│       ├── <doi_*|uuid>.state.json     quarantine reasons + history
│       └── <doi_*|uuid>.contributor.json   (when from upload path)
├── processed/                          watcher "already seen" markers
├── logs/                               per-skip JSON logs (legacy)
├── blocklist.txt                       DOIs to refuse on future crawl
├── failed_downloads.txt                DOIs Sci-Hub gave up on
└── crawler_queue.db                    SQLite, the crawler's pending list
```

The legacy `skipped/` and `failed/` directories were merged into
`pdf/quarantine/` by `scripts/pipeline/migrate_quarantine_layout.py`
on 2026-05-13 (Phase C of the consolidation). New ingests go straight
into the new layout via `_dispose_post_pipeline`.

`/opt/munin/knowledge/` holds the §15 embedding-map output and the
notion-sync state.

## Troubleshooting

### Watcher service won't ingest a PDF I just dropped

Check if a stale processed-marker is shadowing it:

```bash
ls -la /opt/munin/data/papers/processed/doi_<your-doi>.json
```

If it exists, the watcher will skip the PDF. Either delete the
marker (the watcher will re-run the pipeline) or re-ingest manually
with `--single`.

### "Could not authenticate to Neo4j" when running manually

The cluster.env exports aren't reaching the Python process. Use the
`set -a && source && set +a` pattern shown earlier — without `set -a`,
the variables stay shell-local.

### GROBID 503 / "Could not get an engine from the pool"

GROBID has a finite engine pool (~10 by default). The watcher uses
`--workers 1` to leave headroom for the admin/ingest path. If you
manually run a high-concurrency pipeline (e.g. `--workers 4`) while
admin/ingest is also active, the pool saturates. Wait for in-flight
work to drain, then re-run.

If GROBID is sustained-down, see `docker compose logs grobid` in
`/opt/munin/docker/`.

### Sci-Hub returned the wrong PDF for a DOI

The 2026-05-12 audit identified this as the dominant crawler-side
metadata-corruption mechanism. The hardened pipeline now rejects the
Crossref enrichment when the GROBID-parsed title doesn't match the
Crossref title (`token-Jaccard < 0.3`), but the wrong DOI is still
stored.

Detect with `find-metadata-mismatch`, queue with `--queue-for-reingest`,
follow up with `reingest-queue`. See
[`../../docs/PAPER-INGEST-AUDIT.md`](../../docs/PAPER-INGEST-AUDIT.md).

## What's documented elsewhere

- Crawler internals (arXiv, Sci-Hub, citation harvesting, seed
  management): [`PAPER_CRAWLER.md`](PAPER_CRAWLER.md).
- 2026-05-12 ingest audit + the title-similarity guard:
  [`../../docs/PAPER-INGEST-AUDIT.md`](../../docs/PAPER-INGEST-AUDIT.md).
- The in-progress pipeline consolidation that will replace
  `skipped/` + `failed/` with a unified `quarantine/` and rebuild the
  cleanup CLI around a state-machine model:
  [`../../docs/PIPELINE-CONSOLIDATION-PLAN.md`](../../docs/PIPELINE-CONSOLIDATION-PLAN.md).
