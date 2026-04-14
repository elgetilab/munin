#!/bin/bash
# ==============================================================================
# MUNIN BACKEND - DEPLOYMENT SCRIPT
# ==============================================================================
# Syncs this repo into /opt/munin/, installs systemd units, rebuilds the
# retrieval container, and removes the legacy Open WebUI + status-page
# services. Intended to run directly on the cluster head (hugin).
#
# Usage:
#   sudo ./deploy.sh all            - everything (recommended first run)
#   sudo ./deploy.sh dirs           - create filesystem layout
#   sudo ./deploy.sh compose        - docker/docker-compose.yml
#   sudo ./deploy.sh personas       - persona JSON + logos
#   sudo ./deploy.sh agents         - config/agents.yml + munin.env.template
#   sudo ./deploy.sh vllm           - scripts/vllm/*.sh → /opt/cluster/scripts/llm/
#   sudo ./deploy.sh deepresearch   - scripts/deepresearch/* + systemd unit + MiroThinker model
#   sudo ./deploy.sh tunnel         - munin-tunnel.service (+ daemon-reload + restart)
#   sudo ./deploy.sh retrieval      - retrieval/ code, rebuild + restart container
#   sudo ./deploy.sh searxng        - searxng settings.yml + restart container
#   sudo ./deploy.sh cleanup        - remove Open WebUI + status-page containers and dirs
#   sudo ./deploy.sh verify         - smoke-test /api/status and /api/personas
#   sudo ./deploy.sh --dry-run <mode> - show what would change, do nothing
#
# NOTE: vLLM SLURM job changes only take effect on next submission. Use
#   vllm-service stop && vllm-service start (or schedule-vllm.sh) to cut over.
# ==============================================================================

set -e

DRY_RUN=0
if [ "$1" = "--dry-run" ]; then
    DRY_RUN=1
    shift
fi

MODE=${1:-}
if [ -z "$MODE" ]; then
    echo "Usage: sudo $0 [--dry-run] <mode>"
    echo "Modes: all dirs compose personas agents vllm deepresearch tunnel retrieval searxng cleanup verify"
    exit 1
fi

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

MUNIN_ROOT=/opt/munin
MUNIN_CONFIG=$MUNIN_ROOT/config
MUNIN_PERSONAS=$MUNIN_ROOT/personas
MUNIN_DATA=$MUNIN_ROOT/data
MUNIN_USER_DOCS=$MUNIN_DATA/user_docs
MUNIN_LOGS=$MUNIN_ROOT/logs
MUNIN_DEEPRESEARCH=$MUNIN_ROOT/deepresearch
MUNIN_DOCKER=$MUNIN_ROOT/docker
MUNIN_RETRIEVAL=$MUNIN_ROOT/services/retrieval
MUNIN_SEARXNG=$MUNIN_ROOT/services/searxng
MUNIN_SANDBOX=$MUNIN_ROOT/services/sandbox

CLUSTER_SCRIPTS=/opt/cluster/scripts/llm
SYSTEMD_DIR=/etc/systemd/system
HUGIN_ENV=/opt/hugin/config/cluster.env

MIROTHINKER_MODEL_ID="cyankiwi/MiroThinker-v1.5-30B-AWQ-4bit"
MIROTHINKER_MODEL_DIR=$MUNIN_ROOT/data/models/mirothinker-v1.5-30b
VLLM_VENV=/opt/munin/services/vllm/venv

echo "=============================================="
echo "MUNIN BACKEND - Deployment"
echo "=============================================="
echo "Repo:    $REPO_DIR"
echo "Mode:    $MODE"
[ "$DRY_RUN" = "1" ] && echo "Dry run: YES (no changes will be made)"
echo ""

# ------------------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------------------
run() {
    if [ "$DRY_RUN" = "1" ]; then
        echo "  [dry-run] $*"
    else
        eval "$@"
    fi
}

need_root() {
    # `verify` is read-only (curl only); dry-runs never mutate anything.
    if [ "$MODE" = "verify" ] || [ "$DRY_RUN" = "1" ]; then
        return 0
    fi
    if [ "$EUID" -ne 0 ]; then
        echo "[ERROR] This script must be run as root (use sudo)"
        exit 1
    fi
}

