# Munin

Monorepo for the Munin AI platform. Two deploy targets sharing one source tree:

- **`backend/`** — runs on the SLURM cluster (`hugin`). vLLM, retrieval API,
  MCP tool server, chat persistence, paper pipeline, deep research daemon.
- **`frontend/`** — runs on the Hetzner VPS. Caddy, email-OTP auth,
  API gateway, file upload, static pages, React chat UI (`webui/`).
- **`shared/`** — single source of truth for cross-cut artifacts that both
  deploys consume:
  - `shared/personas/` — persona JSON + logos (read by `backend/deploy.sh`)
  - `shared/config/contributors.yml` — allowlist (cluster + VPS backfill)
  - `shared/docs/` — contracts and shared docs (`BACKEND-API.md`
    is canonical; `BACKEND-FRONTEND-SYNC.md` is the coordination
    log; `CONTRIBUTOR-INGEST.md` covers the upload→cluster flow;
    `DECISIONS.md` captures non-obvious design choices.
    `API-CONTRACT.md` is a redirect stub — retired in favour of
    `BACKEND-API.md`.)

## Architecture

```
Internet
   ▼
VPS (frontend/) — <vps-host>
   Caddy → munin-auth, api-gateway, tusd, hook-service, static UIs
                                     │
                                     │ autossh tunnel (VPS :18080 → cluster :8080)
                                     ▼
Cluster (backend/) — hugin
   retrieval API (:8080), vLLM, Qdrant, Neo4j, GROBID, SearXNG,
   deep research daemon, paper pipeline
```

The two sides share three contracts: HTTP API surface
(`shared/docs/BACKEND-API.md`), persona definitions
(`shared/personas/`), and the contributor allowlist
(`shared/config/contributors.yml`).

## Deploys

Each side has its own entrypoint — there is **no** top-level deploy script.

```bash
# Cluster (backend) — from the cluster head as root
cd backend && sudo ./deploy.sh all          # see deploy.sh for modes

# VPS (frontend) — from the VPS
cd frontend && docker compose up -d --build
```

See [`backend/CLAUDE.md`](backend/CLAUDE.md) and
[`frontend/CLAUDE.md`](frontend/CLAUDE.md) for per-side details.

## Contributing changes that cross the boundary

If you change a persona, an API endpoint, or the contributor format, edit
the file under `shared/` once. Do not duplicate. If a change requires
backend + frontend coordination, capture the answer in
[`shared/docs/BACKEND-FRONTEND-SYNC.md`](shared/docs/BACKEND-FRONTEND-SYNC.md).
