# Plan: Unify munin-backend + munin-vps into a single monorepo

## Context

You've physically dropped the two previously separate repos into one tree —
`backend/` (formerly `munin-backend`, runs on the SLURM cluster) and
`frontend/` (formerly `munin-vps`, runs on the Hetzner VPS, which also
contains the React chat UI and all Caddy-served static pages).

Both halves talk to each other across an autossh reverse tunnel
(VPS `127.0.0.1:18080` → cluster `:8080`). They share three concrete
contracts: the HTTP API surface (documented in `BACKEND-FRONTEND-SYNC.md`
+ `BACKEND-API.md` / `BACKEND-API_v2.md`), the persona files
(JSON + SVG icons), and the contributor backfill script that runs on the
VPS but POSTs to `/api/admin/ingest` on the cluster.

Today the two halves drift independently. The first scan turned up:

- Three copies of the contributor backfill script with **diverging
  503-handling logic** (`backend/scripts/vps/`, `frontend/scripts/`,
  `frontend/todo/`).
- Two copies of each persona JSON (`backend/personas/` vs
  `frontend/cluster/personas/`) **with content drift**, plus two copies
  of every persona SVG.
- Two `BACKEND-FRONTEND-SYNC.md` files where each side fills in only its
  half of the Q&A.
- Two `start-vllm-service*.sh` with different SLURM resource asks.
- Two `munin-tunnel.service` files pointing at **different VPS IPs**
  (one is the live VPS, the other targets a different "hugin" host).
- Stale legacy `retrieval/static/{search,research}.html` superseded by
  the VPS-served `frontend/static/{search,research}/index.html`.

The goal is one repo where each file has one home, sync drift is
impossible, and a contributor can `git clone` once and find both
deploy targets.

## Layout (target)

```
munin/
├── README.md                 ← top-level: what this monorepo is, how
│                                deploys split, link-outs to per-side READMEs
├── CLAUDE.md                 ← top-level Claude context: monorepo overview
├── .gitignore                ← merged (node_modules, .env, __pycache__, etc.)
│
├── shared/                   ← single source of truth for cross-cut artifacts
│   ├── personas/             ← JSON + logos/  (canonical = current backend/)
│   ├── docs/
│   │   ├── BACKEND-FRONTEND-SYNC.md   ← merged: both sides edit one file
│   │   ├── API-CONTRACT.md            ← from frontend/docs/
│   │   ├── BACKEND-API.md             ← canonical (pick newer of v1 / v2)
│   │   ├── CONTRIBUTOR-INGEST.md      ← merged
│   │   └── archive/
│   └── config/
│       └── contributors.yml  ← single source; backend deploy + VPS
│                                backfill cron both read from here
│
├── backend/                  ← deploys to /opt/munin/ on cluster head (hugin)
│   ├── CLAUDE.md             ← cluster-scoped context
│   ├── DESIGN.md
│   ├── deploy.sh             ← unchanged behavior; reads ../shared/personas/,
│   │                            ../shared/config/contributors.yml
│   ├── config/               ← cluster-only systemd units, agents.yml,
│   │                            faq.yml, munin.env.template, tunnel.service
│   ├── docker/               ← cluster-side compose (Qdrant/Neo4j/GROBID/
│   │                            SearXNG/retrieval/sandbox)
│   ├── retrieval/            ← FastAPI (the API the VPS proxies to)
│   ├── sandbox/
│   ├── scripts/
│   │   ├── vllm/             ← keep ONE start-vllm-service.sh
│   │   ├── deepresearch/
│   │   ├── pipeline/
│   │   └── (smoke-test.sh, stress-test.py, repro_*.py)
│   └── docs/                 ← cluster-only docs (KNOWLEDGE-MAP, SERVICES,
│                                PAPER-PIPELINE-AUTOMATION, DEEPRESEARCH_PLAN,
│                                future_features.md, …)
│
└── frontend/                 ← deploys to ~/munin/ on the Hetzner VPS
    ├── CLAUDE.md             ← VPS-scoped context
    ├── README.md
    ├── bootstrap.sh          ← VPS provisioning
    ├── docker-compose.yml    ← Caddy / munin-auth / api-gateway / tusd /
    │                            hook-service
    ├── secrets.env.example   ← keep ONE template (drop frontend/secrets.env.example
    │                            if .env.template referenced in README is the live one)
    ├── caddy/
    ├── auth/
    ├── gateway/
    ├── upload/
    ├── config/               ← VPS-only (quotas.yml)
    ├── webui/                ← RENAMED from frontend/frontend/  ← React UI source
    │                            vite outDir → ../static/chat/
    ├── static/               ← Caddy-served: shared/, chat/, landing/,
    │                            docs/, search/, research/, upload/,
    │                            maintenance/
    ├── scripts/              ← VPS-side scripts (gen_pwa_icons,
    │                            invert_logos, install-backfill-cron,
    │                            backfill_contributed.py — single copy)
    ├── docs/                 ← VPS-only specs (CHAT-PERSISTENCE, CHAT-UI-TASK-LOG,
    │                            FEATHER-VORTEX, SLEEPING-PAGE, AGENTIC-ORCHESTRATION,
    │                            USER-DOCUMENTS, ADDITIONAL-FEATURES, MIGRATION,
    │                            CURRENT-STATUS)
    └── cluster-snippets/     ← RENAMED from frontend/cluster/ ; only
                                 hugin-tunnel-setup.md kept (reference doc).
                                 munin-tunnel.service moved to backend/config/.
```