need_file() {
    if [ ! -e "$1" ]; then
        echo "[ERROR] Required file missing: $1"
        exit 1
    fi
}

# ------------------------------------------------------------------------------
# dirs: create everything that retrieval and deep research expect
# ------------------------------------------------------------------------------
deploy_dirs() {
    echo "[dirs] Ensuring filesystem layout..."
    run "mkdir -p $MUNIN_CONFIG $MUNIN_PERSONAS/logos"
    run "mkdir -p $MUNIN_DATA $MUNIN_USER_DOCS $MUNIN_LOGS"
    run "mkdir -p $MUNIN_DEEPRESEARCH/queue $MUNIN_DEEPRESEARCH/jobs"
    run "mkdir -p $MUNIN_DOCKER $MUNIN_RETRIEVAL $MUNIN_SANDBOX"
    run "mkdir -p $CLUSTER_SCRIPTS"

    # Deep research queue needs docker group write access
    if getent group docker >/dev/null 2>&1; then
        run "chown -R root:docker $MUNIN_DEEPRESEARCH"
        run "chmod 775 $MUNIN_DEEPRESEARCH/queue"
        run "chmod 755 $MUNIN_DEEPRESEARCH/jobs"
    fi

    # Empty SLURM queue file (only if missing)
    if [ ! -f $MUNIN_DEEPRESEARCH/slurm_queue.json ]; then
        run "echo '{\"total_jobs\":0,\"deepresearch_jobs\":0,\"queue\":[],\"updated_at\":null}' > $MUNIN_DEEPRESEARCH/slurm_queue.json"
        run "chmod 644 $MUNIN_DEEPRESEARCH/slurm_queue.json"
    fi

    # Docker compose auto-loads ./.env from the compose dir — symlink to the
    # shared cluster env so NEO4J_PASSWORD, SEMANTIC_SCHOLAR_API_KEY, etc.
    # flow through to retrieval without duplication.
    if [ -f "$HUGIN_ENV" ] && [ ! -e $MUNIN_DOCKER/.env ]; then
        run "ln -sf $HUGIN_ENV $MUNIN_DOCKER/.env"
    fi

    echo "[OK] dirs"
}

# ------------------------------------------------------------------------------
# compose: copy docker-compose.yml (no restart)
# ------------------------------------------------------------------------------
deploy_compose() {
    echo "[compose] Installing docker-compose.yml..."
    need_file "$REPO_DIR/docker/docker-compose.yml"
    run "install -m 0644 $REPO_DIR/docker/docker-compose.yml $MUNIN_DOCKER/docker-compose.yml"
    echo "[OK] compose — restart with: docker compose --profile rag up -d"
}

# ------------------------------------------------------------------------------
# personas: sync persona JSON + logos (mounted read-only into retrieval)
# ------------------------------------------------------------------------------
deploy_personas() {
    echo "[personas] Syncing persona definitions..."
    need_file "$REPO_DIR/personas"
    run "install -d -m 0755 $MUNIN_PERSONAS $MUNIN_PERSONAS/logos"
    run "install -m 0644 $REPO_DIR/personas/*.json $MUNIN_PERSONAS/"
    if compgen -G "$REPO_DIR/personas/logos/*" > /dev/null; then
        run "install -m 0644 $REPO_DIR/personas/logos/* $MUNIN_PERSONAS/logos/"
    fi
    echo "[OK] personas"
}

# ------------------------------------------------------------------------------
# agents: copy agents.yml + munin.env.template into the mounted config dir
# ------------------------------------------------------------------------------
deploy_agents() {
    echo "[agents] Installing agent registry + env template..."
    need_file "$REPO_DIR/config/agents.yml"
    run "install -d -m 0755 $MUNIN_CONFIG"
    run "install -m 0644 $REPO_DIR/config/agents.yml $MUNIN_CONFIG/agents.yml"

    if [ -f "$REPO_DIR/config/munin.env.template" ]; then
        run "install -m 0644 $REPO_DIR/config/munin.env.template $MUNIN_CONFIG/munin.env.template"
    fi

    # §4: the faq tool reads /app/config/faq.yml inside the retrieval
    # container (mounted from $MUNIN_CONFIG). Sync it alongside
    # agents.yml so `./deploy.sh agents` picks up admin-curated
    # how-to answers in one step.
    if [ -f "$REPO_DIR/config/faq.yml" ]; then
        run "install -m 0644 $REPO_DIR/config/faq.yml $MUNIN_CONFIG/faq.yml"
    fi

    # Note: we do NOT touch $MUNIN_CONFIG/munin.env if it already exists.
    # Docker compose sources /opt/hugin/config/cluster.env via the .env symlink.
    # munin.env.template is kept in-tree as documentation / override reference.

    echo "[OK] agents"
}

