# Pipeline consolidation plan (2026-05-13)

Follow-on to the 2026-05-12 ingest audit
([`PAPER-INGEST-AUDIT.md`](PAPER-INGEST-AUDIT.md)). The audit work
shipped hardening + a remediation tool, but the broader
`backend/scripts/pipeline/` directory has been growing organically
for months and the operator has lost the overview. This plan
consolidates the directory into a single coherent state model with
matching CLI surface, retires stale one-shot scripts, and makes both
ingest paths (upload + watcher) symmetric.

## Goals

1. **One state model, one CLI, two doors.** Every PDF carries a
   sidecar JSON tracking its lifecycle. Both ingest paths write the
   same sidecar. All cleanup tools read and update it.
2. **Self-cleaning where possible, manual review where not.** Records
   the hardened pipeline can validate land `live`; everything else
   goes to `quarantine/` with a reason. An interactive CLI walks the
   quarantine queue.
3. **No duplicate tools.** Today there are 12 cleanup subcommands
   that overlap pairwise. Target: 6 subcommands, each with a single
   well-defined job.
4. **No misplaced files.** `pipeline/` is for pipeline code only.
   Notion sync and RAG tool definitions move to their proper homes.
5. **No stale one-shots.** Scripts whose problem has been solved
   get deleted; periodic concerns (`reattribute_unknown.py`) become
   subcommands.

## Currently auto-running services and cron jobs

These run on hugin without operator action and produce most of the
state the cleanup tools have to reason about. They are the baseline
this plan must not break.

### Systemd services (always on)

| Service | What it runs | Purpose |
|---|---|---|
| `munin-paper-pipeline.service` | `paper_pipeline.py --watch --fast --workers 1 --dir /opt/munin/data/papers/pdf` | Polls `/papers/pdf/` every 60s; processes any PDF without a marker in `/papers/processed/`. The main ingest engine for crawler output + operator drops. |
| `deepresearch-daemon.service` | `deepresearch-daemon.py` (in `scripts/deepresearch/`) | Drives the deep-research SLURM job queue. Unrelated to paper ingest. |
| `munin-tunnel.service` | autossh reverse tunnel to VPS | Exposes retrieval API to the VPS over port 18080. Unrelated to ingest. |

### Systemd timers (periodic)

| Timer | Schedule | What it runs | Purpose |
|---|---|---|---|
| `munin-embedding-map.timer` | 01:30 daily | `build_embedding_map.py` | §15 nightly rebuild of the 2D paper-embedding map + HDBSCAN clusters. Reads Qdrant, writes `/opt/munin/knowledge/embedding_map.json`. |
| `munin-paper-cleanup.timer` | 04:00 daily | `paper_cleanup.py repair-and-clean --max-check 200 --auto-remove --limit 20` | Nightly metadata-quality sweep. Re-enriches up to 200 papers, auto-removes up to 20 whose metadata can't be verified by any of OpenAlex / Semantic Scholar / Crossref. **This is the only auto-remove path that currently runs.** |

### HuginSLURM cron jobs (cluster-level, in `/etc/cron.d/hugin-cluster`)

| Schedule | What runs | Purpose |
|---|---|---|
| Mon 04:00 | `reboot-warning.sh` | Broadcast 1h warning before the weekly reboot. |
| Mon 05:00 | `safe-reboot.sh` | Drain SLURM, reboot cluster. |
| Daily 06:00 | `schedule-vllm.sh start` | Submit vLLM SLURM job. |
| Daily 02:00 | `schedule-vllm.sh stop` | `scancel` the vLLM SLURM job (frees the GPU for batch). |
| Daily 03:00 | `check-hdd-health.sh` | SMART check + alert. |

None of the HuginSLURM cron jobs touch the paper corpus. Listed for
completeness.

### What the operator runs manually today

- `paper_crawler.py crawl --max N` — periodically, when new
  citations queue up. No timer; operator-driven.
- `paper_cleanup.py remove --doi X` — one-off cleanup.
- `paper_cleanup.py find-metadata-mismatch ...` — Stage 2 audit
  tool from 2026-05-12. No timer yet.
- `paper_cleanup.py reingest-queue ...` — drives the queue from
  the above. No timer yet.

## End-state vision

### Directory layout

