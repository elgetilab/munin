# Migration Guide — munin-vps

## Overview

This guide walks through extracting VPS files into the new `munin-vps` repository **and replacing Authentik with a lightweight email-OTP auth service**.

**Key change from current state:** Authentik (5 containers) is replaced by `munin-auth` (1 container). The entire PostgreSQL + Redis + Authentik server + worker stack goes away.

**Prerequisites:**
- SSH access to the VPS (`ssh -i ~/.ssh/munin_admin varghele@<vps-host>`)
- GitHub account with repo creation permissions
- SMTP credentials for HostEurope email server (for sending OTP codes)
- The current VPS is running and healthy

**Estimated time:** 4–5 hours (includes building munin-auth and testing)

**Risk level:** Medium. We're replacing the auth stack, so there will be a brief period where users need to re-authenticate. Plan to do this during a low-usage window.

---

## Phase 1: Snapshot Current State

### 1.1 — Document VPS state

```bash
ssh -i ~/.ssh/munin_admin varghele@<vps-host>

# Record running containers
docker compose -f ~/munin/docker-compose.yml ps > /tmp/vps-containers.txt

# Record Authentik user list (for whitelist migration)
# Get this from Authentik admin: https://auth.muninai.org/if/admin/ → Directory → Users
# Export the email list manually — you'll need it for whitelist.csv

# Record docker volumes
docker volume ls > /tmp/vps-volumes.txt

# Check disk usage
df -h /mnt/uploads

exit
```

### 1.2 — Backup

```bash
mkdir -p ~/backups/munin-vps
rsync -avz -e "ssh -i ~/.ssh/munin_admin" \
    varghele@<vps-host>:~/munin/ \
    ~/backups/munin-vps/$(date +%Y%m%d)/
```

### 1.3 — Collect Authentik user emails

From the Authentik admin panel, note down every registered user's email and name. These go into `whitelist.csv` for the new auth service. If you have a whitelist policy in Authentik, you already have the email list.

---

## Phase 2: Create the Repository

### 2.1 — Initialize

```bash
cd ~/Projects
mkdir munin-vps && cd munin-vps
git init
```

### 2.2 — Create directory structure

```bash
mkdir -p caddy
mkdir -p auth/templates
mkdir -p upload
mkdir -p static/landing
```

### 2.3 — Copy files from current source

```bash
SRC=~/PycharmProjects/HuginSLURM/vps

# Core
cp "$SRC/bootstrap.sh"           ./bootstrap.sh

# Caddy (will be modified)
cp "$SRC/caddy/Caddyfile"        ./caddy/Caddyfile

# Upload service
cp "$SRC/upload/Dockerfile"       ./upload/Dockerfile
cp "$SRC/upload/hook_service.py"  ./upload/hook_service.py

# Static landing page
cp -r "$SRC/static/landing/"*     ./static/landing/ 2>/dev/null || true
```

**Do NOT copy:** `authentik/` directory, `docker-compose.yml` (will be rewritten).

### 2.4 — Create the whitelist

```bash
cat > auth/whitelist.csv << 'EOF'
email,name
user900@example.org,Person900
# Add more users here
EOF
```

### 2.5 — Build munin-auth

Create the auth service:

**`auth/Dockerfile`:**
```dockerfile
FROM python:3.12-slim
WORKDIR /app
RUN pip install fastapi uvicorn jinja2 itsdangerous aiosmtplib python-multipart --no-cache-dir
COPY main.py .
COPY templates/ /app/templates/
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8090"]
```

**`auth/main.py`** — The core auth service (~300 lines). Key components:
- `POST /login` — Check email against whitelist, generate 6-digit OTP, send via SMTP
- `POST /verify` — Validate OTP, create session in SQLite, set signed cookie
- `GET /auth/check` — Caddy forward-auth endpoint: validate cookie → 200 with headers or 401
- `POST /logout` — Delete session, clear cookie
- In-memory OTP store with 10-minute expiry
- SQLite sessions table with 30-day expiry
- Rate limiting: 3 OTP sends per email per 15 min, 5 failed verifications per 15 min

**`auth/templates/login.html`** — Simple email entry form (matches landing page dark theme)