# ------------------------------------------------------------------------------
# vllm: copy SLURM job + cron wrapper into /opt/cluster/scripts/llm/
# ------------------------------------------------------------------------------
deploy_vllm() {
    echo "[vllm] Installing vLLM scripts..."
    need_file "$REPO_DIR/scripts/vllm/start-vllm-service.sh"
    need_file "$REPO_DIR/scripts/vllm/schedule-vllm.sh"
    run "install -d -m 0755 $CLUSTER_SCRIPTS"
    run "install -m 0755 $REPO_DIR/scripts/vllm/start-vllm-service.sh $CLUSTER_SCRIPTS/start-vllm-service.sh"
    run "install -m 0755 $REPO_DIR/scripts/vllm/schedule-vllm.sh $CLUSTER_SCRIPTS/schedule-vllm.sh"
    run "ln -sf $CLUSTER_SCRIPTS/schedule-vllm.sh /usr/local/bin/vllm-service"
    echo "[OK] vllm — changes take effect on next job submission"
    echo "     to cut over now: sudo vllm-service stop && sudo vllm-service start"
}

# ------------------------------------------------------------------------------
# deepresearch: scripts + systemd unit + MiroThinker model (guarded)
# ------------------------------------------------------------------------------
deploy_deepresearch() {
    echo "[deepresearch] Installing daemon + job script..."
    need_file "$REPO_DIR/scripts/deepresearch/deepresearch-daemon.py"
    need_file "$REPO_DIR/scripts/deepresearch/deepresearch-job.sh"
    need_file "$REPO_DIR/config/deepresearch-daemon.service"
    run "install -d -m 0755 $CLUSTER_SCRIPTS"
    run "install -m 0755 $REPO_DIR/scripts/deepresearch/deepresearch-daemon.py $CLUSTER_SCRIPTS/deepresearch-daemon.py"
    run "install -m 0755 $REPO_DIR/scripts/deepresearch/deepresearch-job.sh $CLUSTER_SCRIPTS/deepresearch-job.sh"
    run "install -m 0644 $REPO_DIR/config/deepresearch-daemon.service $SYSTEMD_DIR/deepresearch-daemon.service"
    run "systemctl daemon-reload"

    echo ""
    echo "[deepresearch] Checking MiroThinker model..."
    if [ -d "$MIROTHINKER_MODEL_DIR" ]; then
        echo "[SKIP] MiroThinker already present at $MIROTHINKER_MODEL_DIR"
    else
        echo "MiroThinker not found at $MIROTHINKER_MODEL_DIR"
        echo "Model: $MIROTHINKER_MODEL_ID (~17 GB, 10-30 min download)"
        if [ -f "$VLLM_VENV/bin/activate" ]; then
            if [ "$DRY_RUN" = "0" ]; then
                # shellcheck disable=SC1091
                source "$VLLM_VENV/bin/activate"
                huggingface-cli download "$MIROTHINKER_MODEL_ID" \
                    --local-dir "$MIROTHINKER_MODEL_DIR" \
                    --local-dir-use-symlinks False
                deactivate
                echo "[OK] MiroThinker downloaded"
            else
                echo "  [dry-run] would download $MIROTHINKER_MODEL_ID → $MIROTHINKER_MODEL_DIR"
            fi
        else
            echo "[WARN] vLLM venv not found at $VLLM_VENV"
            echo "       Download manually after Phase 2 vLLM setup:"
            echo "         source $VLLM_VENV/bin/activate"
            echo "         huggingface-cli download $MIROTHINKER_MODEL_ID \\"
            echo "             --local-dir $MIROTHINKER_MODEL_DIR"
        fi
    fi

    echo "[OK] deepresearch — enable with: systemctl enable --now deepresearch-daemon"
}

