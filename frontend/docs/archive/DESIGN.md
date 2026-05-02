# Munin VPS — Design Document

## Repository: `munin-vps`

**Purpose:** Everything frontend-facing that lives on the Hetzner VPS (CAX21). This is the public gateway to the Munin AI research platform — reverse proxy, authentication, static pages, file uploads, and the custom chat UI.

---

## Current State (from `munin-vps/`)

### What Exists Today

The VPS at `<vps-host>` runs a Docker Compose stack that handles:

- **Caddy** — Reverse proxy with automatic TLS for all `*.muninai.org` subdomains.
- **Authentik** (server + worker + PostgreSQL + Redis) — Identity provider with forward-auth, self-registration with email whitelist. Being replaced by munin-auth (see below).
- **tusd + hook-service** — Chunked file upload server (PDF papers). tusd handles resumable uploads, the hook service (FastAPI) moves completed files into per-user directories.
- **Static pages** — Landing page, paper search, deep research, docs/API reference, upload page. All share a consistent dark theme.
- **Bootstrap script** — `bootstrap.sh` automates fresh VPS provisioning.

### Domain & Email

- **Domain:** `muninai.org` hosted on HostEurope. Email server also on HostEurope.
- **DNS:** HostEurope, A records pointing to VPS IP.
- **VPS:** Hetzner CAX21, Ubuntu 24.04.

### Subdomains and Routing

| Subdomain | Backend | Auth | Purpose |
|-----------|---------|------|---------|
| `muninai.org` | Static landing page | **No** | Public — accessible to everyone |
| `auth.muninai.org` | munin-auth service | — | Login page (email OTP) |
| `chat.muninai.org` | Custom web UI (future) | Yes | Chat interface |
| `docs.muninai.org` | Static files | Yes | Architecture, API docs |
| `search.muninai.org` | Static + cluster tunnel | Yes | Paper search tool |
| `research.muninai.org` | Static + cluster tunnel | Yes | Deep research tool |
| `upload.muninai.org` | Static + tusd | Yes | Paper upload |

**Key rule:** The landing page (`muninai.org`) is public. Every other subdomain is behind authentication.

---

## What to Keep

### 1. Static Pages — All of Them

The static pages work well and serve distinct functions. They stay:

- **Landing page** (`muninai.org`) — Public overview of the platform. Links to chat, search, research, upload.
- **Paper search** (`search.muninai.org`) — Interactive search UI backed by the retrieval API. Users search by topic, author, year, DOI. Results ranked semantically.
- **Deep research** (`research.muninai.org`) — Job submission UI for complex research questions. Uses MiroThinker model on GPU 0. Shows status, queue, outputs as Markdown/PDF.
- **Docs/API** (`docs.muninai.org`) — Architecture diagram, API reference, persona comparison.
- **Upload** (`upload.muninai.org`) — Resumable PDF upload with Uppy/tusd.

These are not being replaced or absorbed into the chat UI. They are standalone tools. The only new page being built is the **chat interface** (replacing Open WebUI).

### 2. Shared Visual Style

All existing pages already share a consistent dark theme with common CSS variables:

```css
:root {
    --bg-primary: #0f1419;
    --bg-secondary: #1a1f26;
    --bg-tertiary: #242a33;
    --text-primary: #e6edf3;
    --text-secondary: #8b949e;
    --accent: #58a6ff;
    --accent-hover: #79b8ff;
    --border: #30363d;
    --success: #3fb950;
    --warning: #d29922;
    --error: #f85149;
}
```

They also share a common header/footer/nav structure with the Munin logo and navigation links.

**Action:** Extract the shared CSS into a single stylesheet (`/srv/static/shared/style.css`) and a shared header/footer partial. All pages (including the new chat UI and the munin-auth login page) import this. One place to update colors, fonts, spacing, branding.

The style guide file should document: color palette, typography (system font stack), spacing scale, component patterns (cards, buttons, forms, tables, code blocks), header/footer/nav structure, responsive breakpoints.

### 3. Caddy

Clean Caddyfile, automatic TLS, host-mode networking. The `(auth)` snippet pattern stays but targets munin-auth instead of Authentik.

