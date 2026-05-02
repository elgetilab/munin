# Contributor-Corpus Ingestion + Tag-Scoped Search — Plan

Sprint plan for bringing curated user-uploaded paper corpora into the
shared `papers` knowledge base, with contributor attribution and
tag-scoped retrieval.

Full design lives in `docs/future_features.md`:
- §15 Paper-embedding 2D map with clustering (lines 1497-1590)
- §28 Tag-scoped knowledge with contributor attribution (lines
  3416-3904)

This file is the agreed slice of those, with sequencing decisions.

## Status

- **§15 — SHIPPED 2026-04-20.** Nightly embedding map + clustering is
  live. See `docs/KNOWLEDGE-MAP.md` for operator reference. First
  build: 29,893 papers → 213 clusters, payload write-back confirmed,
  `/api/embedding_map` endpoint serving real data.
- **§28 Sprint A (ingestion path) — SHIPPED 2026-04-20.** See
  `docs/CONTRIBUTOR-INGEST.md` for operator reference. `POST
  /api/admin/ingest` with bearer-token auth, `paper_pipeline.py`
  reads contributor sidecars, DOI-keyed Qdrant points, `:Contributor`
  nodes + `[:CONTRIBUTED]` relationships in Neo4j, smoke-tested
  end-to-end on a real paper (Zeitler group attribution verified on
  both stores). **VPS side outstanding**: `hook_service.py` update
  to POST to the endpoint + one-time backfill of the 4,236 existing
  files at `/mnt/uploads/complete/<email>/`.
- **§28 Sprint B (tag-scoped search) — SHIPPED 2026-04-20.** See
  `docs/TAG-SCOPED-SEARCH.md` for operator reference. `#topic`,
  `#group`, `#@username` tag chips supported end-to-end:
  `GET /api/tags` catalog live (213 topics from §15 + 3 groups + 3
  contributors from allowlist), `current_query_tags` ContextVar
  flows from `/api/chat/completions` body → `chat_service` →
  `paper_search` → Qdrant `must` filters. Payload indexes created
  on startup for `contributors[].{group_slug,username,email}`,
  `topic_slug`, `cluster_id`. `paper_search` results now surface
  `contributors[]` + `topic` + `applied_tags`; persona prompts
  instruct the model to credit `group_display_name` in citations
  and acknowledge scope. End-to-end smoke-tested.

## What remains (not in this repo)

- **VPS-side hook**: `hook_service.py` update to POST each
  newly-landed file to `http://127.0.0.1:18080/api/admin/ingest`
  with the shared bearer token; move `complete/<email>/` →
  `processed/<email>/` on 200.
