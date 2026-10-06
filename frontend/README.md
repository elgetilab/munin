# munin / frontend (VPS)

The frontend half of the [Munin](https://muninai.org) monorepo: login, API gateway, uploads and the web UI. It runs on a small public VM in front of the backend (reached over a private path such as the reverse SSH tunnel), or on the same machine as the backend. Cluster-side code lives in `../backend/`; cross-cut artifacts in `../shared/`.

To install Munin, start at the top-level [INSTALL.md](../INSTALL.md) (`scripts/configure.sh --mode frontend` plus `docker compose`). The reference deployment is walked through in [`docs/install/reference-deployment.md`](../docs/install/reference-deployment.md). This README is the internals and operations reference for that deployment and for developers. The reference deploy runs on a Hetzner CAX21 (Ubuntu 24.04, 4 vCPU ARM, 8 GB RAM) plus a 50 GB volume mounted at `/mnt/uploads`.

## Services

| Service | Port | Purpose |
|---------|------|---------|
| **Caddy** | 80, 443 | Reverse proxy, auto TLS, forward-auth |
| **munin-auth** | 8090 | Email OTP authentication |
| **api-gateway** | 8070 | API key validation, rate limiting, usage logging, proxy |
| **tusd** | 11080 | Resumable file uploads |
| **hook-service** | 18088 | Post-upload file organization |
| **webui-build** | n/a | One-shot chat UI build into `static/chat/` (`webui` profile; the reference VPS builds locally instead) |

Images are pinned to exact versions, and the Python services install
against a `constraints.txt` frozen from the reference deployment.

## Subdomains

Every hostname derives from `MUNIN_DOMAIN`, which is required (both
compose files refuse to start without it). The Caddyfile names its
sites `{$MUNIN_DOMAIN}`, `auth.{$MUNIN_DOMAIN}` and so on; the static
pages link to each other through Caddy templates
(`{{env "MUNIN_URL_*"}}`, set in `docker-compose.yml`); munin-auth
scopes its cookie, CORS origins and redirects to the domain; and the
chat UI derives its sibling URLs at runtime from the hostname it is
served on (`webui/src/lib/urls.ts`). Nothing needs editing per
domain beyond DNS. The reference deployment uses `muninai.org`.

| Subdomain | Auth | Backend |
|-----------|------|---------|
| `<domain>` | None (public) | Static landing page |
| `auth.<domain>` | n/a | munin-auth login UI |
| `chat.<domain>` | Session cookie | React chat UI → gateway → backend |
| `docs.<domain>` | Session cookie | Static documentation |
| `search.<domain>` | Session cookie | Static + gateway → backend |
| `research.<domain>` | Session cookie | Retired standalone Deep Research; shows an "unavailable" page (Deep Research now runs inside chat) |
| `upload.<domain>` | Session cookie | Static + tusd |
| `api.<domain>` | API key (Bearer) | gateway → backend (OpenAI-compatible) |

## Quick Start (local development)

Run the whole stack on one machine from the repo root (see
[INSTALL.md](../INSTALL.md) for the details):

```bash
scripts/configure.sh --mode all --domain localhost --admin-email you@example.org \
    --llm-url http://host.docker.internal:11434 --llm-model qwen3:32b
docker compose up -d --build
docker compose logs munin-auth | grep "login code"
```

`--domain localhost` selects `caddy/Caddyfile.local` (everything on
`http://localhost`, no TLS), builds the chat UI in the `webui-build`
container, and echoes the login code to the auth log instead of
sending mail. `configure.sh` also creates the gitignored seed files
from their `.example`: `auth/whitelist.csv` (with you as admin),
`config/quotas.yml` and `../shared/config/contributors.yml`.

For UI work, `cd webui && npm install && npm run dev` runs the Vite
dev server. Use Node 22 (the build image is `node:22`; Vite 8 needs
20.19+ or 22.12+).

After first boot the auth DB inside the `auth_data` volume is the
source of truth: add / edit / remove users via the Admin panel
"Users" tab in the chat UI rather than the CSV.

## Deploy to VPS

This is how the reference deployment ships: the UI is built locally
and the tree is rsynced, with `~/munin/frontend/.env` (from
`.env.template`) on the VPS. Run from the monorepo root (so `shared/`
ships alongside `frontend/`; the auth compose mount references
`../shared/config/contributors.yml`).

```bash
# Build frontend first
cd frontend/webui && npm run build && cd ../..

# Sync the whole tree (no --delete: do not risk wiping VPS-only state).
rsync -avz --exclude '.env' --exclude '.git' --exclude 'node_modules' \
  ./ <admin>@<vps-ip>:~/munin/

# Prune stale hashed JS/CSS bundles in frontend/static/chat/assets/.
# Vite emits content-hashed filenames on every build, so old bundles
# accumulate without this step (~50 MB after a month of deploys).
# --delete is scoped to this single directory so it cannot affect
# anything else.
rsync -avz --delete \
  frontend/static/chat/assets/ \
  <admin>@<vps-ip>:~/munin/frontend/static/chat/assets/

# On VPS: rebuild and restart from the frontend/ project dir.
ssh <admin>@<vps-ip> 'cd ~/munin/frontend && docker compose up -d --build'

# Reload Caddy config (no restart needed).
# This works because caddy/ is bind-mounted as a directory, so the
# rsync'd Caddyfile is visible to the container. If you ever switch
# back to a single-file mount, rsync's inode swap makes this reload a
# silent no-op and you must `docker compose up -d --force-recreate caddy`
# instead.
ssh <admin>@<vps-ip> 'docker exec $(docker ps -qf name=caddy) caddy reload --config /etc/caddy/Caddyfile'
```

## Architecture

```
Users (browser / CLI tools)
    │ HTTPS
    ▼
VPS (public IP)
    │
    ├─ Caddy (:443)        auto TLS, routing
    │     │
    │     ├─ munin-auth (:8090)        email OTP, session cookies
    │     │
    │     ├─ api-gateway (:8070)       API keys, rate limits, usage logs
    │     │     │
    │     │     └─ proxy ──→ BACKEND_URL (default 127.0.0.1:18080,
    │     │                       the VPS end of the SSH tunnel) ──→ Cluster
    │     │
    │     ├─ tusd (:11080)             resumable uploads
    │     └─ hook-service (:18088)     file organization
    │
    └─ /mnt/uploads (MUNIN_UPLOADS_DIR; external volume on the reference VPS)

Backend: GPU server or cluster (no inbound)
    ├─ vLLM (:8000)              LLM inference
    ├─ Retrieval API (:8080)     RAG, search, deep research
    └─ autossh tunnel → VPS :18080
```

`BACKEND_URL` (old name `CLUSTER_TUNNEL`, still accepted) is the only
backend address the frontend uses. It must be a private path: a
tunnel, a VPN, or a LAN nobody else is on. The tunnel recipe is in
[`docs/install/tunnel.md`](../docs/install/tunnel.md). Optionally set
the same `MUNIN_GATEWAY_TOKEN` here and in the backend's env: Caddy
and the gateway then send it, and retrieval refuses forwarded
identity headers without it.

Shared secrets that must match the backend's env: `ADMIN_INGEST_TOKEN`,
`KB_GATE_TOKEN`, `CONTRIBUTORS_SYNC_TOKEN` and, if used,
`MUNIN_GATEWAY_TOKEN`. On a split install `configure.sh --mode backend`
writes them to `munin-peer.env` for `--mode frontend --peer-env`.

## Auth Flow (munin-auth)

Email-OTP authentication, no Authentik / PostgreSQL / Redis: one
FastAPI container plus SQLite.

1. User visits a protected subdomain → Caddy forward-auth → no
   session → 401.
2. Redirect to `auth.<your-domain>/login`.
3. User enters email → checked against the auth DB (seeded from
   `auth/whitelist.csv`, then managed in the Admin panel).
4. If the address is known → 6-digit OTP sent via SMTP.
5. User enters OTP → verified → signed session cookie set
   (30 days, `.<your-domain>` domain).
6. Redirect to original destination.

API-key requests bypass forward-auth and go straight to
`api-gateway`, which validates the bearer token.

## Status

What's running today:

- **munin-auth**: email-OTP auth.
- **api-gateway**: proxy, API keys, rate limiting, usage logging.
- **Chat UI**: React + TypeScript + Tailwind, SSE streaming, task
  execution log, feather vortex animation, PWA.
- **Shared style guide**: common stylesheet under
  `static/shared/style.css`, served at `/shared/*` across every
  subdomain by Caddy's `(shared-assets)` snippet.
- **Sleeping page**: off-hours display when vLLM is down (2 to 6 AM).
- **Maintenance page**: shown while the cluster is in maintenance
  mode (the `munin-maintenance` flag on the backend, reported by
  `/api/status`).
- **Caddyfile**: routing, forward-auth, gateway proxy, SPA
  fallback.
- **Docker Compose**: 5-service stack (Caddy, munin-auth,
  api-gateway, tusd, hook-service) plus the optional `webui-build`.
- **Bootstrap script**: VPS provisioning, safe to re-run (keys are added, not replaced).

## Repository Structure

```
frontend/                    # VPS-side of the monorepo
├── caddy/Caddyfile          # Reverse proxy config (subdomains + ACME)
├── caddy/Caddyfile.local    # Single-host config (http://localhost, by path)
├── auth/                    # Email-OTP auth (FastAPI); whitelist.csv.example
├── gateway/                 # API gateway (FastAPI)
├── upload/                  # Upload hook service (FastAPI)
├── config/quotas.yml.example  # Rate-limiting config (copy to quotas.yml)
├── webui/                   # Chat UI (React + TypeScript + Tailwind)
├── static/
│   ├── shared/              # Cross-subdomain assets served at /shared/* by Caddy
│   ├── chat/                # Built chat frontend (from webui/)
│   ├── landing/             # Public landing page
│   ├── docs/                # Documentation pages
│   ├── search/              # Paper search
│   ├── research/            # Retired Deep Research page ("unavailable")
│   ├── upload/              # Upload UI
│   └── maintenance/         # Maintenance page (reads /api/status)
├── docs/                    # Live design notes (+ docs/archive/ for shipped specs)
├── scripts/                 # backfill_contributed.py, build helpers
├── bootstrap.sh             # VPS provisioning script
├── .env.template            # Reference VPS env (copy to .env)
└── docker-compose.yml       # The 5 services + webui-build (profile webui)
```

The contract docs (`BACKEND-API.md`, `BACKEND-FRONTEND-SYNC.md`)
live in `../shared/docs/`. See the top-level `README.md`.

## Key Paths on VPS

After bootstrap and first deploy, expect:

- `~/munin/`: monorepo rsync target. Both `frontend/` and `shared/`
  ship here.
- `~/munin/frontend/.env`: secrets (not in git; copy from
  `.env.template` and fill in, `MUNIN_DOMAIN` included). Docker
  compose reads it from this path because compose lives at
  `~/munin/frontend/docker-compose.yml`.
- `~/munin/frontend/auth/whitelist.csv`: first-boot user seed
  file (gitignored; copy from `whitelist.csv.example`, header
  `email,first_name,last_name,role`; the old `email,name,role` is
  still read). After first start the DB inside the `auth_data` volume is
  the source of truth; the CSV is imported additively on every
  start (new emails only, existing rows never overwritten) and is
  kept as a backup / break-glass path.
- `~/munin/frontend/config/quotas.yml`: rate limits (gitignored;
  copy from `quotas.yml.example`).
- `/mnt/uploads/`: upload storage (external volume; `MUNIN_UPLOADS_DIR`).
  `staging/` and `complete/` must be owned by uid 1000 (tusd).

## Common Tasks

**Add / edit / remove a user (preferred):** sign in as an admin and
use the Admin panel "Users" tab in the chat UI. Supports multi-email
per user, role transitions (`user` / `group_leader` / `admin`), and
group assignment. Changes take effect immediately for the next request.

**Add a user via the CSV seed (break-glass only):** edit
`frontend/auth/whitelist.csv` and restart munin-auth from
`~/munin/frontend/` on the VPS:
`docker compose restart munin-auth`. The CSV is additive-only on
restart, so existing DB rows are never overwritten. Use this only
when the admin UI is unreachable; otherwise prefer the UI.

**Create API key:** `POST /api/keys` with session auth, returns
an `sk-munin-...` key.

**Check usage:** `GET /api/usage/me` returns monthly token usage
and breakdown.

**Update styling:** edit `static/shared/style.css`, applies to all
pages on next load.

**Rebuild chat UI:** `cd webui && npm run build`, outputs to
`static/chat/`. Then rsync to VPS. (Or `docker compose up --build
webui-build` on a host without Node.)

**Add a subdomain:**
1. A record at your DNS provider, pointing to the VPS IP.
2. Site block `<name>.{$MUNIN_DOMAIN}` in `caddy/Caddyfile` (use the
   `import auth` snippet for protected routes; landing pages skip
   it).
3. Reload Caddy: `docker exec $(docker ps -qf name=caddy) caddy
   reload --config /etc/caddy/Caddyfile`.

**Maintenance mode:** nothing to change on the VPS. Run
`sudo munin-maintenance on|off` on the backend; the chat UI and the
static `/maintenance` page follow the flag through `/api/status`
(see `../backend/README.md`).

**Fresh VPS setup:** see [INSTALL.md](../INSTALL.md) and, for the
reference deployment, [`docs/install/reference-deployment.md`](../docs/install/reference-deployment.md).

## Design Documents

The cluster-to-VPS tunnel recipe is in
[`docs/install/tunnel.md`](../docs/install/tunnel.md). Frozen
historical specs (features already shipped) are in
[`docs/archive/`](docs/archive/). The canonical API contract lives
at [`../shared/docs/BACKEND-API.md`](../shared/docs/BACKEND-API.md).

## Related

- `../backend/`: cluster-side of the monorepo (vLLM, retrieval API, RAG, paper pipeline).
- `../shared/`: cross-cut artifacts (personas, contributors.yml, contract docs).
- HuginSLURM (separate repo, not public): the reference cluster's base setup (CUDA, SLURM, storage).
