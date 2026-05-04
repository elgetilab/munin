# munin monorepo — post-merge cleanup

Punch-list from the audit run on 2026-05-04, after the
`backend/` + `frontend/` merge into a single tree.

Sorted by value × safety. Tier 1 + Tier 2 are scheduled for
immediate cleanup. Tier 3 needs cluster verification first.
Tier 4 is intentionally deferred — no action unless reopened.

---

## Tier 1 — fix immediately (low risk, high value)

- [x] **API doc drift** — `shared/docs/BACKEND-API.md` is the
  canonical contract per `CLAUDE.md`, but three frontend-facing
  endpoints exist in `backend/retrieval/main.py` and aren't
  documented:
  - `GET /api/tags` (main.py:1543)
  - `GET /api/tags/{kind}/{slug}/papers` (main.py:1389)
  - `GET /api/embedding_map` (main.py:1363)

  Plus two ops/internal routes worth a one-liner so readers
  don't think they're undocumented holes:
  - `GET /health` (main.py:502) — ops health, not user-facing
  - `POST /api/admin/ingest` (main.py:1824) — covered by
    `shared/docs/CONTRIBUTOR-INGEST.md`; cross-link from §4

- [x] **Stale `munin-vps` references in active docs** —
  `backend/docs/CONTRIBUTOR-CORPUS-PLAN.md:53, 195, 209` still
  point readers at a separate repo that no longer exists.
  Rewrite to point at `frontend/` or `frontend/webui/`.

- [x] **Stale full-path comment** —
  `backend/scripts/pipeline/qdrant_repair_sweep.py:22` has a
  copy-paste hint missing the `backend/` segment after the
  merge.

- [x] **`frontend/secrets.env.example` is dead** — references
  Authelia (`AUTHELIA_*`) and Open WebUI (`WEBUI_SECRET_KEY`),
  both replaced by munin-auth + the React UI. `frontend/CLAUDE.md`
  already says it's superseded. Delete.

- [x] **`frontend/.env.template` doesn't exist** despite
  `frontend/CLAUDE.md` claiming it supersedes
  `secrets.env.example`. Either ship a real template or
  remove the reference. (Lowest-effort path: remove the
  reference; secrets live in `~/munin/.env` on the VPS only,
  per the deploy notes.)

## Tier 2 — worth doing while we're here

- [x] **Header drift** — `frontend/CLAUDE.md:1` and
  `frontend/README.md:1` still title themselves `# munin-vps`.
  Cosmetic, but a confusing first impression for anyone landing
  in the merged tree.

- [x] **Logo duplication + version split** —
  `backend/retrieval/static/assets/munin_logo_without_script.webp`
  is byte-identical to two copies under
  `frontend/static/{research,search}/assets/`. Two more copies
  under `frontend/static/{shared,docs,landing}/assets/` are a
  newer 74 KB version. Pick the newer file, move it to
  `shared/assets/munin_logo_without_script.webp`, and have all
  six callsites reference the shared path.

## Tier 3 — done after cluster verification

- [x] **`deploy_cleanup` block** — verified on hugin: no
  `munin-openwebui` / `munin-status-page` / `munin-cloudflared`
  containers, no `services/openwebui` / `services/status-page`
  dirs, no `update-openwebui-models.sh`. Removed the function
  from `backend/deploy.sh`, dropped the `cleanup` mode, removed
  it from `deploy_all`, removed it from both `Modes:` echo lines.

- [x] **`X-Authentik-Email` fallback** — confirmed dead by
  cross-checking `BACKEND-FRONTEND-SYNC.md` QF5 (gateway sets
  `X-Munin-Email` exclusively; retrieval/main.py + mcp/endpoints.py
  already had the fallback dropped 2026-04-21). Removed from
  `frontend/upload/hook_service.py:122` (the lone surviving live
  caller). Updated `backend/CLAUDE.md`, `backend/DESIGN.md`,
  and `shared/docs/API-CONTRACT.md` to drop the prescriptive
  "accept both" language and replace with a removal note.

## Tier 5 — done (turned out to be orphan-deletion, not centralization)

- [x] **Logo "duplication" was actually orphan files** — the
  Caddyfile's `(shared-assets)` snippet already serves
  `frontend/static/shared/*` cross-subdomain at `/shared/*`, and
  every active page (search, research, landing, docs, chat,
  upload, maintenance) references `/shared/munin_logo...`. The
  five non-shared copies under
  `frontend/static/{research,search,landing,docs}/assets/` and
  `backend/retrieval/static/assets/` were never referenced —
  they had been dead since the Caddyfile was set up that way.
  Deleted the orphan logo files and the now-empty `assets/`
  subdirs. Single canonical at `frontend/static/shared/` is
  what every active page already uses.

- [x] **Backend retrieval `static/` was entirely empty** — only
  contained the orphan logo. Removed the dir + the
  `COPY static /app/static/` line in `backend/retrieval/Dockerfile`.
  The routes in `main.py` that reference `STATIC_DIR`
  (`/deepresearch`, `/search`, `/assets` mount) all already
  guard with `os.path.exists` and degrade to inline-HTML
  fallbacks — no code changes needed; they self-degrade
  cleanly when the dir is absent.

- [x] **Empty `shared/docs/archive/` directory** removed (Tier 4
  trivial win folded in here).

## Tier 6 — done

- [x] **Reconcile duplicated archived specs** — turns out
  `FRONTEND-TASKS.md` is *not* a duplicate: the backend copy is
  "Frontend Tasks (handoff from munin-backend)" — backend's
  perspective, referenced from `chat_service.py` and
  `future_features.md` (load-bearing); the frontend copy is
  "Frontend Integration Task List" — frontend's reconciliation
  of the API contract. Kept both. Removed the two
  byte-identical duplicates (`FRONTEND-KNOWLEDGE-TAB.md`,
  `FRONTEND-REPORT-CHAT.md`) from `backend/docs/archive/`,
  cross-linked the README to point at `frontend/docs/archive/`
  for those.

- [x] **Dead static-serving infrastructure in
  retrieval/main.py** — confirmed Caddy on the VPS serves the
  user-facing UIs from `frontend/static/...` directly; the
  cluster's `GET /` and `GET /deepresearch` HTML routes were
  unreachable from anything. Replaced `GET /` with a four-line
  JSON identifier (handy for `curl` smoke tests); deleted the
  `GET /deepresearch` handler, the `/assets` mount, the
  `STATIC_DIR` env var, and the now-unused `HTMLResponse` /
  `StaticFiles` imports. The other `/deepresearch/{submit,
  status,output,queue,jobs}` API routes are unaffected.

## Tier 4 — skip (not actually broken)

- `frontend/docs/archive/` and `backend/docs/archive/` —
  already archived, not referenced from live code or docs.
  Moving to a git tag is more ceremony than it saves.
- `shared/docs/archive/` — empty directory; benign.
- Manual QA scripts under `backend/scripts/` (smoke-test.sh,
  flakiness-suite.py, repro_vllm_hang.py, stress-test.py,
  test_*.py) — referenced from CLAUDE.md as developer tooling.
- `frontend/scripts/` build helpers (gen_pwa_icons.py,
  invert_logos.py, create_background.py) — one-shot maintenance
  utilities; harmless to keep.

---

## Status

- 2026-05-04: audit complete, this file written.
- 2026-05-04: Tier 1 + Tier 2 + Tier 3 + Tier 5 + Tier 6 done.
- All audit-surfaced cleanup complete.