### 4. tusd + hook-service

File upload pipeline. Keep as-is, update to read `X-Munin-Email` header.

### 5. Bootstrap script

Simplified (fewer packages without Authentik).

### 6. SSH tunnel architecture

autossh reverse tunnel from cluster to VPS.

---

## What to Discard

1. **Authentik** — All of it: server, worker, PostgreSQL, Redis, blueprint YAML files. Replaced by munin-auth.
2. **Open WebUI** — Replaced by the custom chat frontend at `chat.muninai.org`.
3. **Authentik blueprints** (`authentik/*.yaml`) — No longer relevant.

---

## New: Email OTP Authentication (munin-auth)

### The Design

Replace Authentik with a lightweight FastAPI service. Email-based OTP login, like Cloudflare Access.

**Flow:**
1. User visits any protected subdomain (e.g., `search.muninai.org`)
2. Caddy forward-auth → no valid session cookie → 401
3. Redirect to `auth.muninai.org/login?redirect=search.muninai.org`
4. User enters their email address
5. munin-auth checks email against the whitelist CSV
6. If whitelisted: 6-digit OTP sent via SMTP (HostEurope), stored with 10-minute expiry
7. User enters OTP
8. munin-auth verifies, sets signed session cookie (`munin_session`, 30 days, `.muninai.org`)
9. Redirect to original destination

**What this replaces:** Authentik (5 containers) → munin-auth (1 container). No PostgreSQL, no Redis.

### Whitelist & User Data

User management is a single CSV file (`whitelist.csv`), mounted as a volume:

```csv
email,name
user900@example.org,Person900
jane.doe@example.com,Jane Doe
```

This is the single source of truth for:
- **Who can log in** — email must be in the CSV
- **Display name** — the `name` column, forwarded as `X-Munin-Name` header

To add a user: add a row to the CSV, restart munin-auth (or send SIGHUP to reload).

### munin-auth Specification

**Stack:** Python 3.12, FastAPI, uvicorn

**Endpoints:**

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/login` | GET | Login page (enter email) — uses shared Munin styling |
| `/login` | POST | Submit email → check whitelist → send OTP |
| `/verify` | GET | OTP entry page |
| `/verify` | POST | Verify OTP → set session cookie |
| `/auth/check` | GET | Caddy forward-auth: 200 + headers or 401 |
| `/logout` | POST | Clear session cookie |
| `/health` | GET | Health check |

**Data storage:**
- **Whitelist:** CSV file (`/data/whitelist.csv`), mounted read-only. Reloaded on SIGHUP or container restart.
- **Sessions:** SQLite (`/data/sessions.db`). Table: `sessions(token, email, name, created_at, expires_at)`.
- **OTP codes:** In-memory dict with TTL. No persistence — codes expire in 10 minutes.

**Session cookie:**
- Name: `munin_session`, Domain: `.muninai.org`
- Secure, HttpOnly, SameSite=Lax
- Max-Age: 30 days
- Value: HMAC-SHA256 signed session token

**Caddy integration:**

```
(auth) {
    forward_auth 127.0.0.1:8090 {
        uri /auth/check
        copy_headers X-Munin-Email X-Munin-Name
    }
}
```

On valid session: returns `200` with `X-Munin-Email` and `X-Munin-Name` (from the CSV).
On invalid/missing: returns `401`. Caddy redirects to login.

**Rate limiting:** 3 OTP requests per email per 15 min. 5 failed verifications per email per 15 min.

**SMTP:** Sends from `noreply@muninai.org` via HostEurope SMTP. Simple branded email with 6-digit code and expiry notice.

**Login page styling:** Uses the shared Munin dark theme (`style.css`). Same look and feel as all other pages.

### Updated Docker Compose

```yaml
services:
  caddy:
    image: caddy:2-alpine
    restart: unless-stopped
    network_mode: host
    volumes:
      - ./caddy/Caddyfile:/etc/caddy/Caddyfile:ro
      - ./static:/srv/static:ro
      - caddy_data:/data
      - caddy_config:/config

  munin-auth:
    build: ./auth
    restart: unless-stopped
    ports:
      - "127.0.0.1:8090:8090"
    volumes:
      - ./auth/whitelist.csv:/data/whitelist.csv:ro
      - auth_data:/data/db
    environment:
      SECRET_KEY: ${AUTH_SECRET_KEY}
      SMTP_HOST: ${SMTP_HOST}
      SMTP_PORT: ${SMTP_PORT}
      SMTP_USERNAME: ${SMTP_USERNAME}
      SMTP_PASSWORD: ${SMTP_PASSWORD}
      SMTP_SENDER: ${SMTP_SENDER:-noreply@muninai.org}
      SESSION_MAX_AGE: 2592000
      OTP_EXPIRY: 600

  tusd:
    image: tusproject/tusd:latest
    restart: unless-stopped
    network_mode: host
    volumes:
      - /mnt/uploads/staging:/srv/tusd-data
    command: >
      -upload-dir=/srv/tusd-data
      -hooks-http=http://127.0.0.1:18088/hooks
      -hooks-http-forward-headers=X-Munin-Email,X-Munin-Name
      -port=11080
      -host=127.0.0.1
      -max-size=10737418240
      -behind-proxy
    depends_on:
      - hook-service

  hook-service:
    build: ./upload
    restart: unless-stopped
    network_mode: host
    volumes:
      - /mnt/uploads:/mnt/uploads

