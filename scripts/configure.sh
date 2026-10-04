#!/usr/bin/env bash
# ==============================================================================
# Munin: write a .env for one of the three install modes
# ==============================================================================
#   all       both halves on one machine (a laptop with --domain localhost, or
#             one server with a real domain)
#   backend   retrieval, databases, pipeline, sandbox (the GPU / cluster side)
#   frontend  Caddy, auth, gateway, uploads, web UI (the small public server)
#
# It generates every secret, puts your address in the login whitelist, copies
# the other seed files from their .example, and writes absolute paths so
# `docker compose` works from the repo root. For a split install, run it on the
# backend first: it writes munin-peer.env, the values both sides must share.
# Copy that file to the frontend machine and pass it with --peer-env.
#
#   scripts/configure.sh --mode all --domain localhost --admin-email me@lab.org \
#       --llm-url http://host.docker.internal:11434 --llm-model qwen3:32b
#
#   scripts/configure.sh --mode backend --domain lab.example.edu \
#       --admin-email me@lab.example.edu --llm-url http://gpu01:8000 \
#       --vps-host vps.lab.example.edu          # also sets up the tunnel
#   scripts/configure.sh --mode frontend --domain lab.example.edu \
#       --admin-email me@lab.example.edu --peer-env munin-peer.env
#
# Anything not given on the command line is asked for (or, with
# --non-interactive, is an error). An existing .env is never overwritten
# without --force, and then it is backed up first.
# ==============================================================================

set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
ENV_FILE="$ROOT/.env"
PEER_FILE="$ROOT/munin-peer.env"

MODE="" DOMAIN="" ADMIN_EMAIL="" LLM_URL="" LLM_MODEL="" CLUSTER_NAME=""
CONTACT_EMAIL="" PEER_ENV="" VPS_HOST="" BACKEND_URL="" FORCE=0 INTERACTIVE=1
UPLOADS_DIR=""
SMTP_HOST="" SMTP_PORT="587" SMTP_USERNAME="" SMTP_PASSWORD="" SMTP_SENDER=""

usage() { sed -n '2,29p' "$0" | sed 's/^# \{0,1\}//'; exit "${1:-0}"; }
die() { echo "configure: $*" >&2; exit 1; }

while [ $# -gt 0 ]; do
    case "$1" in
        --mode) MODE=$2; shift 2 ;;
        --domain) DOMAIN=$2; shift 2 ;;
        --admin-email) ADMIN_EMAIL=$2; shift 2 ;;
        --llm-url) LLM_URL=$2; shift 2 ;;
        --llm-model) LLM_MODEL=$2; shift 2 ;;
        --cluster-name) CLUSTER_NAME=$2; shift 2 ;;
        --contact-email) CONTACT_EMAIL=$2; shift 2 ;;
        --peer-env) PEER_ENV=$2; shift 2 ;;
        --vps-host) VPS_HOST=$2; shift 2 ;;
        --backend-url) BACKEND_URL=$2; shift 2 ;;
        --uploads-dir) UPLOADS_DIR=$2; shift 2 ;;
        --smtp-host) SMTP_HOST=$2; shift 2 ;;
        --smtp-port) SMTP_PORT=$2; shift 2 ;;
        --smtp-user) SMTP_USERNAME=$2; shift 2 ;;
        --smtp-password) SMTP_PASSWORD=$2; shift 2 ;;
        --smtp-sender) SMTP_SENDER=$2; shift 2 ;;
        --force) FORCE=1; shift ;;
        --non-interactive) INTERACTIVE=0; shift ;;
        -h|--help) usage 0 ;;
        *) echo "configure: unknown option $1" >&2; usage 1 ;;
    esac
done
[ -t 0 ] || INTERACTIVE=0

ask() {  # ask VAR "question" [default]
    local var=$1 q=$2 def=${3:-} ans
    [ -n "${!var}" ] && return 0
    if [ "$INTERACTIVE" = 1 ]; then
        read -r -p "$q${def:+ [$def]}: " ans
        printf -v "$var" '%s' "${ans:-$def}"
    else
        printf -v "$var" '%s' "$def"
    fi
    local flag=${var,,}; flag=${flag//_/-}
    case "$flag" in smtp-username) flag=smtp-user ;; esac
    [ -n "${!var}" ] || die "missing --$flag (no default; see --help)"
}

