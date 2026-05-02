# munin-vps

Public gateway for the [Munin](https://muninai.org) AI research platform. Runs on a Hetzner VPS (CAX21, Ubuntu 24.04), reverse-proxying to a SLURM cluster via SSH tunnel.

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

## Repository Structure

```
munin-vps/
├── caddy/Caddyfile          # Reverse proxy config
├── auth/                    # Email OTP auth (FastAPI)
├── gateway/                 # API gateway (FastAPI)
├── upload/                  # Upload hook service (FastAPI)
├── config/quotas.yml        # Rate limiting config
├── webui/                   # Chat UI (React + TypeScript + Tailwind)
├── static/
│   ├── shared/              # CSS, logo, feather vortex animation
│   ├── chat/                # Built chat frontend (from webui/)
│   ├── landing/             # Public landing page
│   ├── docs/                # Documentation pages
│   ├── search/              # Paper search
│   ├── research/            # Deep research
│   ├── upload/              # Upload UI
│   └── maintenance/         # Temporary maintenance page
├── docs/                    # Design specs and architecture docs
├── bootstrap.sh             # VPS provisioning script
└── docker-compose.yml       # All 5 services
```

## Common Tasks

**Add a user:** Edit `auth/whitelist.csv`, restart munin-auth: `docker restart munin-auth`

**Create API key:** `POST /api/keys` with session auth, returns `sk-munin-...` key

**Check usage:** `GET /api/usage/me` returns monthly token usage and breakdown

**Update styling:** Edit `static/shared/style.css` — applies to all pages

**Rebuild chat UI:** `cd webui && npm run build` — outputs to `static/chat/`

## Design Documents

Architecture specs and future feature plans are in [`docs/`](docs/):

- [DESIGN.md](docs/DESIGN.md) — Overall architecture and upgrade plan
- [API-CONTRACT.md](docs/API-CONTRACT.md) — VPS ↔ cluster API surface
- [API-GATEWAY.md](docs/API-GATEWAY.md) — Gateway, API keys, usage tracking
- [CHAT-PERSISTENCE.md](docs/CHAT-PERSISTENCE.md) — Chat storage and context memory
- [CHAT-UI-TASK-LOG.md](docs/CHAT-UI-TASK-LOG.md) — Loading messages and execution log
- [FEATHER-VORTEX.md](docs/FEATHER-VORTEX.md) — Animated loading indicator spec
- [SLEEPING-PAGE.md](docs/SLEEPING-PAGE.md) — Off-hours display
- [ADDITIONAL-FEATURES.md](docs/ADDITIONAL-FEATURES.md) — PWA, memory, search enhancements
- [AGENTIC-ORCHESTRATION.md](docs/AGENTIC-ORCHESTRATION.md) — Agent workflows
- [USER-DOCUMENTS.md](docs/USER-DOCUMENTS.md) — Per-user document store

## Related Repos

- **munin-backend** — AI backend on the SLURM cluster (vLLM, retrieval API, RAG)
- **HuginSLURM** — Base cluster setup (CUDA, SLURM, storage)
