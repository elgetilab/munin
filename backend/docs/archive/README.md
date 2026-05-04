# Archived docs (backend perspective)

Specs that drove shipped features, kept as frozen historical
references so git history + spec intent can be read together. Do
NOT treat any doc here as current contract — read the
corresponding live operator doc or `BACKEND-API.md` instead.

| Archived doc | Drove | Live reference |
|---|---|---|
| `FRONTEND-TASKS.md` | Backend-side handoff list (2026-04-14, 12 items) — 13/15 shipped, 1 low-pri (FTS prefix search), 1 obsolete (slash commands). Referenced from `backend/retrieval/chat_service.py` and `backend/docs/future_features.md`. | See `BACKEND-API.md` §4 for live endpoints |

Originally also held `FRONTEND-KNOWLEDGE-TAB.md` and
`FRONTEND-REPORT-CHAT.md`; both were byte-identical to copies in
`frontend/docs/archive/` and were removed 2026-05-04 to dedup.
For those specs, see `frontend/docs/archive/`.

`frontend/docs/archive/FRONTEND-TASKS.md` is a separate document
(frontend's API-contract reconciliation), not a copy of this one.

Archived 2026-04-21 as part of the backend↔frontend sync
reconciliation (`BACKEND-FRONTEND-SYNC.md`).