# ------------------------------------------------------------------------------
# tunnel: install cleaned munin-tunnel.service and restart it
# ------------------------------------------------------------------------------
deploy_tunnel() {
    echo "[tunnel] Installing munin-tunnel.service..."
    need_file "$REPO_DIR/config/munin-tunnel.service"
    run "install -m 0644 $REPO_DIR/config/munin-tunnel.service $SYSTEMD_DIR/munin-tunnel.service"
    run "systemctl daemon-reload"
    if systemctl list-unit-files munin-tunnel.service >/dev/null 2>&1; then
        run "systemctl restart munin-tunnel.service"
        echo "[OK] tunnel — restarted"
    else
        run "systemctl enable --now munin-tunnel.service"
        echo "[OK] tunnel — enabled + started"
    fi
}

# ------------------------------------------------------------------------------
# searxng: install settings.yml and restart the container
# ------------------------------------------------------------------------------
deploy_searxng() {
    echo "[searxng] Installing settings.yml..."
    need_file "$REPO_DIR/docker/searxng/settings.yml"
    run "install -d -m 0755 $MUNIN_SEARXNG"
    run "install -m 0644 $REPO_DIR/docker/searxng/settings.yml $MUNIN_SEARXNG/settings.yml"

    if [ "$DRY_RUN" = "0" ]; then
        if docker info >/dev/null 2>&1 && docker ps --format '{{.Names}}' | grep -q '^munin-searxng$'; then
            run "cd $MUNIN_DOCKER && docker compose --profile rag restart searxng"
            echo "[OK] searxng — config reloaded"
        else
            echo "[WARN] munin-searxng not running; bring it up with: docker compose --profile rag up -d searxng"
        fi
    else
        echo "  [dry-run] would docker compose restart searxng"
    fi
}


# ------------------------------------------------------------------------------
# sandbox: sync sandbox/, rebuild image, restart container
# ------------------------------------------------------------------------------
deploy_sandbox() {
    echo "[sandbox] Syncing code to $MUNIN_SANDBOX..."
    need_file "$REPO_DIR/sandbox"
    run "install -d -m 0755 $MUNIN_SANDBOX"

    run "rsync -a --delete \
        --exclude='__pycache__' \
        --exclude='*.pyc' \
        --exclude='.pytest_cache' \
        $REPO_DIR/sandbox/ $MUNIN_SANDBOX/"

    # Make sure the compose file is current too — sandbox is a new service
    # and the network/security_opt blocks must be in place before `up -d`.
    run "install -m 0644 $REPO_DIR/docker/docker-compose.yml $MUNIN_DOCKER/docker-compose.yml"

    echo "[sandbox] Rebuilding container..."
    run "cd $MUNIN_DOCKER && docker compose --profile rag build sandbox"
    echo "[sandbox] Restarting container..."
    run "cd $MUNIN_DOCKER && docker compose --profile rag up -d sandbox"

    if [ "$DRY_RUN" = "0" ]; then
        sleep 3
        if docker ps --format '{{.Names}}' | grep -q '^munin-sandbox$'; then
            echo "[OK] sandbox container is running"
        else
            echo "[WARN] sandbox container is not running — check logs:"
            echo "       docker logs --tail 200 munin-sandbox"
            return 1
        fi

        # Probe /healthz from the sandbox container's own loopback. We used
        # to probe from inside retrieval, but if retrieval was started with
        # an older compose file it isn't on sandbox-net yet and the DNS
        # lookup for `sandbox` fails. Loopback always works.
        local probe
        probe=$(docker exec munin-sandbox python -c \
            "import httpx;r=httpx.get('http://localhost:8090/healthz',timeout=5);print(r.status_code);print(r.text)" \
            2>&1 || true)
        if echo "$probe" | head -n1 | grep -q '^200$'; then
            echo "  [OK] /healthz: $(echo "$probe" | tail -n1)"
        else
            echo "[WARN] /healthz probe failed:"
            echo "$probe"
            return 1
        fi

        # If retrieval is already running, reconcile it too: the compose
        # file we just installed adds sandbox-net to retrieval's networks
        # list, but `up -d sandbox` alone does NOT touch the retrieval
        # container, so retrieval still can't reach sandbox by hostname
        # until it is recreated. `up -d retrieval` is a no-op if the
        # config hasn't changed and a clean recreate if it has.
        if docker ps --format '{{.Names}}' | grep -q '^munin-retrieval$'; then
            echo "[sandbox] Reconciling retrieval container so it joins sandbox-net..."
            run "cd $MUNIN_DOCKER && docker compose --profile rag up -d retrieval"
        else
            echo "[sandbox] Note: retrieval is not running. After you start it,"
            echo "          it will pick up the sandbox-net automatically."
        fi
    fi
}


