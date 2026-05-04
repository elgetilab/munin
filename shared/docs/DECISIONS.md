# munin: design decisions

Short rationales for non-obvious choices made over the lifetime of
the monorepo, captured so future readers don't have to re-derive
them. Companion to git history (which has the *what*).

Add a new section when a decision is non-obvious from code +
commit messages alone. Don't add a section for things that
self-document (renames, refactors, bug fixes).

---

## 2026-05-04: `shared/docs/API-CONTRACT.md` retired

The file is now a redirect stub pointing at
[`BACKEND-API.md`](BACKEND-API.md) (canonical). The original had
four broken descriptions of code reality:

- A phantom `event: metadata` SSE frame that doesn't exist in any
  code path. The real first event is `conversation`.
- A wrong `/api/status` response shape (listed `gpu`,
  `slurm_queue`, `started_at`, `uptime_seconds`, none of which
  are emitted). Real shape is
  `{vllm: {...}, services: {...}, timestamp}`.
- Wrong RAG sources (listed `notion`, `graph`; backend supports
  only `papers` + `web`).
- A fictitious `code` field in the error envelope. Real envelope
  is `{"error": {"message": "..."}}` only.

The redirect stub was kept (rather than the file being deleted
outright) so prior links from external docs and archived specs
don't 404.

## 2026-05-04: `shared/docs/BACKEND-FRONTEND-SYNC.md` archived

The original sync log declared its own self-archival criterion
("this doc gets archived once `default_tags` lands and both sides
have completed housekeeping"). Both conditions had been met. The
original file was moved to
`shared/docs/archive/SYNC-2026-04.md` as a frozen historical
record; a fresh minimal sync log replaced it at the original
path. Future cross-cut Q&A entries go in the new file; the
archived one is read-only context for understanding why various
2026-Q1/Q2 design decisions look the way they do.

## 2026-05-04: files deliberately kept (post-merge cleanup Tier 4)

A six-tier post-merge cleanup audit identified things that *look*
like dead code but were intentionally retained. Listed here so a
future "should we delete this?" question can find the answer:

- **`frontend/docs/archive/`, `backend/docs/archive/`**:
  already-archived design specs that drove shipped features (e.g.
  `FRONTEND-KNOWLEDGE-TAB.md`, `FRONTEND-REPORT-CHAT.md`,
  `MIGRATION.md`, `CHAT-PERSISTENCE.md`). Not referenced from
  live code; kept as frozen historical context. Moving them to a
  git tag was considered and rejected as more ceremony than
  benefit.
- **Manual QA scripts** under `backend/scripts/` (`smoke-test.sh`,
  `flakiness-suite.py`, `repro_vllm_hang.py`, `stress-test.py`,
  `test_compile_latex_diff_flow.py`,
  `test_delegate_persona.py`), actively useful as developer
  tooling for cluster-side debugging. Not invoked by deploys, so
  they don't appear in the runtime path; safe to ignore unless
  you're debugging.
- **Frontend build helpers** under `frontend/scripts/`
  (`gen_pwa_icons.py`, `invert_logos.py`, `create_background.py`),
  one-shot maintenance utilities used during initial setup.
  Harmless to keep; expensive to re-derive if needed again.
- **`backend/docs/archive/FRONTEND-TASKS.md`**: looks duplicate
  with `frontend/docs/archive/FRONTEND-TASKS.md` but isn't: the
  backend copy is "Frontend Tasks (handoff from munin-backend)"
  (backend's perspective), the frontend copy is "Frontend
  Integration Task List" (frontend's API-contract reconciliation).
  Both kept; the backend one is referenced from
  `chat_service.py` and `future_features.md`.

## 2026-05-04: CLAUDE.md kept lean; runbook content lives in README.md

CLAUDE.md is auto-loaded into the agent's context every session.
Keep it focused on "what you need to know to make safe edits in
this directory", coding conventions, gotchas, cross-references
to canonical docs. Anything that reads more like
"here's the project" or "here's how to deploy" goes into
README.md (or DESIGN.md), where humans + agents alike find it.

This is why the directory-level CLAUDE.md files are short
(~70 lines each) and the README.md files carry the architecture
diagrams, repo-structure trees, key paths, and deploy commands.
Don't move runbook content back into CLAUDE.md.

## 2026-04: monorepo origin (merged from `munin-backend` + `munin-vps`)

This repo started as two separate repos: `munin-backend` (cluster-side: vLLM, retrieval API, paper pipeline) and `munin-vps` (VPS-side: Caddy, auth, gateway, web UI). They were merged in 2026-04 because three concrete artifacts had drifted between them:

- Persona JSON files and logos: two copies, content drift.
- Contributor backfill script: three copies with diverging 503-handling.
- `BACKEND-FRONTEND-SYNC.md`: two half-filled copies, one per side.

The merge introduced `shared/` as the single source of truth for cross-cut artifacts: `shared/personas/` (backend deploy rsyncs into `/opt/munin/personas`; frontend fetches at runtime via `/api/personas`), `shared/config/contributors.yml` (read by the cluster ingest endpoint and the VPS backfill cron), and `shared/docs/` (canonical API contract and sync docs both sides edit).

The two deploys stayed separate. There is no top-level deploy script and no merged `docker-compose.yml`: the cluster needs sudo + systemd, the VPS is docker compose, different lifecycles, intentionally not unified.

Other artefacts of the merge worth knowing:

- The React UI was at `frontend/frontend/` pre-merge, renamed to `frontend/webui/` to remove the confusing nesting.
- `backend/personas/` was canonical; `frontend/cluster/personas/` was stale and dropped.
- The pre-merge `BACKEND-FRONTEND-SYNC.md` is archived at `shared/docs/archive/SYNC-2026-04.md` (see the 2026-05-04 entry above).

Post-merge cleanup landed in six tiers (commit `e419fe6` plus the 2026-05-04 entries above). The original step-by-step merge plan (`REFACTOR-PLAN.md`) was deleted in 2026-05 once this section captured the outcome; `git log --diff-filter=D -- REFACTOR-PLAN.md` will resurrect it if needed.
