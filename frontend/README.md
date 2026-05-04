# munin / frontend (VPS)

VPS-side of the [Munin](https://muninai.org) AI research monorepo. Runs on a Hetzner VPS (CAX21, Ubuntu 24.04), reverse-proxying to a SLURM cluster via SSH tunnel. Cluster-side code lives in `../backend/`; cross-cut artifacts in `../shared/`.

## Services

| Service | Port | Purpose |
|---------|------|---------|
| **Caddy** | 80, 443 | Reverse proxy, auto TLS, forward-auth |
| **munin-auth** | 8090 | Email OTP authentication |
| **api-gateway** | 8070 | API key validation, rate limiting, usage logging, proxy |
| **tusd** | 11080 | Resumable file uploads |
| **hook-service** | 18088 | Post-upload file organization |

## Subdomains

| Subdomain | Auth | Backend |
|-----------|------|---------|
| `muninai.org` | None (public) | Static landing page |
| `auth.muninai.org` | — | munin-auth login UI |
| `chat.muninai.org` | Session cookie | React chat UI → gateway → cluster |
| `docs.muninai.org` | Session cookie | Static documentation |
| `search.muninai.org` | Session cookie | Static + gateway → cluster |
| `research.muninai.org` | Session cookie | Static + gateway → cluster |
| `upload.muninai.org` | Session cookie | Static + tusd |
| `api.muninai.org` | API key (Bearer) | gateway → cluster (OpenAI-compatible) |

## Quick Start

```bash
# 1. Configure secrets
cp .env.template .env
# Fill in: AUTH_SECRET_KEY (openssl rand -hex 32), SMTP credentials, ADMIN_EMAILS

# 2. Add users
vi auth/whitelist.csv   # email,name per row

# 3. Build the chat frontend
cd webui && npm install && npm run build && cd ..

# 4. Start all services
docker compose up -d --build
```

## Deploy to VPS

```bash
# Build frontend first
cd webui && npm run build && cd ..

# Sync to VPS (excludes secrets and git)
rsync -avz --exclude '.env' --exclude '.git' --exclude 'node_modules' \
  ./ varghele@<vps-host>:~/munin/

# On VPS: rebuild and restart
cd ~/munin && docker compose up -d --build

# Reload Caddy config (no restart needed)
docker exec $(docker ps -qf name=caddy) caddy reload --config /etc/caddy/Caddyfile
```

## Architecture

```
Users (browser / CLI tools)
    │ HTTPS
    ▼
Hetzner VPS (<vps-host>)
    │
    ├─ Caddy (:443) ─── auto TLS, routing
    │     │
    │     ├─ munin-auth (:8090) ─── email OTP, session cookies
    │     │
    │     ├─ api-gateway (:8070) ─── API keys, rate limits, usage logs
    │     │     │
    │     │     └─ proxy ──→ SSH tunnel (:18080) ──→ Cluster
    │     │
    │     ├─ tusd (:11080) ─── resumable uploads
    │     └─ hook-service (:18088) ─── file organization
    │
    └─ /mnt/uploads (Hetzner Volume)

SLURM Cluster (university, no inbound)
    ├─ vLLM (:8000) — LLM inference
    ├─ Retrieval API (:8080) — RAG, search, deep research
    └─ autossh tunnel → VPS :18080
```

## Auth Flow (munin-auth)

Email-OTP authentication, no Authentik / PostgreSQL / Redis — one
FastAPI container plus SQLite:

1. User visits a protected subdomain → Caddy forward-auth → no
   session → 401.
2. Redirect to `auth.muninai.org/login`.
3. User enters email → checked against `auth/whitelist.csv`.
4. If whitelisted → 6-digit OTP sent via SMTP (HostEurope).
5. User enters OTP → verified → signed session cookie set
   (30 days, `.muninai.org` domain).
6. Redirect to original destination.

API-key requests bypass forward-auth and go straight to
`api-gateway`, which validates the bearer token.

## Status

What's running today:

- **munin-auth** — Email-OTP auth.
- **api-gateway** — Proxy, API keys, rate limiting, usage logging.
- **Chat UI** — React + TypeScript + Tailwind, SSE streaming, task
  execution log, feather vortex animation, PWA.
- **Shared style guide** — Common stylesheet under
  `static/shared/style.css`, served at `/shared/*` across every
  subdomain by Caddy's `(shared-assets)` snippet.
- **Sleeping page** — Off-hours display when vLLM is down (2–6 AM).
- **Maintenance page** — Temporary page while the cluster is being
  upgraded.
- **Caddyfile** — Routing, forward-auth, gateway proxy, SPA
  fallback.
- **Docker Compose** — 5-service stack (Caddy, munin-auth,
  api-gateway, tusd, hook-service).
- **Bootstrap script** — Idempotent VPS provisioning.

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
live in `../shared/docs/` — see the top-level `CLAUDE.md`.

## Key Paths on VPS

After bootstrap + first deploy, expect:

- `~/munin/` — project directory (rsync target)
- `~/munin/.env` — secrets (not in git; copy from a trusted
  source or regenerate)
- `~/munin/auth/whitelist.csv` — user whitelist (`email,name`
  per row)
- `/mnt/uploads/` — upload storage (Hetzner Volume)

## VPS Details

- **IP:** <vps-host>
- **Admin user:** `varghele` (SSH key: `~/.ssh/munin_admin`)
- **Tunnel user:** `tunnel` (port-forwarding only, restricted shell)
- **Domain:** `muninai.org` (registrar + DNS at HostEurope)

## Common Tasks

**Add a user:** Edit `auth/whitelist.csv`, restart munin-auth:
`docker restart munin-auth`

**Create API key:** `POST /api/keys` with session auth, returns
`sk-munin-...` key.

**Check usage:** `GET /api/usage/me` returns monthly token usage
and breakdown.

**Update styling:** Edit `static/shared/style.css` — applies to all
pages on next load.

**Rebuild chat UI:** `cd webui && npm run build` — outputs to
`static/chat/`. Then rsync to VPS.

**Add a subdomain:**
1. A record in HostEurope DNS → VPS IP.
2. Route block in `caddy/Caddyfile` (use the `import auth` snippet
   for protected routes; landing pages skip it).
3. Reload Caddy: `docker exec $(docker ps -qf name=caddy) caddy
   reload --config /etc/caddy/Caddyfile`.

**Revert maintenance mode:** In `caddy/Caddyfile`, find the
`MAINTENANCE` comments on `chat.muninai.org` and
`research.muninai.org`. Uncomment the real routes, remove the
maintenance blocks. Reload Caddy.

**Fresh VPS setup:**
1. Create VPS in Hetzner with SSH key.
2. `ssh root@VPS_IP` → run `bootstrap.sh`.
3. `ssh varghele@VPS_IP` → clone monorepo, copy `.env`, build chat
   UI, `docker compose up -d --build`.
4. DNS A records in HostEurope.

## Design Documents

Live design notes in [`docs/`](docs/):

- [ADDITIONAL-FEATURES.md](docs/ADDITIONAL-FEATURES.md) — PWA, user memory, BM25, compaction
- [AGENTIC-ORCHESTRATION.md](docs/AGENTIC-ORCHESTRATION.md) — Agent registry and orchestration
- [hugin-tunnel-setup.md](docs/hugin-tunnel-setup.md) — Cluster-side tunnel install runbook

Frozen historical specs (features already shipped) are in
[`docs/archive/`](docs/archive/). The canonical API contract lives
at [`../shared/docs/BACKEND-API.md`](../shared/docs/BACKEND-API.md).

## Related

- `../backend/` — cluster-side of the monorepo (vLLM, retrieval API, RAG, paper pipeline)
- `../shared/` — cross-cut artifacts (personas, contributors.yml, contract docs)
- HuginSLURM (separate repo) — base cluster setup (CUDA, SLURM, storage)