```
/opt/munin/data/papers/
├── pdf/
│   ├── doi_*.pdf                       live corpus
│   ├── *.state.json          NEW       per-PDF state sidecar
│   ├── inbox/                          admin/ingest staging (unchanged)
│   └── quarantine/           NEW       replaces skipped/ + failed/
│       ├── doi_*.pdf
│       ├── *.state.json
│       └── *.contributor.json
├── processed/                          watcher markers (unchanged)
└── logs/                               run logs (unchanged)
```

### State sidecar (`*.state.json`)

Lives next to every PDF the pipeline has touched. Same JSON is
mirrored to the Qdrant payload as `_state`, `_quarantine_reasons`,
`_history` so `paper_search` and detection runs can filter
without walking disk.

```json
{
  "doi": "10.2307/411791",
  "state": "quarantine",
  "ingest_path": "upload",
  "quarantine_reasons": ["title_mismatch_with_crossref"],
  "detection_runs": ["audit-2026-05-12-14-22-19"],
  "first_seen_at": "2026-04-22T14:18Z",
  "last_inspected_at": "2026-05-13T11:30Z",
  "contributor": { "email": "...", "group_slug": "elgeti" },
  "audit_findings": {
    "grobid_title": "LIGPLOT: a program to generate...",
    "grobid_doi": "10.2307/411791",
    "jaccard_vs_stored": 0.0
  },
  "history": [
    { "at": "2026-04-22T14:18Z", "state": "live", "via": "upload" },
    { "at": "2026-05-13T11:30Z", "state": "quarantine", "reason": "title_mismatch_with_crossref" }
  ]
}
```

State values:
- `live`: in the corpus, searchable, no known issues.
- `quarantine`: out of the corpus (Qdrant point deleted), PDF
  preserved in `quarantine/`, needs human review.
- `rejected`: operator explicitly removed it (via `review` → reject
  or `remove --doi`). PDF kept for audit; not re-ingested.

### CLI surface (12 → 6 subcommands)

```
paper_cleanup.py
    inspect    <doi>          multi-source verify + show state + show sidecar
    detect     [--kinds ...]  unified detection: metadata-mismatch, low-quality, short, orphan
    review     [--limit N]    interactive CLI walk over quarantine/, keep/reject/reingest
    reingest   [--limit N]    paced reingest of `quarantine/` items the operator marked
    remove     <doi>          kill a paper everywhere (+ --file for batch)
    reattribute               retroactively assign contributors that became known later
```

Subcommands going away:

| Old | Replaced by |
|---|---|
| `bulk-remove` | `remove --file F` |
| `verify-doi` | `inspect <doi>` |
| `repair-and-clean` | `detect --kinds low-quality --auto-quarantine` |
| `repair-auto` | `detect --kinds metadata-mismatch,low-quality --auto-quarantine` |
| `find-low-quality` | `detect --kinds low-quality` |
| `find-short` | `detect --kinds short` |
| `find-metadata-mismatch` | `detect --kinds metadata-mismatch` |
| `reingest-queue` | `reingest` |
| `scan-processed` | folded into `detect` |

### Both ingest paths through one helper

New module-level function in `paper_pipeline.py`:

```python
def _dispose_post_pipeline(
    pdf_path: str,
    paper: Optional[Paper],     # None on failure
    error: Optional[str],       # quarantine reason, or None on success
    ingest_path: str,           # "upload" | "crawler"
) -> None:
    """Single source of truth for post-pipeline state transitions.
    Called by both admin/ingest and paper_pipeline.py --watch."""
```

Writes the state sidecar (live or quarantine), mirrors to Qdrant
payload, moves PDF to its final location (live in `/papers/pdf/` or
quarantine in `/papers/pdf/quarantine/`). The asymmetry where the
watcher leaves bad PDFs in place gets fixed for free.

## What gets retired

### Delete (one-shots whose job is done)

| File | Reason |
|---|---|
| `seed_processed_markers.py` | One-shot to seed the watcher's marker dir. Already run. |
| `qdrant_repair_sweep.py` | Narrower version of what `repair-auto` does; will be folded into `detect --kinds orphan`. |
| `REPAIR_AND_CLEAN_PLAN.md` | Describes work that shipped. |

### Move (not pipeline code)

| File | New home | Why |
|---|---|---|
| `notion_sync.py` | `backend/scripts/notion_sync.py` (sibling, not under `pipeline/`) | Separate concern. |

### Reclassified during Phase A — delete instead of move