**`auth/templates/verify.html`** — OTP code entry form

**`auth/templates/email_otp.html`** — Email template for the OTP code

### 2.6 — Write new docker-compose.yml

```bash
cat > docker-compose.yml << 'EOF'
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
EOF
```

### 2.7 — Update Caddyfile

Replace the Authentik-based `(auth)` snippet:

```
{
    email info@muninai.org
}

(auth) {
    forward_auth 127.0.0.1:8090 {
        uri /auth/check
        copy_headers X-Munin-Email X-Munin-Name
    }
}

muninai.org {
    root * /srv/static/landing
    file_server
}

auth.muninai.org {
    reverse_proxy 127.0.0.1:8090
}

chat.muninai.org {
    import auth
    reverse_proxy 127.0.0.1:18080 {
        header_up X-Munin-Email {http.request.header.X-Munin-Email}
        header_up X-Munin-Name {http.request.header.X-Munin-Name}
    }
}

search.muninai.org {
    import auth
    handle_path /api/* {
        reverse_proxy 127.0.0.1:18080
    }
    handle {
        root * /srv/static/search
        file_server
    }
}

research.muninai.org {
    import auth
    handle_path /api/* {
        reverse_proxy 127.0.0.1:18080
    }
    handle {
        root * /srv/static/research
        file_server
    }
}

upload.muninai.org {
    import auth
    handle /whoami {
        header Content-Type application/json
        respond `{"email":"{http.request.header.X-Munin-Email}"}` 200
    }
    handle /files/* {
        reverse_proxy 127.0.0.1:11080
    }
    handle {
        root * /srv/static/upload
        file_server
    }
}
```

### 2.8 — Update hook_service.py

Change the header it reads from `X-Authentik-Email` to `X-Munin-Email`:

```python
email_list = http_headers.get("X-Munin-Email", http_headers.get("X-Authentik-Email", []))
```

(Accept both during transition.)

### 2.9 — Update bootstrap.sh

Simplify — remove Authentik-specific package installs. The bootstrap script no longer needs to prepare for PostgreSQL or Redis.

### 2.10 — Create .env.template

```bash
cat > .env.template << 'EOF'
# ==============================================================================
# MUNIN VPS — ENVIRONMENT VARIABLES
# ==============================================================================
# Copy to .env and fill in values. NEVER commit .env to git.
# Generate secret: openssl rand -hex 32
# ==============================================================================

# ── Auth Service ──────────────────────────────────────────────────────────────
AUTH_SECRET_KEY=__GENERATE_WITH_OPENSSL__

# ── Email (HostEurope SMTP for OTP delivery) ─────────────────────────────────
SMTP_HOST=__HOSTEUROPE_SMTP_HOST__
SMTP_PORT=587
SMTP_USERNAME=__HOSTEUROPE_EMAIL_USER__
SMTP_PASSWORD=__HOSTEUROPE_EMAIL_PASSWORD__
SMTP_SENDER=noreply@muninai.org
EOF
```

### 2.11 — Initial commit and push

```bash
git add -A
git commit -m "Initial commit: VPS infrastructure with email-OTP auth

Replaces Authentik (5 containers) with munin-auth (1 container).
Auth flow: email whitelist → OTP via SMTP → 30-day session cookie.
Contains: Caddy, munin-auth, tusd, hook-service, landing page."

git remote add origin <former-frontend-repo>
git branch -M main
git push -u origin main
```

---

## Phase 3: Test Locally (Optional)

Before deploying to the VPS, test the auth service locally:

```bash
cd ~/Projects/munin-vps

# Build and start
docker compose build munin-auth
docker compose up munin-auth

# Test whitelist check (should return 401 — no session)
curl -s http://localhost:8090/auth/check -w "%{http_code}\n" -o /dev/null
# Expected: 401

# Test login page
curl -s http://localhost:8090/login | head -20
# Expected: HTML form

# Test with a non-whitelisted email
curl -s -X POST http://localhost:8090/login -d "email=nobody@example.com"
# Expected: error message about email not being authorized
```

---

## Phase 4: Deploy to VPS

### 4.1 — Stop old stack

