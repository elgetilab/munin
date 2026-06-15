# munin / frontend (VPS)

VPS-side of the [Munin](https://muninai.org) AI research monorepo. Runs on a Linux VPS, reverse-proxying to a SLURM cluster via SSH tunnel. Cluster-side code lives in `../backend/`; cross-cut artifacts in `../shared/`.

For first-time setup on a new VPS, follow the top-level [SETUP-VPS.md](../SETUP-VPS.md). This README is the operating reference once the deploy is in place. The reference deploy runs on a Hetzner CAX21 (Ubuntu 24.04, 4 vCPU ARM, 8 GB RAM) plus a 50 GB volume mounted at `/mnt/uploads`.

## Services

| Service | Port | Purpose |
|---------|------|---------|
| **Caddy** | 80, 443 | Reverse proxy, auto TLS, forward-auth |
| **munin-auth** | 8090 | Email OTP authentication |
| **api-gateway** | 8070 | API key validation, rate limiting, usage logging, proxy |
| **tusd** | 11080 | Resumable file uploads |
| **hook-service** | 18088 | Post-upload file organization |

## Subdomains

The reference deploy uses `muninai.org`. Replace with your own domain throughout the Caddyfile and DNS records.

| Subdomain | Auth | Backend |
|-----------|------|---------|
| `muninai.org` | None (public) | Static landing page |
| `auth.muninai.org` | n/a | munin-auth login UI |
| `chat.muninai.org` | Session cookie | React chat UI → gateway → cluster |
| `docs.muninai.org` | Session cookie | Static documentation |
| `search.muninai.org` | Session cookie | Static + gateway → cluster |
| `research.muninai.org` | Session cookie | Static + gateway → cluster |
| `upload.muninai.org` | Session cookie | Static + tusd |
| `api.muninai.org` | API key (Bearer) | gateway → cluster (OpenAI-compatible) |

## Quick Start (local development)

All commands are run from inside `frontend/`.

```bash
# 1. Configure secrets
cp .env.template .env
# Fill in: AUTH_SECRET_KEY (openssl rand -hex 32), SMTP credentials,
# ADMIN_EMAILS, ADMIN_INGEST_TOKEN, KB_GATE_TOKEN, CONTRIBUTORS_SYNC_TOKEN.
# The last THREE all pair with the cluster's cluster.env (same values both sides).
# KB_GATE_TOKEN was originally VPS-only; since 2026-06-02 the cluster's metrics
# proxy also reads it -- see shared/docs/MONITORING.md.

# 2. Seed the user list (first boot only)
vi auth/whitelist.csv   # email,name,role per row

# 3. Build the chat frontend
cd webui && npm install && npm run build && cd ..

# 4. Start all services
docker compose up -d --build
```

After first boot the auth DB inside the `auth_data` volume is the
source of truth: add / edit / remove users via the Admin panel
"Users" tab in the chat UI rather than the CSV.

## Deploy to VPS

Run from the monorepo root (so `shared/` ships alongside `frontend/`;
the auth compose mount references `../shared/config/contributors.yml`).

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
    │     │     └─ proxy ──→ SSH tunnel (:18080) ──→ Cluster
    │     │
    │     ├─ tusd (:11080)             resumable uploads
    │     └─ hook-service (:18088)     file organization
    │
    └─ /mnt/uploads (external volume)

SLURM Cluster (no inbound)
    ├─ vLLM (:8000)              LLM inference
    ├─ Retrieval API (:8080)     RAG, search, deep research
    └─ autossh tunnel → VPS :18080
```

## Auth Flow (munin-auth)

Email-OTP authentication, no Authentik / PostgreSQL / Redis: one
FastAPI container plus SQLite.

1. User visits a protected subdomain → Caddy forward-auth → no
   session → 401.
2. Redirect to `auth.<your-domain>/login`.
3. User enters email → checked against `auth/whitelist.csv`.
4. If whitelisted → 6-digit OTP sent via SMTP.
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
- **Maintenance page**: temporary page while the cluster is being
  upgraded.
- **Caddyfile**: routing, forward-auth, gateway proxy, SPA
  fallback.
- **Docker Compose**: 5-service stack (Caddy, munin-auth,
  api-gateway, tusd, hook-service).
- **Bootstrap script**: idempotent VPS provisioning.

Specs that haven't shipped yet are in
[`docs/ADDITIONAL-FEATURES.md`](docs/ADDITIONAL-FEATURES.md) and
[`docs/AGENTIC-ORCHESTRATION.md`](docs/AGENTIC-ORCHESTRATION.md).

## Repository Structure

```
frontend/                    # VPS-side of the monorepo
├── caddy/Caddyfile          # Reverse proxy config
├── auth/                    # Email-OTP auth (FastAPI)
├── gateway/                 # API gateway (FastAPI)
├── upload/                  # Upload hook service (FastAPI)
├── config/quotas.yml        # Rate-limiting config
├── webui/                   # Chat UI (React + TypeScript + Tailwind)
├── static/
│   ├── shared/              # Cross-subdomain assets served at /shared/* by Caddy
│   ├── chat/                # Built chat frontend (from webui/)
│   ├── landing/             # Public landing page
│   ├── docs/                # Documentation pages
│   ├── search/              # Paper search
│   ├── research/            # Deep research
│   ├── upload/              # Upload UI
│   └── maintenance/         # Temporary maintenance page
├── docs/                    # Live design notes (+ docs/archive/ for shipped specs)
├── scripts/                 # backfill_contributed.py, build helpers
├── bootstrap.sh             # VPS provisioning script
└── docker-compose.yml       # All 5 services
```

The contract docs (`BACKEND-API.md`, `BACKEND-FRONTEND-SYNC.md`)
live in `../shared/docs/`. See the top-level `CLAUDE.md`.

## Key Paths on VPS

After bootstrap and first deploy, expect:

- `~/munin/`: monorepo rsync target. Both `frontend/` and `shared/`
  ship here.
- `~/munin/frontend/.env`: secrets (not in git; copy from
  `.env.template` and fill in). Docker compose reads it from this
  path because compose lives at `~/munin/frontend/docker-compose.yml`.
- `~/munin/frontend/auth/whitelist.csv`: first-boot user seed
  file. After first start the DB inside the `auth_data` volume is
  the source of truth; the CSV is imported additively on every
  start (new emails only, existing rows never overwritten) and is
  kept as a backup / break-glass path.
- `/mnt/uploads/`: upload storage (external volume).

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
`static/chat/`. Then rsync to VPS.

**Add a subdomain:**
1. A record at your DNS provider, pointing to the VPS IP.
2. Route block in `caddy/Caddyfile` (use the `import auth` snippet
   for protected routes; landing pages skip it).
3. Reload Caddy: `docker exec $(docker ps -qf name=caddy) caddy
   reload --config /etc/caddy/Caddyfile`.

**Revert maintenance mode:** in `caddy/Caddyfile`, find the
`MAINTENANCE` comments on `chat.<your-domain>` and
`research.<your-domain>`. Uncomment the real routes, remove the
maintenance blocks. Reload Caddy.

**Fresh VPS setup:** see the top-level [SETUP-VPS.md](../SETUP-VPS.md).

## Design Documents

Live design notes in [`docs/`](docs/):

- [ADDITIONAL-FEATURES.md](docs/ADDITIONAL-FEATURES.md): PWA, user memory, BM25, compaction.
- [AGENTIC-ORCHESTRATION.md](docs/AGENTIC-ORCHESTRATION.md): agent registry and orchestration.
- [hugin-tunnel-setup.md](docs/hugin-tunnel-setup.md): cluster-side tunnel install runbook.

Frozen historical specs (features already shipped) are in
[`docs/archive/`](docs/archive/). The canonical API contract lives
at [`../shared/docs/BACKEND-API.md`](../shared/docs/BACKEND-API.md).

## Related

- `../backend/`: cluster-side of the monorepo (vLLM, retrieval API, RAG, paper pipeline).
- `../shared/`: cross-cut artifacts (personas, contributors.yml, contract docs).
- HuginSLURM (separate repo): base cluster setup (CUDA, SLURM, storage).