`knowledge_tools.py` was originally listed for relocation to
`backend/retrieval/`, but it has zero non-self callers anywhere in
the tree. Its tool functions (`search_papers`, `find_citing_papers`,
`find_author_papers`, `get_paper_details`, `web_search`) are already
implemented in `retrieval/mcp/tools/`. It's dead legacy code from an
earlier function-calling design. Deleted in Phase A together with
the pipeline `README.md` (which only documents that dead code).

### Fold (was a script, becomes a subcommand)

| File | New home |
|---|---|
| `reattribute_unknown.py` | `paper_cleanup.py reattribute` |

### Consolidate docs (6 → 1)

Merge into one canonical `INGEST.md`:
- `instructions.md`
- `instructions_pipeline.md`
- `paper_quality_quickstart.md`
- `quick_reference.md`
- `quick_reference_cleaner.md`
- `quick_fix_neo4j.md`

The existing `PAPER_CRAWLER.md` stays separate (crawler is its own
tool with its own user surface). `README.md` stays — it covers the
broader RAG-tools concern, not just ingest.

## Future auto-running services

The plan adds **no new always-running services**. It changes one
existing timer and introduces one new timer:

### Changed: `munin-paper-cleanup.timer`

Today runs `paper_cleanup.py repair-and-clean --max-check 200
--auto-remove --limit 20`. After consolidation:

```
ExecStart=paper_cleanup.py detect \
    --kinds low-quality,metadata-mismatch \
    --max-check 200 \
    --auto-quarantine \
    --limit 20
```

Crucially, the verb changes from `--auto-remove` to
`--auto-quarantine`. Today's timer deletes records the metadata
sources can't verify. After consolidation it instead moves them to
`quarantine/` with `quarantine_reasons=["metadata_unverifiable"]`,
preserving the PDF and contributor sidecar so the operator can
review before final rejection. Net effect: the corpus stays the
same shape (those records leave search), but recovery from a bad
nightly run becomes a `review` step instead of a database restore.

### New: `munin-paper-reattribute.timer`

Light periodic backfill, equivalent to what `reattribute_unknown.py`
did when run manually:

```
OnCalendar=*-*-* 04:30:00
ExecStart=paper_cleanup.py reattribute --auto-confirm
```

Runs nightly 30 min after `paper-cleanup`. When a new contributor is
added to `contributors.yml`, this picks up any of their previously
"unknown"-tagged uploads. Today this requires the operator to remember
to run `reattribute_unknown.py` by hand.

### Not auto-running: `review`

The interactive review CLI deliberately stays manual. It's the
"human in the loop" step; auto-running it would defeat the point.
Operator runs it weekly (or whenever the quarantine queue grows
past a comfortable threshold).

### Not auto-running: `reingest`

The reingest of quarantine items also stays manual after the
operator triages via `review`. Auto-reingest could surface the same
bad records on repeat if the underlying GROBID extraction is
deterministic on the same PDF — better to gate behind explicit
operator action.

## Phase-by-phase implementation

Five commits, each independently revertable.

### Phase A — File tidy (this commit)

Mechanical, no behavior change. Lays the groundwork by making
`pipeline/` contain only pipeline code.

- Move `knowledge_tools.py` to `backend/retrieval/knowledge_tools.py`.
  Update any imports in the retrieval container.
- Move `notion_sync.py` to `backend/scripts/notion_sync.py`.
- Delete `seed_processed_markers.py` (one-shot, already run).
- Delete `qdrant_repair_sweep.py` (subsumed by detect/repair).
- Delete `REPAIR_AND_CLEAN_PLAN.md` (work shipped).
- Consolidate 6 quick-reference markdowns into one `INGEST.md`.
  The new doc documents today's pipeline (pre-state-machine)
  accurately; Phase B/C/D/E will update it incrementally as the
  consolidation lands.
- Fold `reattribute_unknown.py` into `paper_cleanup.py reattribute`
  subcommand. Delete the standalone script. New timer service
  (`munin-paper-reattribute.timer`) added but **not enabled yet**
  (Phase E enables it after the new CLI is settled).

Estimated effort: 2-3 hours.

Estimated risk: low. Mechanical moves; one new subcommand mirroring
existing behavior.

### Phase B — State sidecar + dispose helper

Introduce the state model itself. Forward-only: new ingests start
producing sidecars. Existing records get the sidecar lazy-backfilled
by `detect` (Phase D).