## Shape of the change

```
                    BEFORE                                  AFTER
   ┌────────────────────────────────────┐    ┌────────────────────────────────────┐
   │ backend/personas/  (canonical?)    │    │ shared/personas/  (single source)  │
   │ frontend/cluster/personas/  (drift)│ ─► │     ▲              ▲                │
   └────────────────────────────────────┘    │     │              │                │
                                              │ backend/deploy.sh  frontend/webui  │
   ┌────────────────────────────────────┐    │ rsyncs into        Caddy /api/persona│
   │ backend/scripts/vps/backfill_…  v1 │    │ /opt/munin/personas fetches from API │
   │ frontend/scripts/backfill_…     v2 │ ─► └────────────────────────────────────┘
   │ frontend/todo/backfill_…        v2 │    ┌────────────────────────────────────┐
   └────────────────────────────────────┘    │ frontend/scripts/backfill_…  (one) │
                                              │ shared/docs/CONTRIBUTOR-INGEST.md  │
                                              │ shared/config/contributors.yml     │
                                              └────────────────────────────────────┘

   ┌────────────────────────────────────┐    ┌────────────────────────────────────┐
   │ backend/docs/BACKEND-FRONTEND-SYNC │    │ shared/docs/BACKEND-FRONTEND-SYNC  │
   │ frontend/docs/BACKEND-FRONTEND-SYNC│ ─► │   (single doc, both sides edit)    │
   └────────────────────────────────────┘    └────────────────────────────────────┘
```

Cluster and VPS are still separate runtimes — there is no merged
`docker-compose.yml`. Each side has its own deploy entrypoint
(`backend/deploy.sh` for cluster-side rsync into `/opt/munin/`,
`frontend/{bootstrap.sh,docker-compose.yml}` for the VPS). The monorepo
unifies the source tree, not the deployments.

## Concrete moves / merges

### A. Personas (drift today)
1. Diff `backend/personas/{chat,code,research}.json` vs
   `frontend/cluster/personas/{chat,code,research}.json`. Reconcile
   into a single set under `shared/personas/`. Likely the backend
   versions are the live ones (the API serves them from
   `/opt/munin/personas`), but **confirm before deleting the
   frontend/cluster/personas/ copies**.
2. Move all SVG logos to `shared/personas/logos/`. Drop the duplicate
   top-level SVGs in `frontend/cluster/personas/`.
3. Update `backend/deploy.sh` (the `personas` mode at lines that rsync
   the `personas/` dir into `$MUNIN_PERSONAS`) to read from
   `../shared/personas/` instead of `./personas/`.
4. The frontend doesn't need persona files at build time — it fetches
   them at runtime via `GET /api/personas` and `GET /api/personas/{id}/icon`
   served by `retrieval/main.py:962-970`. So the `shared/` move only
   affects backend deploy.

### B. Contributor backfill (3-way drift today)
1. Pick the newer logic. The `backend/scripts/vps/backfill_contributed.py`
   has the in-place sleep on 503; the `frontend/scripts/` and
   `frontend/todo/` copies break out and let cron retry. Per the
   sync-doc note ("Both pieces shipped 2026-04-23"), the cron-break
   variant is the agreed approach — **canonicalize the
   frontend/scripts/ version**.
2. Single home: `frontend/scripts/backfill_contributed.py` +
   `frontend/scripts/install-backfill-cron.sh`.
3. Delete `backend/scripts/vps/` entirely (move
   `BACKFILL-README.md` into `shared/docs/` if useful, otherwise drop).
