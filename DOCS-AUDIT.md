# munin monorepo — documentation audit (2026-05-04)

After the six-tier post-merge cleanup landed (`CLEANUP.md`), this
audit checked whether the long-form docs and sync/contract docs
still match reality. Two parallel audits ran: one on
`shared/docs/` (sync + contract docs), one on the long-form
`CLAUDE.md` / `README.md` / `DESIGN.md` files.

Findings below are grouped by severity, then by file. After this
audit completed, all six fixes were executed in the same session
(see commit history).

---

## Severity 1 — broken (claims things that aren't true)

- **`backend/CLAUDE.md:155, 164`** — references `sudo ./deploy.sh
  cleanup` and lists the mode in the all-flow comment. The mode
  was removed in Tier 3.

- **`backend/CLAUDE.md:29`** — repository-structure tree still
  shows the pre-merge layout (`munin-backend/` as the root,
  `personas/` under backend rather than under `shared/`).

- **`backend/CLAUDE.md:54`** — lists `retrieval/static/` as
  "Search/Research UIs (kept)". Tier 6 deleted that directory.

- **`backend/CLAUDE.md:59`** — `personas/` shown as a backend dir;
  canonical location is now `shared/personas/`.

- **`frontend/CLAUDE.md:50`** — repository-structure tree root is
  labelled `munin-vps/`. Tier 2 fixed the title but the tree
  itself wasn't touched.

- **`frontend/README.md:92`** — same: tree root says
  `munin-vps/`.

- **`backend/DESIGN.md:91, 125`** — repo tree still shows
  `static/` "kept temporarily" (gone post-Tier-6) and uses
  pre-merge framing throughout.

- **`shared/docs/API-CONTRACT.md`** — four broken descriptions of
  code reality:
  - line 51: status example lists `gpu`, `slurm_queue`,
    `started_at`, `uptime_seconds` — none of these are emitted by
    `/api/status` (real shape is `{vllm:{status,model,next_start?},
    services:{...}, timestamp}`).
  - lines 108–114: lists `notion`, `graph` as RAG sources;
    backend supports only `papers` + `web`.
  - line 113: says `persona` is required; backend defaults to
    `chat` if omitted.
  - lines 118–155: describes a phantom `event: metadata` SSE frame
    that doesn't exist in any code path. The real first SSE event
    is `conversation` per `BACKEND-API.md` §5.
  - lines 262–273: error-envelope description includes a `code`
    field; real envelope is `{"error":{"message":"..."}}` only.

  The doc is fully redundant with `BACKEND-API.md` (canonical) — recommend retire.

## Severity 2 — stale (post-merge framing or missing recent additions)

- **`backend/CLAUDE.md:1–7`** — "What This Repo Is" still framed
  as "munin-backend" in a separate-repo world ("This repo
  extracts, cleans up, and extends those services").

- **`backend/CLAUDE.md:12, 199`** — architecture diagram says
  `VPS (munin-vps)`; "Related Repos: munin-vps".

- **`frontend/CLAUDE.md:222`** — "Related Repos: munin-backend".

- **`frontend/README.md:142`** — same.

- **`backend/DESIGN.md`** — multiple sections describing already-completed
  Tier-3-style cleanups (Open WebUI removal, Cloudflared
  removal, status-page removal) as still-pending future work.
  Endpoint catalog at lines 51–77 is missing `/api/tags`,
  `/api/tags/{kind}/{slug}/papers`, `/api/embedding_map`,
  `/api/admin/ingest`. Cleanest fix: drop the catalog entirely
  and point at `shared/docs/BACKEND-API.md` (canonical).

- **`shared/docs/BACKEND-FRONTEND-SYNC.md`**:
  - lines 431–432: Q10 items 1–2 were silently answered by
    QF1/QF2 elsewhere in the same doc (cluster topic labels
    fixed; raw-mode usage shipped).
  - lines 206–223: doc-path table predates the monorepo split
    (paths like `docs/BACKEND-API.md` should be
    `shared/docs/BACKEND-API.md`).
  - lines 671–674: doc says it gets archived "once `default_tags`
    lands and both sides have completed housekeeping".
    `default_tags` shipped per §7 lines 642–650 — by the doc's
    own self-archival criterion it is now archive-ready.
  - line 611: still recommends keeping `API-CONTRACT.md` (which
    we're retiring per Severity 1).

## Severity 3 — cosmetic (won't surface unless someone is being meticulous)

- `shared/docs/BACKEND-API.md §4.21` — could note that an empty
  embedding map → empty `topics` array on `/api/tags`, not 404.
- `shared/docs/CONTRIBUTOR-INGEST.md` — pipeline-diagram paths
  hardcode `/papers/inbox/...` rather than noting they're
  env-driven (`PAPERS_PDF_DIR`).

---

## Execution — done 2026-05-04

All six fixes landed:

1. ✅ `deploy.sh cleanup` references removed from
   `backend/CLAUDE.md` (the `all` flow comment + the per-mode line).
2. ✅ Repo-structure trees rewritten in `backend/CLAUDE.md`,
   `frontend/CLAUDE.md`, `frontend/README.md`, and
   `backend/DESIGN.md`. Each now reflects the merged monorepo
   layout and points at `../shared/` for cross-cut artifacts.
3. ✅ `shared/docs/API-CONTRACT.md` retired — replaced with a
   short redirect stub explaining what the doc used to be and
   pointing at `BACKEND-API.md`. Existing references in
   top-level `CLAUDE.md` / `README.md` and in
   `frontend/docs/AGENTIC-ORCHESTRATION.md` updated.
4. ✅ `shared/docs/BACKEND-FRONTEND-SYNC.md` moved (via
   `git mv`) to `shared/docs/archive/SYNC-2026-04.md`. A fresh
   minimal sync log replaced the original location, with
   format guidance and a back-reference to the archive.
5. ✅ Framing / related-repos refs fixed across
   `backend/CLAUDE.md`, `frontend/CLAUDE.md`,
   `frontend/README.md`, `backend/DESIGN.md`. All stop saying
   "munin-vps" / "munin-backend" as if they were separate
   repos, and link relatively (`../frontend/`, `../backend/`,
   `../shared/`) where appropriate.
6. ✅ Duplicate endpoint catalogue dropped from
   `backend/DESIGN.md`; replaced with a pointer to
   `shared/docs/BACKEND-API.md` (canonical). Completed
   "Changes to Existing Code" + "What to Discard" sections
   converted to past tense.

Severity-3 items intentionally skipped — they don't justify the
change cost.