# Quote a value for a compose .env file: single quotes are literal (no
# interpolation), so use them unless the value has a single quote or a
# backslash (compose still reads \' as an escape there); otherwise double
# quotes, escaping \ and " and doubling $ (compose's escape).
env_quote() {
    local v=$1
    if [[ $v != *"'"* && $v != *\\* ]]; then printf "'%s'" "$v"; return; fi
    v=${v//\\/\\\\}; v=${v//\"/\\\"}; v=${v//\$/\$\$}
    printf '"%s"' "$v"
}

secret() {
    if command -v openssl >/dev/null 2>&1; then openssl rand -hex 32
    else python3 -c 'import secrets; print(secrets.token_hex(32))'; fi
}

# ------------------------------------------------------------------------------
# What to build
# ------------------------------------------------------------------------------
ask MODE "Install mode (all / backend / frontend)" "all"
case "$MODE" in all|backend|frontend) ;; *) die "--mode must be all, backend or frontend" ;; esac
ask DOMAIN "Base domain (localhost for a laptop demo)" "$([ "$MODE" = all ] && echo localhost)"
ask ADMIN_EMAIL "Your email (first admin; the only address allowed to log in at first)"
case "$ADMIN_EMAIL" in *@*.*) ;; *) die "--admin-email does not look like an address" ;; esac
LOCAL=0; [ "$DOMAIN" = localhost ] && LOCAL=1
[ "$LOCAL" = 1 ] && [ "$MODE" != all ] && die "--domain localhost only makes sense with --mode all"

if [ "$MODE" != frontend ]; then
    ask LLM_URL "OpenAI-compatible model endpoint" "http://host.docker.internal:8000"
    ask LLM_MODEL "Model name the endpoint serves (as in its /v1/models)"
fi
if [ "$MODE" = frontend ]; then
    ask BACKEND_URL "Backend address (default: the tunnel's end on this host)" "http://127.0.0.1:18080"
fi
if [ "$MODE" != backend ] && [ "$LOCAL" = 0 ]; then
    ask SMTP_HOST "SMTP host for login codes"
    ask SMTP_USERNAME "SMTP username" "-"
    [ "$SMTP_USERNAME" = "-" ] && SMTP_USERNAME=""
    if [ -z "$SMTP_PASSWORD" ] && [ "$INTERACTIVE" = 1 ]; then
        read -r -s -p "SMTP password (hidden): " SMTP_PASSWORD; echo
    fi
    [ -n "$SMTP_SENDER" ] || SMTP_SENDER="noreply@$DOMAIN"
fi

if [ -f "$ENV_FILE" ]; then
    [ "$FORCE" = 1 ] || die "$ENV_FILE exists; pass --force to replace it (it is backed up first)"
    cp -p "$ENV_FILE" "$ENV_FILE.bak-$(date +%Y%m%d-%H%M%S)"
fi

# ------------------------------------------------------------------------------
# Secrets. The four the two halves share come from --peer-env when given, so
# a split install ends up with matching values on both machines.
# ------------------------------------------------------------------------------
SHARED_KEYS="ADMIN_INGEST_TOKEN KB_GATE_TOKEN CONTRIBUTORS_SYNC_TOKEN MUNIN_GATEWAY_TOKEN"
declare -A S
for k in $SHARED_KEYS; do S[$k]=$(secret); done
if [ -n "$PEER_ENV" ]; then
    [ -r "$PEER_ENV" ] || die "cannot read $PEER_ENV"
    for k in $SHARED_KEYS; do
        v=$(grep -E "^$k=" "$PEER_ENV" | tail -n1 | cut -d= -f2-)
        [ -n "$v" ] || die "$PEER_ENV has no $k"
        S[$k]=$v
    done
    peer_domain=$(grep -E '^MUNIN_DOMAIN=' "$PEER_ENV" | tail -n1 | cut -d= -f2-)
    [ -z "$peer_domain" ] || [ "$peer_domain" = "$DOMAIN" ] \
        || die "$PEER_ENV is for $peer_domain, not $DOMAIN"
elif [ "$MODE" = frontend ]; then
    echo "configure: no --peer-env: generating fresh shared tokens. Copy munin-peer.env"
    echo "           to the backend and give it the same values, or nothing will match." >&2
fi

# ------------------------------------------------------------------------------
# Seed files: copy each .example once, then put the admin address in.
# ------------------------------------------------------------------------------
seed() {  # seed <path> ; copies <path>.example to <path> if missing
    [ -f "$ROOT/$1" ] && { echo "  kept    $1 (already there)"; return 1; }
    cp "$ROOT/$1.example" "$ROOT/$1"; echo "  created $1"; return 0
}
echo "Seed files:"
if [ "$MODE" != backend ]; then
    if seed frontend/auth/whitelist.csv; then
        sed -i "s/^admin@example\.org,/$ADMIN_EMAIL,/; /^ada@example\.org,/d" \
            "$ROOT/frontend/auth/whitelist.csv"
    fi
    if seed frontend/config/quotas.yml; then
        sed -i "s/^  admin@example\.org:/  $ADMIN_EMAIL:/" "$ROOT/frontend/config/quotas.yml"
    fi