# ------------------------------------------------------------------------------
# retrieval: sync code, rebuild container, restart
# ------------------------------------------------------------------------------
deploy_retrieval() {
    echo "[retrieval] Syncing code to $MUNIN_RETRIEVAL..."
    need_file "$REPO_DIR/retrieval"
    run "install -d -m 0755 $MUNIN_RETRIEVAL"

    # Remove stale mirror left from earlier deploys (see audit)
    if [ -d $MUNIN_DOCKER/retrieval ]; then
        echo "  removing stale $MUNIN_DOCKER/retrieval"
        run "rm -rf $MUNIN_DOCKER/retrieval"
    fi

    # rsync the retrieval tree. --delete keeps the build context in lockstep
    # with git. Excludes caches so we don't ship __pycache__ into the image.
    run "rsync -a --delete \
        --exclude='__pycache__' \
        --exclude='*.pyc' \
        --exclude='.pytest_cache' \
        $REPO_DIR/retrieval/ $MUNIN_RETRIEVAL/"

    # Make sure the compose file is current too (mounts, env vars).
    run "install -m 0644 $REPO_DIR/docker/docker-compose.yml $MUNIN_DOCKER/docker-compose.yml"

    echo "[retrieval] Rebuilding container..."
    run "cd $MUNIN_DOCKER && docker compose --profile rag build retrieval"
    echo "[retrieval] Restarting container..."
    run "cd $MUNIN_DOCKER && docker compose --profile rag up -d retrieval"

    if [ "$DRY_RUN" = "0" ]; then
        sleep 3
        if docker ps --format '{{.Names}}' | grep -q '^munin-retrieval$'; then
            echo "[OK] retrieval container is running"
        else
            echo "[WARN] retrieval container is not running — check logs:"
            echo "       docker logs --tail 200 munin-retrieval"
            return 1
        fi
    fi

    # Always run smoke tests at the end of a retrieval deploy (dry-run aware).
    deploy_verify
}

# ------------------------------------------------------------------------------
# cleanup: retire Open WebUI and the status page
# ------------------------------------------------------------------------------
deploy_cleanup() {
    echo "[cleanup] Removing legacy Open WebUI, status-page, cloudflared..."

    # Skip docker-touching steps entirely if the socket isn't reachable
    # (e.g. dry-runs as a non-root user). The real run is always root.
    if docker info >/dev/null 2>&1; then
        for name in munin-openwebui munin-status-page munin-cloudflared; do
            if docker ps -a --format '{{.Names}}' | grep -q "^${name}\$"; then
                run "docker stop $name 2>/dev/null || true"
                run "docker rm $name 2>/dev/null || true"
            fi
        done
    else
        echo "  [skip] docker socket not reachable — container stop/rm skipped"
    fi

    for dir in $MUNIN_ROOT/services/openwebui $MUNIN_ROOT/services/status-page; do
        if [ -d "$dir" ]; then
            echo "  removing $dir"
            run "rm -rf $dir"
        fi
    done

    # update-openwebui-models.sh lives under /opt/cluster/scripts/llm/
    if [ -f $CLUSTER_SCRIPTS/update-openwebui-models.sh ]; then
        run "rm -f $CLUSTER_SCRIPTS/update-openwebui-models.sh"
    fi

    echo "[OK] cleanup"
}