- **VPS-side backfill**: one-time script to walk
  `/mnt/uploads/complete/` and POST every existing file (~4,236
  across 3 non-admin uploaders; admin's 1 test file skipped).
- **Frontend**: `#tag` composer chip parsing, autocomplete against
  `GET /api/tags`, render `contributors` + `applied_tags` in paper
  cards. Lives in munin-vps.

## Agreed decisions (2026-04-20)

- **Sequence**: §15 first, §28 after. §15 is the prerequisite for
  `#topic` tags — no manual topic tagging as a bridge.
- **Upload surface**: `upload.muninai.org` uses **tusd** (resumable)
  + `hook_service.py` post-finish webhook. Finished files land on
  VPS at `/mnt/uploads/complete/<email>/`. 4,236 files already
  waiting (~13 GB, 4 users). Plain PDFs only — Uppy restricts
  upload to PDF, so no ZIP handling needed on either side.
- **Routing**: every file that arrives via upload.muninai.org is
  **shared-corpus-bound** (→ `papers` collection, SPECTER embedding,
  Neo4j citation graph). The per-user private `user_docs` collection
  is reserved for chat-attachment uploads and is not touched by this
  sprint.
- **Transport VPS → cluster**: HTTP push over the existing autossh
  tunnel. `hook_service.py` POSTs each finished file to
  `http://127.0.0.1:18080/api/admin/ingest` with bearer-token auth
  (`ADMIN_INGEST_TOKEN`, shared via `munin.env`). No new SSH keys,
  no new ports, no rsync.
- **Cadence**: real-time. `hook_service.py` fires per `post-finish`
  event; no cron.
- **Idempotency**: VPS-side directory move is the primary signal —
  `complete/<email>/` → `processed/<email>/` only on 200 OK from the
  cluster. Cluster adds content-hash dedup as a safety net.
- **Ingest endpoint semantics**: **synchronous**. `/api/admin/ingest`
  runs the full GROBID → CrossRef → SPECTER → Qdrant/Neo4j pipeline
  and returns 200 only after the paper is fully indexed. Matches the
  VPS's "200 = move to processed/" semantics and is fine for the
  one-time backfill (migration script on VPS can parallelise POSTs).
- **Attribution model — contributors as LIST field**: each Qdrant
  `papers` point carries a `contributors` array of
  `{email, group_slug, display_name, upload_time}`. DOI dedup in
  the pipeline appends new contributors to the existing point
  instead of creating a duplicate, so a paper uploaded by both
  Zeitler and Corzilius has two entries and matches both `#zeitler`
  and `#corzilius`. Qdrant's array-member filter handles the tag
  query natively.
- **Allowlist miss**: ingest anyway. Stamp contributor entry with
  `group_slug="unknown"` and no display name. An admin can amend
  `config/contributors.yml` and rerun if attribution is wanted
  later.
- **Existing uploader mapping** (for `config/contributors.yml`):
  | Email | Slug | Display Name |
  |---|---|---|
  | `contributor-a@example.org` | `corzilius` | Corzilius Lab (Rostock) |
  | `contributor-b@example.org` | `deibel` | Deibel Group (Chemnitz) |
  | `contributor-c@example.org` | `zeitler` | Zeitler Lab (Leipzig) |
  `admin@example.org` (admin test uploads, 1 file) is **skipped in
  the backfill** entirely.
- **Retention**: both sides keep the PDF. VPS archives to
  `/mnt/uploads/processed/<email>/`; cluster stores a working copy
  under `/opt/munin/data/papers/pdf/` so `get_paper_pdf` /
  `read_paper` stay local.

## §15 scope

1. `scripts/knowledge/build_embedding_map.py`
   - Scroll `papers` Qdrant collection with `with_vectors=True`.
   - UMAP → 2D (`n_components=2, n_neighbors=15, min_dist=0.1`).
   - HDBSCAN on UMAP output (`min_cluster_size=30`, auto cluster
     count).
   - For each cluster, sample ~10 titles closest to centroid, ask
     vLLM for a 2-4 word topic label with `enable_thinking=false`.
   - **Write `cluster_id`, `topic_label`, `topic_slug` back to every
     Qdrant point's payload** (the piece §28 relies on).
   - Also emit `/opt/munin/knowledge/embedding_map.json` for the
     future map UI.
2. New endpoint `GET /api/embedding_map` — static file serve.
3. Idempotent nightly run at 3 AM — skip rebuild if no new papers
   since last run.
4. `deploy.sh knowledge` mode to sync `scripts/knowledge/`.

**Execution host**: open question. Host cron on `hugin` is simplest
(UMAP + HDBSCAN are CPU-only, ~30 s on 50 k papers). SLURM job only
if we want resource isolation from the head node. vLLM label calls
hit the existing service either way.

## §28 scope (after §15)

1. `config/contributors.yml` — allowlist entry shape:
   ```yaml
   - email: contributor-c-lab@example.org
     username: zeitler
     display_name: Zeitler Lab
     research_group: zeitler
     research_group_display_name: Zeitler Lab
   ```
2. Extend `paper_pipeline.py`:
   - Read `{uuid}.contributor.json` sidecar beside the PDF.
   - Stamp `contributor_username`, `contributor_display_name`,
     `research_group`, `research_group_display_name`, `contributed_at`
     into Qdrant `papers` payload AND Neo4j `Paper` node.
3. **Backfill script** (`scripts/pipeline/backfill_contributed.py`):
   - Walk the existing upload drop dir.
   - For each file/batch, resolve uploader → allowlist entry.
   - Unzip if necessary, write sidecar JSONs, invoke
     `paper_pipeline.py --single` per PDF.
4. Extend `POST /api/documents/upload` for *new* uploads:
   - Optional `contribute=true` form flag.
   - Allowlisted users → drop into `pdf/inbox/` with sidecar,
     invoke pipeline (subprocess or queue — decide at impl time).
   - Non-allowlisted users who set `contribute=true` → silently
     route to private `user_docs` as today. No 4xx.
5. `GET /api/tags` catalog:
   - `topics`: distinct `{cluster_id, topic_label, topic_slug,
     paper_count}` from `papers` payload.
   - `contributors`: union of allowlist entries and distinct
     `contributor_username` values observed in `papers` payloads.
   - `groups`: distinct `research_group` values.
   - `system`: `[{slug: "me", label: "My personal notes"}]`.
6. `retrieval/mcp/context.py` — new `current_query_tags` ContextVar.
7. Search tool `tags` parameter across `paper_search`,
   `semantic_scholar_search`, `deep_research`. Topic tags filter on
   `cluster_id`, contributor tags on `contributor_username`, group
   tags on `research_group`. Multiple tags AND-combine.
8. `chat_service.py` reads `tags` from request body, sets
   `current_query_tags`, optionally injects active tag list into
   system prompt.
9. Persona prompts: explain tag semantics; instruct the model to
   surface contributor + group attribution in citations when those
   fields are present on a result.
10. Tests per §28 test plan.

## Open — upload file location

The piece blocking §28 backfill. Need to establish:

- **Where do files submitted via upload.muninai.org physically land?**
  Candidates: a VPS-local directory, an S3/object bucket, pushed
  through the autossh tunnel into the cluster, or a per-user drop
  directory under `/opt/munin/data/`.
- **How is the uploader identified?** A JSON manifest alongside each
  batch? A filename convention? Directory tree keyed by email?
- **Are there already files waiting?** User says yes. We need an
  inventory before writing the backfill script so we know what
  shapes to support.

Investigation plan:
1. Do a test upload via upload.muninai.org while watching VPS +
   cluster file-system activity and server logs.
2. Check the `munin-vps` repo for the upload endpoint handler (this
   repo has no code matching `upload.muninai` or `inbox`, confirming
   the handler isn't here).
3. Inspect the autossh-tunnel target routes — if upload.muninai.org
   proxies to the cluster via the tunnel, the handler would be in
   `retrieval/main.py`, which it isn't. So the handler is almost
   certainly on the VPS side.

## What's NOT in this sprint

- §9 user memory (shipped) and personal-notes `#me` tag mechanism
  (§28's third tag kind). Ship the `#group` / `#@user` / `#topic`
  mechanism first; fold `#me` into the same code path later.
- Conversation-level tag inheritance (`conversations.default_tags`).
- Frontend autocomplete for `#tags` (lives in munin-vps).
- Full §15 interactive map UI (this repo only ships the JSON + the
  endpoint; frontend work is separate).