fi
seed shared/config/contributors.yml || true
# The chat UI is built into this directory (webui profile). Create it as you,
# or Docker creates it as root when it bind-mounts it.
[ "$MODE" = backend ] || mkdir -p "$ROOT/frontend/static/chat"
# Uploads: tusd runs as uid 1000 and writes staging/. A directory Docker
# creates for it is root's, and every upload then fails.
UPLOADS_DIR=${UPLOADS_DIR:-$ROOT/.runtime/uploads}
if [ "$MODE" != backend ]; then
    mkdir -p "$UPLOADS_DIR/staging" "$UPLOADS_DIR/complete" 2>/dev/null \
        || echo "  could not create $UPLOADS_DIR (run: sudo mkdir -p $UPLOADS_DIR/{staging,complete})"
    if [ "$(stat -c %u "$UPLOADS_DIR/staging" 2>/dev/null)" != 1000 ]; then
        echo "  NOTE: uploads need uid 1000 (tusd) to own $UPLOADS_DIR:"
        echo "        sudo chown -R 1000:1000 $UPLOADS_DIR"
    fi
fi

# ------------------------------------------------------------------------------
# Compose files, profiles and paths for the mode. Paths are absolute, so it
# does not matter which compose file comes first.
# ------------------------------------------------------------------------------
B="$ROOT/backend" F="$ROOT/frontend" RT="$ROOT/.runtime"
case "$MODE" in
    all)      FILES="backend/docker/docker-compose.yml:frontend/docker-compose.yml"
              PROFILES="rag,pipeline,monitoring,webui,seed" ;;
    backend)  FILES="backend/docker/docker-compose.yml"
              PROFILES="rag,pipeline,monitoring" ;;
    frontend) FILES="frontend/docker-compose.yml"
              PROFILES="webui" ;;
esac
TUNNEL_KEY="$RT/config/tunnel/id_ed25519"
if [ "$MODE" = backend ] && [ -n "$VPS_HOST" ]; then
    PROFILES="$PROFILES,tunnel"
    mkdir -p "$(dirname "$TUNNEL_KEY")"
    [ -f "$TUNNEL_KEY" ] || ssh-keygen -q -t ed25519 -N '' -C "munin-tunnel@$DOMAIN" -f "$TUNNEL_KEY"
    chmod 600 "$TUNNEL_KEY"
fi