# ------------------------------------------------------------------------------
# verify: smoke-test the key frontend endpoints after a deploy
# ------------------------------------------------------------------------------
RETRIEVAL_BASE=${RETRIEVAL_BASE:-http://127.0.0.1:8080}
VERIFY_EMAIL=${VERIFY_EMAIL:-deploy-check@munin.local}

deploy_verify() {
    echo "[verify] Smoke-testing retrieval endpoints..."

    if [ "$DRY_RUN" = "1" ]; then
        echo "  [dry-run] would poll $RETRIEVAL_BASE/health until 200, then curl /api/status + /api/personas"
        return 0
    fi

    # 1. Wait for /health (up to ~30s) so we don't race container startup.
    local attempt=0
    local ready=0
    while [ $attempt -lt 30 ]; do
        if curl -fsS -o /dev/null -m 2 "$RETRIEVAL_BASE/health"; then
            ready=1
            break
        fi
        attempt=$((attempt + 1))
        sleep 1
    done
    if [ $ready -eq 0 ]; then
        echo "[FAIL] /health did not respond within 30s"
        echo "       docker logs --tail 200 munin-retrieval"
        return 1
    fi
    echo "  [OK] /health responding (after ${attempt}s)"

    # 2. /api/status — must return HTTP 200 and contain a 'vllm' key.
    local status_body
    status_body=$(curl -fsS -m 5 "$RETRIEVAL_BASE/api/status" 2>/dev/null || true)
    if [ -z "$status_body" ]; then
        echo "[FAIL] /api/status returned no body"
        return 1
    fi
    if ! python3 -c "import json,sys; d=json.loads(sys.argv[1]); assert 'vllm' in d and 'services' in d" "$status_body" 2>/dev/null; then
        echo "[FAIL] /api/status response missing vllm/services keys"
        echo "       body: $status_body"
        return 1
    fi
    local vllm_state
    vllm_state=$(python3 -c "import json,sys; print(json.loads(sys.argv[1])['vllm'].get('status','?'))" "$status_body")
    echo "  [OK] /api/status — vllm=$vllm_state"

    # 3. /api/personas — must return 200 with a non-empty personas array.
    local personas_body
    personas_body=$(curl -fsS -m 5 -H "X-Munin-Email: $VERIFY_EMAIL" "$RETRIEVAL_BASE/api/personas" 2>/dev/null || true)
    if [ -z "$personas_body" ]; then
        echo "[FAIL] /api/personas returned no body"
        return 1
    fi
    local persona_count
    persona_count=$(python3 -c "import json,sys; d=json.loads(sys.argv[1]); print(len(d.get('personas',[])))" "$personas_body" 2>/dev/null || echo 0)
    if [ "$persona_count" = "0" ]; then
        echo "[FAIL] /api/personas returned empty list — check /opt/munin/personas mount"
        return 1
    fi
    local persona_ids
    persona_ids=$(python3 -c "import json,sys; print(','.join(p['id'] for p in json.loads(sys.argv[1])['personas']))" "$personas_body")
    echo "  [OK] /api/personas — $persona_count loaded ($persona_ids)"

    echo "[OK] verify — all smoke tests passed"
}

# ------------------------------------------------------------------------------
# Main
# ------------------------------------------------------------------------------
need_root

case "$MODE" in
    dirs)         deploy_dirs ;;
    compose)      deploy_compose ;;
    personas)     deploy_personas ;;
    agents)       deploy_agents ;;
    vllm)         deploy_vllm ;;
    deepresearch) deploy_deepresearch ;;
    tunnel)       deploy_tunnel ;;
    searxng)      deploy_searxng ;;
    sandbox)      deploy_sandbox ;;
    retrieval)    deploy_retrieval ;;
    cleanup)      deploy_cleanup ;;
    verify)       deploy_verify ;;
    all)
        deploy_dirs
        deploy_compose
        deploy_personas
        deploy_agents
        deploy_vllm
        deploy_deepresearch
        deploy_tunnel
        deploy_searxng
        deploy_cleanup
        deploy_sandbox       # must be up before retrieval starts since
                             # retrieval depends_on sandbox in the compose
        deploy_retrieval     # ends with deploy_verify
        ;;
    *)
        echo "[ERROR] Unknown mode: $MODE"
        echo "Modes: all dirs compose personas agents vllm deepresearch tunnel retrieval sandbox searxng cleanup verify"
        exit 1
        ;;
esac

echo ""
echo "=============================================="
[ "$DRY_RUN" = "1" ] && echo "Dry run complete — no changes were made." || echo "Done."
echo "=============================================="
