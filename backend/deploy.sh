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
#   sudo ./deploy.sh maintenance    - maintenance-mode toggle → munin-maintenance
#   sudo ./deploy.sh deepresearch   - scripts/deepresearch/* + systemd unit + MiroThinker model
#   sudo ./deploy.sh tunnel         - munin-tunnel.service (+ daemon-reload + restart)
#   sudo ./deploy.sh knowledge      - §15 embedding-map script + nightly timer
#   sudo ./deploy.sh pipeline       - paper_pipeline.py → /opt/cluster/scripts/pipeline/ (§28)
#   sudo ./deploy.sh retrieval      - retrieval/ code, rebuild + restart container
#   sudo ./deploy.sh searxng        - searxng settings.yml + restart container
#   sudo ./deploy.sh monitoring     - prometheus + grafana on the metrics endpoint
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
    echo "Modes: all dirs compose personas agents vllm maintenance deepresearch tunnel retrieval searxng verify"
    exit 1
fi

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SHARED_DIR="$(cd "$REPO_DIR/../shared" && pwd)"

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

# P1 #8 — the retrieval container counts context tokens with the real
# Qwen3 tokenizer. Rather than mount the 19 GB vLLM model dir, deploy
# copies just the tokenizer files into a small dedicated dir that
# docker-compose mounts read-only. Keep VLLM_MODEL_DIR in sync with
# MODEL_PATH in scripts/vllm/start-vllm-service.sh.
VLLM_MODEL_DIR=$MUNIN_ROOT/data/models/qwen3.6-35b-a3b-awq-4bit
QWEN_TOKENIZER_DIR=$MUNIN_ROOT/data/models/qwen-tokenizer

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
    run "mkdir -p $MUNIN_DATA $MUNIN_DATA/reported $MUNIN_USER_DOCS $MUNIN_LOGS"
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
# compose: copy docker-compose.yml + grobid.yaml (no restart)
# ------------------------------------------------------------------------------
deploy_compose() {
    echo "[compose] Installing docker-compose.yml..."
    need_file "$REPO_DIR/docker/docker-compose.yml"
    run "install -d -m 0755 $MUNIN_DOCKER"
    run "install -m 0644 $REPO_DIR/docker/docker-compose.yml $MUNIN_DOCKER/docker-compose.yml"

    # GROBID config override — sets consolidation.crossref.mailto so
    # the internal Crossref client uses the polite pool.
    need_file "$REPO_DIR/docker/grobid/grobid.yaml"
    run "install -d -m 0755 $MUNIN_DOCKER/grobid"
    run "install -m 0644 $REPO_DIR/docker/grobid/grobid.yaml $MUNIN_DOCKER/grobid/grobid.yaml"

    # Symlink cluster.env into the compose dir as .env so
    # `docker compose ...` from $MUNIN_DOCKER picks up all the
    # ${VAR} interpolations the compose file expects. Without this
    # every `${VAR:-default}` evaluates to its default — recently
    # caused CONTRIBUTORS_SYNC_TOKEN to be empty on the cluster
    # (P1 #11 deploy 2026-05-29). Idempotent: -f forces replacement
    # only if the symlink target changed or it isn't a symlink yet.
    if [ -f "$HUGIN_ENV" ]; then
        run "ln -snf $HUGIN_ENV $MUNIN_DOCKER/.env"
    else
        echo "  [warn] $HUGIN_ENV not found; skipping .env symlink — compose will use defaults"
    fi

    echo "[OK] compose — restart with: docker compose --profile rag up -d --force-recreate grobid"
}