volumes:
  caddy_data:
  caddy_config:
  auth_data:
```

---

## Upgrade Plan

### Phase 1: Repository Setup

```
munin-vps/
├── README.md
├── DESIGN.md                    ← This document
├── bootstrap.sh
├── docker-compose.yml
├── .env.template
├── caddy/
│   └── Caddyfile
├── auth/                        ← Email OTP service
│   ├── Dockerfile
│   ├── main.py
│   ├── whitelist.csv
│   └── templates/
│       ├── login.html
│       ├── verify.html
│       └── email_otp.html
├── upload/
│   ├── Dockerfile
│   └── hook_service.py
├── static/
│   ├── shared/                  ← NEW: shared style guide
│   │   ├── style.css            ← Extracted common CSS
│   │   ├── header.html          ← Common header/nav partial
│   │   └── footer.html          ← Common footer partial
│   ├── landing/                 ← muninai.org (public)
│   ├── docs/                    ← docs.muninai.org
│   ├── search/                  ← search.muninai.org
│   ├── research/                ← research.muninai.org
│   └── upload/                  ← upload.muninai.org
└── frontend/                    ← Custom chat UI (Phase 4)
```

### Phase 2: Shared Style Guide

Extract the common CSS, header, footer, and nav from the existing pages into `static/shared/`. Update all existing pages to import from there instead of having inline styles. This is mostly mechanical: the CSS variables and layout patterns already exist, they just need to be deduplicated.

The style guide becomes the reference for:
- The login/verify pages in munin-auth
- The new chat frontend
- Any future pages

### Phase 3: Build munin-auth

Build the email OTP service (~300 lines of Python). Uses the shared style for login/verify templates. Reads whitelist from CSV file with email and name columns.

### Phase 4: Custom Chat UI

Build the React chat frontend at `chat.muninai.org`, replacing Open WebUI. Uses the shared style guide for consistency. Talks to the cluster retrieval API via the tunnel. This is the only new "page" being built — search, research, docs, upload all stay as they are.

---

## Connection to Other Repositories

```
munin-vps (this repo)          munin-backend (cluster)         HuginSLURM (cluster base)
─────────────────────          ────────────────────────         ─────────────────────────
Caddy + munin-auth             vLLM (port 8000)                SLURM setup
Static pages (all kept)        Retrieval API (port 8080)       CUDA/drivers
Custom chat UI (new)           Qdrant, Neo4j, GROBID           User management
tusd + hook-service            SearXNG, Deep Research          Storage/quotas
Landing page (public)          MCP server, Paper pipeline
   │
   │ SSH tunnel (:18080)
   └──────────────────────►
```

The search and research pages talk to the retrieval API at `127.0.0.1:18080` via Caddy's `handle_path /api/*` → tunnel. This routing stays exactly the same.