4. Delete `frontend/todo/` (it's the same files as `frontend/scripts/`).

### C. Sync / contract docs (2-way drift today)
1. Single `shared/docs/BACKEND-FRONTEND-SYNC.md`: take whichever copy
   has more up-to-date answers in §4 and merge any unique content
   from the other.
2. `shared/docs/CONTRIBUTOR-INGEST.md`: merge — the backend copy has
   the deeper "adding a contributor" workflow; the frontend copy has
   shorter allowlist guidance. Combine.
3. `shared/docs/BACKEND-API.md`: pick the newer (`BACKEND-API_v2.md`
   in frontend) and replace the older. **Verify** which is current
   before deleting.
4. `frontend/docs/API-CONTRACT.md` → `shared/docs/API-CONTRACT.md`.
5. Cluster-only docs stay in `backend/docs/` (KNOWLEDGE-MAP, SERVICES,
   PAPER-PIPELINE-AUTOMATION, DEEPRESEARCH_PLAN, TAG-SCOPED-SEARCH,
   USER-DOC-INGESTION-GAPS, USER-DOCUMENTS-ANSWERS, CONTRIBUTOR-CORPUS-PLAN,
   future_features.md, the two HANDOFF docs, archive/).
6. VPS-only specs stay in `frontend/docs/` (CHAT-PERSISTENCE,
   CHAT-UI-TASK-LOG, FEATHER-VORTEX, SLEEPING-PAGE, MIGRATION,
   CURRENT-STATUS, ADDITIONAL-FEATURES, AGENTIC-ORCHESTRATION,
   USER-DOCUMENTS).

### D. Contributors registry (currently cluster-only)
1. Move `backend/config/contributors.yml` → `shared/config/contributors.yml`.
2. Update `backend/deploy.sh agents` mode to rsync from
   `../shared/config/contributors.yml`.
3. The VPS backfill script doesn't read this file directly today —
   the cluster does — so no VPS-side change needed.

### E. Tunnel + vLLM scripts (drift today)
1. `frontend/cluster/munin-tunnel.service` is **stale** — it points at
   IP `<old-vps-host>` and forwards an unused Open WebUI port `13000`.
   Verify the live `backend/config/munin-tunnel.service` (IP
   `<vps-host>`, only forwards `:18080`) is the deployed one,
   then **delete** the frontend/cluster copy.
2. `frontend/start-vllm-service_oncluster.sh` and
   `backend/scripts/vllm/start-vllm-service.sh` differ in SLURM
   resource asks (4 CPU/16G/20h vs 8 CPU/64G/18h). The backend copy
   is what `deploy.sh vllm` actually ships, so it's authoritative —
   **delete the frontend root-level copy** after confirming the
   resource numbers in the backend version are current.
3. `frontend/cluster/openwebui.env` is dead (Open WebUI was removed
   per `deploy.sh cleanup`) — delete.
4. `frontend/cluster/hugin-tunnel-setup.md` is a reference doc for a
   secondary tunnel — keep, move to `frontend/docs/` or
   `shared/docs/`.

### F. Stale frontend artifacts in retrieval
1. `backend/retrieval/static/{search.html,deepresearch.html}` are
   pre-VPS legacy UIs. The VPS now serves
   `frontend/static/{search,research}/index.html` via Caddy. Confirm
   nobody hits the cluster directly for these (the tunnel is
   API-only via Caddy `/api/*` routes), then delete the legacy files.
   Keep `STATIC_DIR` mount in `retrieval/main.py:97,170` for the
   `/assets` path which still serves shared assets bundled in the
   image. **Verify** with `grep -rn "deepresearch.html\|search.html"
   backend/` that nothing else references them before removal.

### G. Misplaced artifacts
1. `frontend/vps/static/upload/assets/` — odd `vps/` subtree with one
   asset dir. Merge into `frontend/static/upload/` or delete if the
   asset is unused. Check the upload page HTML for references first.
2. `backend/__init__.py` at repo root is a leftover from when
   `backend/` was an importable package; with the monorepo it's not
   imported as a top-level Python package anymore. Delete (verify
   no `from backend import …` exists in `retrieval/`).
3. Rename `frontend/frontend/` → `frontend/webui/` to remove the
   confusing nesting. Update `frontend/webui/vite.config.ts:8` to
   keep `outDir: '../static/chat'` (still correct after rename).
   Update `frontend/README.md`, `frontend/CLAUDE.md`, and
   `frontend/docker-compose.yml` references (`cd frontend && npm run
   build` → `cd webui && npm run build`).

### H. Top-level scaffolding (new)
1. New `README.md` at root: one-paragraph monorepo description,
   "deploys split into two targets" diagram, link to
   `backend/README.md` (create if missing) and `frontend/README.md`.
2. New `CLAUDE.md` at root: tells Claude the layout, points to the
   per-side `CLAUDE.md` files for deeper context, calls out
   `shared/` rules ("if you change a persona, change it once").
3. Merge `.gitignore` (frontend likely has node_modules, build
   outputs; backend has __pycache__, .venv) — produce a single
   root-level file and remove per-side ones unless they need
   different rules.

## Files to read carefully during execution

- `backend/deploy.sh` (lines 45-58 for path constants, search for
  `personas` and `agents` modes) — paths to update for `shared/`.
- `backend/retrieval/main.py:96-170` — STATIC_DIR mount + persona
  routes; verify no path changes needed.
- `frontend/frontend/vite.config.ts` — outDir + dev proxy.
- `frontend/docker-compose.yml` — confirm no relative paths break
  after `frontend/frontend/` → `frontend/webui/`.
- `frontend/scripts/install-backfill-cron.sh` — make sure the cron
  install path still resolves to `frontend/scripts/backfill_contributed.py`.
- Both `BACKEND-FRONTEND-SYNC.md` files — read both fully before
  merging; they answer different questions.

## Decisions (confirmed)

- **`shared/` directory**: yes, introduce top-level `shared/` for
  cross-cut artifacts (personas, sync docs, contributors.yml).
- **Persona conflict resolution**: `backend/personas/` is canonical;
  drop `frontend/cluster/personas/` after reconciling.
- **React UI rename**: `frontend/frontend/` → `frontend/webui/`.
- **Deploy orchestration**: keep the two existing entrypoints
  (`backend/deploy.sh` + `frontend/{bootstrap.sh,docker-compose.yml}`).
  No top-level deploy script.

## Still to verify during execution (not blocking the plan)

- **`BACKEND-API_v2.md` vs `BACKEND-API.md`**: open both, pick the
  newer (likely the v2 in frontend/docs/ given the explicit version
  bump), confirm by checking which lists the most recent endpoints
  documented in `BACKEND-FRONTEND-SYNC.md` §1.
- **Stale legacy UIs in `backend/retrieval/static/`**: before
  deleting `search.html` / `deepresearch.html`, run
  `git grep -E "deepresearch\.html|search\.html"` to confirm nothing
  in `retrieval/main.py` (beyond the existing fallback HTML at lines
  ~139 and ~158) hard-codes them.
- **`frontend/vps/static/upload/assets/`**: open the file, check the
  upload page HTML for references; merge into
  `frontend/static/upload/` or delete.

## Verification

After the refactor, the two halves still build and deploy independently.
Verify each side:

**Backend (cluster, dry-run, no root needed):**
```
cd backend && ./deploy.sh --dry-run all
```
Expect: every rsync source path resolves; `personas` mode points at
`../shared/personas/`; `agents` mode points at `../shared/config/`.

**Frontend (locally):**
```
cd frontend/webui && npm install && npm run build
```
Expect: build emits to `../static/chat/`. Then:
```
cd frontend && docker compose config
```
to verify the compose file still parses with the renamed paths.

**Cross-cut sanity:**
- `git grep "frontend/frontend"` → 0 hits
- `git grep "frontend/todo"` → 0 hits
- `git grep "backend/scripts/vps"` → 0 hits
- `git grep "frontend/cluster/personas"` → 0 hits
- `find . -name backfill_contributed.py` → exactly 1 file
- `find . -name "chat.json"` (under personas) → exactly 1 file
- `find . -name "BACKEND-FRONTEND-SYNC.md"` → exactly 1 file

**End-to-end smoke** (post-deploy on the live machines): the existing
`backend/scripts/smoke-test.sh` still passes against the cluster, and
the VPS can `curl https://chat.muninai.org/api/personas` and get all
three personas with icons.

## Order of execution

1. Confirm the open questions above.
2. Create `shared/{personas,docs,config}/` skeleton.
3. Reconcile + move personas + logos. Update `backend/deploy.sh`.
4. Reconcile + move sync/contract docs.
5. Move `contributors.yml` → `shared/config/`. Update `deploy.sh`.
6. Pick canonical backfill script; delete the two duplicates.
7. Delete stale `frontend/cluster/{munin-tunnel.service,openwebui.env}`,
   `frontend/start-vllm-service_oncluster.sh`,
   `backend/retrieval/static/{search,deepresearch}.html`,
   `backend/__init__.py`.
8. Move `frontend/cluster/hugin-tunnel-setup.md` to chosen home.
   Drop the (now empty) `frontend/cluster/` and `frontend/todo/` dirs.
9. Rename `frontend/frontend/` → `frontend/webui/`. Update
   references in `frontend/{README,CLAUDE,docker-compose.yml}`.
10. Resolve `frontend/vps/static/upload/` (merge or delete).
11. Add root-level `README.md`, `CLAUDE.md`, `.gitignore`.
12. Run the verification checks. Commit in logical chunks
    (one chunk per step 2-11).