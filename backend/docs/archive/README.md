# Archived docs (backend perspective)

Specs that drove shipped features, kept as frozen historical
references so git history + spec intent can be read together. Do
NOT treat any doc here as current contract — read the
corresponding live operator doc or `BACKEND-API.md` instead.

| Archived doc | Drove | Live reference |
|---|---|---|
| `KNOWN-BUGS-resolved.md` | Bugs 1-5 from `KNOWN-BUGS.md`, full write-ups kept after the fixes landed. Several were factually stale by the time anyone re-read them, which is the searchable pattern worth keeping. | Live list: `../KNOWN-BUGS.md` (stubs there point back here) |
| `CLUSTER-USAGE-TRACKING.md` | The empty `usage` object in the SSE `done` event (fixed). | `../../../shared/docs/BACKEND-API.md` |
| `CONTRIBUTOR-CORPUS-PLAN.md` | Contributor attribution and tag-scoped search for uploaded corpora (shipped). | `../TAG-SCOPED-SEARCH.md`, `../../../shared/docs/CONTRIBUTOR-INGEST.md` |
| `DEEPRESEARCH_PLAN.md` | The first Deep Research, as SLURM jobs on MiroThinker (retired 2026-07; Deep Research now runs in-process). | `../../../shared/docs/BACKEND-API.md` |
| `PIPELINE-CONSOLIDATION-PLAN.md` | Consolidating the paper pipeline after the 2026-05 ingest audit (done). | `../PAPER-PIPELINE-AUTOMATION.md` |
| `USER-DOC-INGESTION-GAPS.md`, `USER-DOCUMENTS-ANSWERS.md` | Per-user document upload: known gaps and the VPS side's answers (shipped). | `../../../shared/docs/BACKEND-API.md` |
| `VPS-BACKFILL-HANDOFF.md` | Wiring the VPS upload path to cluster ingest, plus the one-time backfill (done). | `../../../frontend/scripts/BACKFILL-README.md` |
| `SERVICES.md` | The cluster's service list before the compose stack (Open WebUI, Cloudflared, `start-all.sh`); archived 2026-10. | `../../README.md`, `../../../INSTALL.md` |
| `FRONTEND-TASKS.md` | Backend-side handoff list (2026-04-14, 12 items) — 13/15 shipped, 1 low-pri (FTS prefix search), 1 obsolete (slash commands). Referenced from `backend/retrieval/chat_service.py` and `backend/docs/future_features.md`. | See `BACKEND-API.md` §4 for live endpoints |

Originally also held `FRONTEND-KNOWLEDGE-TAB.md` and
`FRONTEND-REPORT-CHAT.md`; both were byte-identical to copies in
`frontend/docs/archive/` and were removed 2026-05-04 to dedup.
For those specs, see `frontend/docs/archive/`.

`frontend/docs/archive/FRONTEND-TASKS.md` is a separate document
(frontend's API-contract reconciliation), not a copy of this one.

Archived 2026-04-21 as part of the backend↔frontend sync
reconciliation (`BACKEND-FRONTEND-SYNC.md`).