umask 077
{
cat <<EOF
# Written by scripts/configure.sh on $(date -u +%Y-%m-%dT%H:%MZ) for mode: $MODE
# Every knob is documented in .env.example; anything not set here takes the
# default described there. Re-run configure.sh with --force to start over.

COMPOSE_FILE=$FILES
COMPOSE_PROFILES=$PROFILES
MUNIN_PREFIX=munin

MUNIN_DOMAIN=$DOMAIN
EOF

if [ "$MODE" != frontend ]; then
cat <<EOF

# ---- backend -----------------------------------------------------------------
MUNIN_ROOT=$RT
MUNIN_RETRIEVAL_SRC=$B/retrieval
MUNIN_SANDBOX_SRC=$B/sandbox
MUNIN_PIPELINE_SRC=$B/scripts/pipeline
MUNIN_SCRIPTS_SRC=$B/scripts
MUNIN_KNOWLEDGE_SRC=$B/scripts/knowledge
MUNIN_PERSONAS_SRC=$ROOT/shared/personas
MUNIN_AGENT_CONFIG=$B/config
MUNIN_DOCKER_CONFIG=$B/docker
MUNIN_SEARXNG_CONFIG=$RT/searxng
MUNIN_CLUSTER_NAME="${CLUSTER_NAME}"
MUNIN_CONTACT_EMAIL=${CONTACT_EMAIL}
LLM_BASE_URL=$LLM_URL
LLM_MODEL_NAME=$LLM_MODEL
NEO4J_PASSWORD=$(secret)
SEARXNG_SECRET=$(secret)
# A single host has smaller RAM than a cluster node.
GROBID_JAVA_OPTS="-Xmx4g -Xms512m"
GROBID_MEM_LIMIT=6G
INGEST_CONCURRENCY=2
# The sandbox asks Docker for this many CPUs; more than the host has and
# Docker refuses to create it.
SANDBOX_CPUS=$(n=$(nproc); [ "$n" -gt 8 ] && n=8; echo "$n")
EOF
[ "$LOCAL" = 1 ] && cat <<EOF
# One host: the paper links point at the local search UI, and the auth
# callbacks are off (the auth service is not reachable from the container).
MUNIN_PUBLIC_URL=http://localhost:8081
MUNIN_SITE_URL=http://localhost:8083
CONTRIBUTORS_SYNC_URL=
AUTH_CHECK_ROLE_URL=
EOF
[ -n "$VPS_HOST" ] && cat <<EOF
# Reverse tunnel to the frontend (profile: tunnel).
MUNIN_VPS_HOST=$VPS_HOST
MUNIN_TUNNEL_KEY=$TUNNEL_KEY
EOF
fi

if [ "$MODE" != backend ]; then
cat <<EOF

# ---- frontend ----------------------------------------------------------------
MUNIN_FRONTEND_SRC=$F
MUNIN_SHARED_SRC=$ROOT/shared
MUNIN_UPLOADS_DIR=$UPLOADS_DIR
AUTH_SECRET_KEY=$(secret)
ADMIN_EMAILS=$ADMIN_EMAIL
EOF
if [ "$LOCAL" = 1 ]; then
cat <<EOF
MUNIN_CADDYFILE=Caddyfile.local
BACKEND_URL=http://127.0.0.1:8080
COOKIE_DOMAIN=
# No mail server on a laptop: the login code is written to the auth log
# (docker compose logs munin-auth | grep "login code"). Never on a real server.
AUTH_DEV_ECHO_OTP=1
VITE_AUTH_BASE=http://localhost
VITE_SITE_HOME=http://localhost:8083
VITE_CHAT_URL=http://localhost
VITE_SEARCH_URL=http://localhost:8081
VITE_DOCS_URL=http://localhost:8082
EOF
else
cat <<EOF
BACKEND_URL=${BACKEND_URL:-http://127.0.0.1:8080}
SMTP_HOST=$SMTP_HOST
SMTP_PORT=$SMTP_PORT
SMTP_USERNAME=$SMTP_USERNAME
SMTP_PASSWORD=$(env_quote "$SMTP_PASSWORD")
SMTP_SENDER=$SMTP_SENDER
EOF
fi
fi

cat <<EOF

# ---- shared between the halves (must match on both machines) ----------------
EOF
for k in $SHARED_KEYS; do echo "$k=${S[$k]}"; done
} > "$ENV_FILE"
echo "Wrote $ENV_FILE (mode 600)"

if [ "$MODE" = backend ] || { [ "$MODE" = frontend ] && [ -z "$PEER_ENV" ]; }; then
    { echo "# Shared values for the other half of a split Munin install."
      echo "# Copy to the other machine and run configure.sh there with --peer-env."
      echo "MUNIN_DOMAIN=$DOMAIN"
      for k in $SHARED_KEYS; do echo "$k=${S[$k]}"; done; } > "$PEER_FILE"
    chmod 600 "$PEER_FILE"
    echo "Wrote $PEER_FILE: copy it to the other machine (it holds secrets)."
fi

# ------------------------------------------------------------------------------
# What next
# ------------------------------------------------------------------------------
echo
echo "Next:"
case "$MODE:$LOCAL" in
    all:1)
        echo "  docker compose up -d --build"
        echo "  open http://localhost and log in as $ADMIN_EMAIL; the code is in"
        echo "  docker compose logs munin-auth | grep 'login code'" ;;
    all:0|frontend:0)
        echo "  DNS: A records for $DOMAIN and auth. chat. search. docs. research."
        echo "       upload. api.$DOMAIN pointing at this machine (Caddy gets the certificates)"
        [ "$MODE" = frontend ] && echo "  the backend must be reachable at $BACKEND_URL over a private path (tunnel or VPN)"
        echo "  docker compose up -d --build"
        echo "  open https://chat.$DOMAIN and log in as $ADMIN_EMAIL" ;;
    backend:0)
        echo "  docker compose up -d --build"
        if [ -n "$VPS_HOST" ]; then
            echo "  on the VPS, append this line to the tunnel user's ~/.ssh/authorized_keys:"
            echo "    restrict,port-forwarding,permitlisten=\"127.0.0.1:18080\",command=\"/bin/false\" $(cat "$TUNNEL_KEY.pub")"
        else
            echo "  the frontend reaches retrieval on 127.0.0.1:8080 of this machine: give it a"
            echo "  private path (re-run with --vps-host for the reverse SSH tunnel)"
        fi ;;
esac