# ------------------------------------------------------------------------------
# personas: sync persona JSON + logos (mounted read-only into retrieval)
# ------------------------------------------------------------------------------
deploy_personas() {
    echo "[personas] Syncing persona definitions..."
    need_file "$SHARED_DIR/personas"
    run "install -d -m 0755 $MUNIN_PERSONAS $MUNIN_PERSONAS/logos"
    run "install -m 0644 $SHARED_DIR/personas/*.json $MUNIN_PERSONAS/"
    if compgen -G "$SHARED_DIR/personas/logos/*" > /dev/null; then
        run "install -m 0644 $SHARED_DIR/personas/logos/* $MUNIN_PERSONAS/logos/"
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

    # §28 / P1 #11: contributor allowlist. Bootstrap copy goes into
    # /opt/munin/data/contributors.yml (= /data inside the retrieval
    # container) only when it doesn't already exist. After the first
    # successful pull from auth.muninai.org/admin/contributors.yaml,
    # retrieval keeps the file fresh on a 5-minute timer; this
    # bootstrap is just so ingest works before the first sync lands.
    if [ -f "$SHARED_DIR/config/contributors.yml" ] && [ ! -f "$MUNIN_DATA/contributors.yml" ]; then
        run "install -m 0644 $SHARED_DIR/config/contributors.yml $MUNIN_DATA/contributors.yml"
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
# maintenance: install the maintenance-mode toggle into /opt/cluster/scripts/
# ------------------------------------------------------------------------------
MAINTENANCE_SCRIPTS=/opt/cluster/scripts/maintenance

deploy_maintenance() {
    echo "[maintenance] Installing maintenance-mode toggle..."
    need_file "$REPO_DIR/scripts/maintenance/maintenance.sh"
    run "install -d -m 0755 $MAINTENANCE_SCRIPTS"
    run "install -m 0755 $REPO_DIR/scripts/maintenance/maintenance.sh \
        $MAINTENANCE_SCRIPTS/maintenance.sh"
    run "ln -sf $MAINTENANCE_SCRIPTS/maintenance.sh /usr/local/bin/munin-maintenance"
    echo "[OK] maintenance — toggle with:"
    echo "     sudo munin-maintenance on [\"message\"] | off | status"
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
# knowledge: install the §15 embedding-map script + nightly timer
# ------------------------------------------------------------------------------
KNOWLEDGE_VENV=/opt/munin/services/knowledge/venv
PIPELINE_DIR=/opt/cluster/scripts/pipeline

# ------------------------------------------------------------------------------
# pipeline: sync paper_pipeline.py → /opt/cluster/scripts/pipeline/
# (mounted read-only into the retrieval container for /api/admin/ingest)
# ------------------------------------------------------------------------------
PIPELINE_VENV=/opt/munin/services/pipeline/venv

deploy_pipeline() {
    echo "[pipeline] Syncing paper_pipeline.py + paper_cleanup.py..."
    need_file "$REPO_DIR/scripts/pipeline/paper_pipeline.py"
    need_file "$REPO_DIR/scripts/pipeline/paper_cleanup.py"
    need_file "$REPO_DIR/scripts/pipeline/requirements.txt"
    need_file "$REPO_DIR/config/munin-paper-pipeline.service"
    need_file "$REPO_DIR/config/munin-paper-reattribute.service"
    need_file "$REPO_DIR/config/munin-paper-reattribute.timer"
    need_file "$REPO_DIR/config/munin-paper-detect.service"

    run "install -d -m 0755 $PIPELINE_DIR"
    run "install -m 0755 $REPO_DIR/scripts/pipeline/paper_pipeline.py \
        $PIPELINE_DIR/paper_pipeline.py"
    run "install -m 0755 $REPO_DIR/scripts/pipeline/paper_cleanup.py \
        $PIPELINE_DIR/paper_cleanup.py"
    # Phase C migration script: one-shot tool that moves entries from
    # legacy quarantine dirs (skipped/, pdf/skipped/, pdf/failed/)
    # into the new pdf/quarantine/ layout. Idempotent; defaults to
    # --dry-run. Run once after deploying Phase B; safe to leave on
    # disk afterwards (re-runs are no-ops).
    run "install -m 0755 $REPO_DIR/scripts/pipeline/migrate_quarantine_layout.py \
        $PIPELINE_DIR/migrate_quarantine_layout.py"
    # seed_processed_markers.py and reattribute_unknown.py were
    # retired in the 2026-05-13 pipeline consolidation (see
    # docs/PIPELINE-CONSOLIDATION-PLAN.md). The first was a one-shot
    # backfill whose problem is long solved; the second is now the
    # `reattribute` subcommand of paper_cleanup.py.
    run "install -m 0644 $REPO_DIR/scripts/pipeline/requirements.txt \
        $PIPELINE_DIR/requirements.txt"

    # PDF drop + inbox + quarantine directories. The watcher daemon
    # scans /opt/munin/data/papers/pdf/*.pdf; /api/admin/ingest writes
    # to pdf/inbox/ and the pipeline's _dispose_post_pipeline moves
    # PDFs to pdf/doi_{hash}.pdf (live) or pdf/quarantine/ (failed).
    # Phase B+C of the 2026-05-13 consolidation merged the legacy
    # skipped/+failed/ trees into one quarantine/ — fresh deploys no
    # longer create the legacy dirs. Existing clusters keep them
    # until the operator rmdirs them post-migration.
    run "install -d -m 0755 $MUNIN_DATA/papers/pdf/inbox"
    run "install -d -m 0755 $MUNIN_DATA/papers/pdf/quarantine"
    run "install -d -m 0755 $MUNIN_DATA/papers/processed"
    run "install -d -m 0755 $(dirname $PIPELINE_VENV)"

    # Dedicated venv for the host-side watcher + cleanup daemons.
    # Debian 12+ enforces PEP 668 so system pip is off-limits;
    # mirrors the knowledge + vLLM patterns.
    if [ "$DRY_RUN" = "0" ]; then
        if [ ! -x "$PIPELINE_VENV/bin/python3" ]; then
            echo "[pipeline] Creating venv at $PIPELINE_VENV..."
            python3 -m venv "$PIPELINE_VENV"
        fi
        if "$PIPELINE_VENV/bin/python3" -m pip install --quiet --upgrade \
                -r "$REPO_DIR/scripts/pipeline/requirements.txt"; then
            echo "[OK] pipeline — deps installed into $PIPELINE_VENV"
        else
            echo "[WARN] pipeline — pip install into venv failed; install manually:"
            echo "       $PIPELINE_VENV/bin/pip install -r $PIPELINE_DIR/requirements.txt"
        fi
    else
        echo "  [dry-run] would create venv at $PIPELINE_VENV and install requirements"
    fi

    # Systemd units. Three always-on services + one nightly timer:
    #   munin-paper-pipeline.service     (Type=simple) watcher loop
    #   munin-paper-detect.service       (Type=simple) continuous sweep
    #   munin-paper-reattribute.timer    (04:30 daily) attribution backfill
    # munin-paper-cleanup.{service,timer} were retired in Phase F
    # (2026-05-13): the always-on detect daemon now does what the
    # nightly cleanup batch used to do, in continuous trickle mode.
    run "install -m 0644 $REPO_DIR/config/munin-paper-pipeline.service \
        $SYSTEMD_DIR/munin-paper-pipeline.service"
    run "install -m 0644 $REPO_DIR/config/munin-paper-detect.service \
        $SYSTEMD_DIR/munin-paper-detect.service"
    run "install -m 0644 $REPO_DIR/config/munin-paper-reattribute.service \
        $SYSTEMD_DIR/munin-paper-reattribute.service"
    run "install -m 0644 $REPO_DIR/config/munin-paper-reattribute.timer \
        $SYSTEMD_DIR/munin-paper-reattribute.timer"
    run "systemctl daemon-reload"

    # Phase F migration: stop + disable the legacy nightly cleanup
    # timer. Best-effort (ignores failure if it's already gone or
    # was never installed).
    run "systemctl disable --now munin-paper-cleanup.timer 2>/dev/null || true"
    # Drop the leftover unit files so list-units stays tidy.
    run "rm -f $SYSTEMD_DIR/munin-paper-cleanup.service $SYSTEMD_DIR/munin-paper-cleanup.timer"
    run "systemctl daemon-reload"

    # Start everything. --now starts immediately; services run
    # continuously, the timer fires at its OnCalendar.
    run "systemctl enable munin-paper-pipeline.service"
    run "systemctl restart munin-paper-pipeline.service"
    run "systemctl enable --now munin-paper-detect.service"
    run "systemctl enable --now munin-paper-reattribute.timer"

    echo "[OK] pipeline — watcher + detect daemons running, reattribute timer armed"
    echo "      Logs:    journalctl -fu munin-paper-pipeline.service"
    echo "               journalctl -fu munin-paper-detect.service"
    echo "               journalctl -u munin-paper-reattribute.service --since today"
    echo "      Timers:  systemctl list-timers 'munin-paper-*'"
    echo "      Tunables (in /etc/systemd/system/munin-paper-detect.service):"
    echo "               DETECT_KINDS, DETECT_PER_CYCLE_LIMIT, DETECT_CYCLE_PACE_SECS"
}

deploy_knowledge() {
    echo "[knowledge] Installing build_embedding_map.py + timer..."
    need_file "$REPO_DIR/scripts/knowledge/build_embedding_map.py"
    need_file "$REPO_DIR/scripts/knowledge/requirements.txt"
    need_file "$REPO_DIR/config/munin-embedding-map.service"
    need_file "$REPO_DIR/config/munin-embedding-map.timer"

    run "install -d -m 0755 $CLUSTER_SCRIPTS/../knowledge"
    run "install -m 0755 $REPO_DIR/scripts/knowledge/build_embedding_map.py \
        /opt/cluster/scripts/knowledge/build_embedding_map.py"
    run "install -m 0644 $REPO_DIR/scripts/knowledge/requirements.txt \
        /opt/cluster/scripts/knowledge/requirements.txt"
    run "install -d -m 0755 $MUNIN_ROOT/knowledge"
    run "install -d -m 0755 $(dirname $KNOWLEDGE_VENV)"

    # Dedicated venv — Debian 12+ enforces PEP 668, so system pip is off-limits.
    # Mirrors the vLLM venv pattern. Idempotent: venv is created once, then
    # requirements are re-synced on every deploy.
    if [ "$DRY_RUN" = "0" ]; then
        if [ ! -x "$KNOWLEDGE_VENV/bin/python3" ]; then
            echo "[knowledge] Creating venv at $KNOWLEDGE_VENV..."
            python3 -m venv "$KNOWLEDGE_VENV"
        fi
        if "$KNOWLEDGE_VENV/bin/python3" -m pip install --quiet --upgrade \
                -r "$REPO_DIR/scripts/knowledge/requirements.txt"; then
            echo "[OK] knowledge — deps installed into $KNOWLEDGE_VENV"
        else
            echo "[WARN] knowledge — pip install into venv failed; install manually:"
            echo "       $KNOWLEDGE_VENV/bin/pip install -r /opt/cluster/scripts/knowledge/requirements.txt"
        fi
    else
        echo "  [dry-run] would create venv at $KNOWLEDGE_VENV and install requirements"
    fi

    run "install -m 0644 $REPO_DIR/config/munin-embedding-map.service \
        $SYSTEMD_DIR/munin-embedding-map.service"
    run "install -m 0644 $REPO_DIR/config/munin-embedding-map.timer \
        $SYSTEMD_DIR/munin-embedding-map.timer"
    run "systemctl daemon-reload"

    run "systemctl enable munin-embedding-map.timer"
    run "systemctl restart munin-embedding-map.timer"
    echo "[OK] knowledge — nightly timer enabled (03:00 local)"
    echo "      First run manually with:  systemctl start munin-embedding-map.service"
    echo "      Watch progress with:       journalctl -fu munin-embedding-map.service"
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
# monitoring: install Prometheus config, bring it up. Dashboards live
# in webui (AdminPanel -> Metrics tab) and query Prometheus via a
# retrieval-side proxy. There is no Grafana service.
# ------------------------------------------------------------------------------
# Idempotent: re-running it copies the latest scrape config to
# /opt/munin/docker/prometheus/ and recreates the container under the
# `monitoring` profile. Bound to 127.0.0.1:9090 only.
deploy_monitoring() {
    echo "[monitoring] Installing Prometheus config..."
    need_file "$REPO_DIR/docker/prometheus/prometheus.yml"
    need_file "$REPO_DIR/docker/docker-compose.yml"

    # Refresh the compose file too. The prometheus service lives under
    # the `monitoring` profile; if /opt/munin/docker/docker-compose.yml
    # predates it, `docker compose up` will reject `prometheus` as an
    # unknown service.
    run "install -d -m 0755 $MUNIN_DOCKER"
    run "install -m 0644 $REPO_DIR/docker/docker-compose.yml \
        $MUNIN_DOCKER/docker-compose.yml"

    local MUNIN_PROM=$MUNIN_DOCKER/prometheus
    run "install -d -m 0755 $MUNIN_PROM"
    run "install -m 0644 $REPO_DIR/docker/prometheus/prometheus.yml \
        $MUNIN_PROM/prometheus.yml"

    if [ "$DRY_RUN" = "0" ]; then
        if docker info >/dev/null 2>&1; then
            # Tear down the legacy Grafana container if it's still
            # running from a pre-2026-06-02 deploy. Volume `grafana_data`
            # is preserved so a future re-introduction of Grafana could
            # reuse it; remove it manually if you want a clean wipe:
            #   docker volume rm frontend_grafana_data
            if docker ps --format '{{.Names}}' | grep -q '^munin-grafana$'; then
                echo "[monitoring] tearing down legacy munin-grafana container..."
                run "docker stop munin-grafana"
                run "docker rm munin-grafana"
            fi
            run "cd $MUNIN_DOCKER && docker compose --profile monitoring up -d prometheus"
            echo "[OK] monitoring — prometheus up on 127.0.0.1:9090"
            echo "      Dashboards: webui AdminPanel -> Metrics tab"
        else
            echo "[WARN] docker unreachable; bring up later with:"
            echo "       docker compose --profile monitoring up -d prometheus"
        fi
    else
        echo "  [dry-run] would docker compose up -d prometheus"
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
stage_qwen_tokenizer() {
    # Copy the Qwen3 tokenizer files out of the vLLM model dir into a
    # small dedicated dir the retrieval container mounts read-only
    # (P1 #8). The container budgets context tokens with the real
    # tokenizer instead of a char heuristic. If the model has not been
    # downloaded yet (first deploy, before vLLM's first run) this is a
    # graceful no-op — chat_context falls back to the heuristic and
    # picks the tokenizer up on a later deploy.
    echo "[tokenizer] Staging Qwen tokenizer for retrieval..."
    run "install -d -m 0755 $QWEN_TOKENIZER_DIR"
    if [ -f "$VLLM_MODEL_DIR/tokenizer.json" ]; then
        run "install -m 0644 $VLLM_MODEL_DIR/tokenizer.json \
            $QWEN_TOKENIZER_DIR/tokenizer.json"
        if [ -f "$VLLM_MODEL_DIR/tokenizer_config.json" ]; then
            run "install -m 0644 $VLLM_MODEL_DIR/tokenizer_config.json \
                $QWEN_TOKENIZER_DIR/tokenizer_config.json"
        fi
        echo "[OK] tokenizer staged to $QWEN_TOKENIZER_DIR"
    else
        echo "[WARN] $VLLM_MODEL_DIR/tokenizer.json not found"
        echo "       (vLLM model not downloaded yet) — retrieval will use"
        echo "       the char-heuristic fallback until a later deploy."
    fi
}

deploy_retrieval() {
    echo "[retrieval] Syncing code to $MUNIN_RETRIEVAL..."
    need_file "$REPO_DIR/retrieval"
    run "install -d -m 0755 $MUNIN_RETRIEVAL"

    # Stage the Qwen tokenizer the container mounts for token budgeting.
    stage_qwen_tokenizer

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
    maintenance)  deploy_maintenance ;;
    deepresearch) deploy_deepresearch ;;
    tunnel)       deploy_tunnel ;;
    knowledge)    deploy_knowledge ;;
    pipeline)     deploy_pipeline ;;
    searxng)      deploy_searxng ;;
    sandbox)      deploy_sandbox ;;
    monitoring)   deploy_monitoring ;;
    retrieval)    deploy_retrieval ;;
    verify)       deploy_verify ;;
    all)
        deploy_dirs
        deploy_compose
        deploy_personas
        deploy_agents
        deploy_vllm
        deploy_maintenance
        deploy_deepresearch
        deploy_tunnel
        deploy_knowledge
        deploy_pipeline
        deploy_searxng
        deploy_sandbox       # must be up before retrieval starts since
                             # retrieval depends_on sandbox in the compose
        deploy_retrieval     # ends with deploy_verify
        ;;
    *)
        echo "[ERROR] Unknown mode: $MODE"
        echo "Modes: all dirs compose personas agents vllm maintenance deepresearch tunnel knowledge pipeline retrieval sandbox searxng verify"
        exit 1
        ;;
esac

echo ""
echo "=============================================="
[ "$DRY_RUN" = "1" ] && echo "Dry run complete — no changes were made." || echo "Done."
echo "=============================================="