```bash
ssh -i ~/.ssh/munin_admin varghele@<vps-host>

cd ~/munin
docker compose down

# Note: this stops Authentik. Users will lose access temporarily.
# Caddy data (TLS certs) persists in named volumes.
```

### 4.2 — Swap to new repo

```bash
mv ~/munin ~/munin-old
git clone <former-frontend-repo> ~/munin

# Create .env with your secrets
cat > ~/munin/.env << 'EOF'
AUTH_SECRET_KEY=$(openssl rand -hex 32)
SMTP_HOST=your-hosteurope-smtp-host
SMTP_PORT=587
SMTP_USERNAME=your-email@muninai.org
SMTP_PASSWORD=your-password
SMTP_SENDER=noreply@muninai.org
EOF

# Copy static pages not yet migrated
cp -r ~/munin-old/static/docs ~/munin/static/ 2>/dev/null || true
cp -r ~/munin-old/static/search ~/munin/static/ 2>/dev/null || true
cp -r ~/munin-old/static/research ~/munin/static/ 2>/dev/null || true
cp -r ~/munin-old/static/upload ~/munin/static/ 2>/dev/null || true
```

### 4.3 — Build and start

```bash
cd ~/munin
docker compose build
docker compose up -d
docker compose ps
```

### 4.4 — Test

```bash
# Auth service health
curl -s http://localhost:8090/health

# Caddy serving landing page
curl -sI https://muninai.org | head -5

# Auth redirect (should get 401 → redirect to login)
curl -sI https://chat.muninai.org | head -10

# Login flow (manual test in browser)
# 1. Go to https://chat.muninai.org
# 2. Should redirect to https://auth.muninai.org/login
# 3. Enter a whitelisted email
# 4. Check inbox for OTP code
# 5. Enter code
# 6. Should redirect back to chat.muninai.org with a session cookie

# Upload still works
curl -sI https://upload.muninai.org | head -5
```

### 4.5 — Verify tunnel

```bash
# Cluster tunnel should still be connecting
curl -s http://localhost:18080/health
```

---

## Phase 5: Clean Up

### 5.1 — Remove old Authentik data

Once you're confident the new auth works (wait at least a few days):

```bash
# Remove old Authentik Docker volumes
docker volume rm munin-old_pg_data munin-old_redis_data 2>/dev/null || true
# (actual volume names may differ — check with docker volume ls)

# Remove old directory
rm -rf ~/munin-old
```

### 5.2 — Remove `auth.muninai.org` DNS if desired

The `auth.muninai.org` subdomain is still used — it now serves the login page from munin-auth instead of Authentik UI. Keep the DNS record.

### 5.3 — Remove VPS files from HuginSLURM

On your local machine:

```bash
cd ~/PycharmProjects/HuginSLURM
rm -rf vps/
rm -f CURRENT-STATUS.md
# Update README to point to munin-vps
git add -A
git commit -m "Remove VPS files — moved to munin-vps repository"
git push
```

---

## Rollback Plan

If the new auth breaks and you need to get users back in quickly:

```bash
ssh -i ~/.ssh/munin_admin varghele@<vps-host>

cd ~/munin && docker compose down
mv ~/munin ~/munin-new
mv ~/munin-old ~/munin
cd ~/munin && docker compose up -d
```

Authentik's PostgreSQL data is in a named Docker volume — it survives directory moves. Users' sessions and accounts will still work.

---

## Post-Migration Checklist

- [ ] `munin-vps` repo on GitHub
- [ ] munin-auth service running and sending OTP emails via HostEurope
- [ ] Whitelist CSV populated with all existing user emails and names
- [ ] Session cookie working across subdomains (`.muninai.org`)
- [ ] Login flow working: email → OTP → 30-day session
- [ ] All subdomains responding (muninai.org, auth, chat, search, research, upload)
- [ ] File upload working with new `X-Munin-Email` header
- [ ] Cluster tunnel still connecting (port 18080)
- [ ] Old Authentik containers stopped
- [ ] `.env` on VPS with HostEurope SMTP credentials
- [ ] `vps/` and `authentik/` removed from HuginSLURM
- [ ] Old backup retained for 30 days
