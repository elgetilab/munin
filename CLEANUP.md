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

## Tier 3 — verify cluster state before deciding

- [ ] **`deploy_cleanup` block in `backend/deploy.sh:558-589`** —
  removes legacy Open WebUI + status-page containers/dirs.
  Idempotent, so running it on a clean node is a no-op. Only
  safe to delete after confirming `docker ps -a` on every
  cluster node shows none of `munin-openwebui`,
  `munin-status-page`, `munin-cloudflared`. If confirmed clean,
  delete the function and its `all`-mode invocation.

- [ ] **`X-Authentik-Email` fallback** —
  `frontend/upload/hook_service.py:122` (and the equivalent
  comment in `backend/CLAUDE.md` Auth Headers section). Keep
  while any caller might still send the old header; add a
  comment `# remove after YYYY-MM-DD` once the cluster is
  confirmed fully migrated. Note: `shared/docs/BACKEND-API.md`
  already says the gateway-side fallback was removed
  2026-04-21, so this hook-service fallback may already be
  dead code.

## Tier 5 — structural follow-up surfaced during cleanup

- [ ] **Centralize shared static assets** — Tier 2 #7 fixed the
  immediate version drift across the 6 logo copies, but they're
  still six copies. The structural fix is to make
  `shared/assets/` the canonical source and have `backend/deploy.sh`
  + `frontend/bootstrap.sh` (or rsync workflow) propagate to the
  six runtime locations. Same pattern as `shared/personas/`.
  Worth doing the next time we touch shared/ for any reason.

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
- 2026-05-04: Tier 1 + Tier 2 done.
- Tier 3 + Tier 5 open.