- New `_dispose_post_pipeline(pdf_path, paper, error, ingest_path)`
  in `paper_pipeline.py`. Both `admin/ingest` (in retrieval main.py)
  and `--watch` mode in `paper_pipeline.py` call it.
- Helper writes `*.state.json` next to the PDF.
- Same fields mirrored to Qdrant payload (`_state`,
  `_quarantine_reasons`, `_history`, `_audit_findings`).
- Both paths' existing "move PDF on success" + "move PDF on failure"
  logic gets centralized in the helper.

Estimated effort: 3-4 hours.

Estimated risk: medium. Touches both ingest paths; needs care to
preserve existing behavior on success.

### Phase C — Migration of `skipped/` + `failed/` → `quarantine/`

One-shot script (`scripts/pipeline/migrate_quarantine_layout.py`,
deleted after the migration runs). Idempotent.

- Walk current `skipped/` and `failed/`.
- For each PDF: parse the existing `skip_info.json` (already there)
  to reconstruct the equivalent `*.state.json`.
- Move PDF + sidecar into new `quarantine/`.
- Remove the empty `skipped/` and `failed/` directories at the end.

Estimated effort: 2 hours.

Estimated risk: medium. Touches on-disk state. The script runs in
`--dry-run` mode by default and prints what it would move; only the
operator's explicit `--commit` flag actually moves files.

### Phase D — Consolidate detection into `detect`

Behavior preserved, surface unified.

- New `detect` subcommand with `--kinds` flag taking a
  comma-separated list: `metadata-mismatch` (Stage 2),
  `low-quality` (today's `find-low-quality`), `short` (today's
  `find-short`), `orphan` (qdrant_repair_sweep's territory).
- Each kind has its own detector function; `detect` orchestrates
  them and writes a single unified state sidecar + a single CSV
  report.
- `--auto-quarantine` replaces today's `--auto-remove` semantics:
  flagged records get their state moved to `quarantine` rather
  than deleted.
- Existing subcommands (`find-low-quality`, `find-short`,
  `find-metadata-mismatch`, `repair-and-clean`, `repair-auto`,
  `scan-processed`) marked deprecated but still functional; remove
  in Phase E.
- `munin-paper-cleanup.timer` updated to call the new form.

Estimated effort: 3-4 hours.

Estimated risk: low. Mostly wiring around existing detection logic.

### Phase E — Interactive `review` + final retirements

The piece that makes the loop closeable.

- New `review` subcommand. Walks `quarantine/`, opens each PDF
  via `xdg-open` (or prints path if no display), shows the stored
  title + GROBID-extracted title + quarantine reasons, prompts:
  - **k** keep (state → live, Qdrant point re-created from sidecar)
  - **r** reject (state → rejected, PDF stays in `quarantine/` for
    audit, Qdrant stays empty)
  - **i** reingest (move PDF to `inbox/<uuid>.pdf` with sidecar so
    the watcher / `reingest` picks it up)
  - **s** skip (defer decision)
- Decision history appended to the sidecar's `history[]`.
- Retire the deprecated subcommands from Phase D.
- Enable `munin-paper-reattribute.timer`.
- Update `INGEST.md` to describe the final state.

Estimated effort: 3-4 hours.

Estimated risk: low. Additive subcommand; no breaking changes
beyond the deprecations.

## Total

5 commits, ~14-17 hours of focused work, 2-3 working days. Each
phase can ship independently. Reversal is straightforward: revert
the commit; the prior phase still works.

## Out of scope (deliberately deferred)

- **Web UI for review.** CLI is enough for current corpus size +
  operator. Revisit if the quarantine queue ever stays >50 entries
  for more than a week.
- **Auto-reject after N days in quarantine.** Risky without seeing
  usage patterns. Hold until the manual `review` step shows what
  the operator actually rejects vs keeps.
- **Crawler-side hardening (Sci-Hub source verification).** The
  audit found ~6-8% mismatch on crawler downloads. The hardened
  pipeline (Stage 1 of the audit) catches the worst cases at
  ingest; deeper fix would be a content-vs-DOI fingerprint check
  which isn't trivial.
- **Multi-PDF-per-DOI handling.** Today the keying assumes one PDF
  per DOI. Some users upload research papers + supplementary
  briefings under the same DOI; we treat the second one as
  duplicate. Could be a state-machine extension later.
