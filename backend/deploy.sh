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
#   sudo ./deploy.sh models         - stage embedding models (bge-large etc.)
#   sudo ./deploy.sh vllm           - scripts/vllm/*.sh → /opt/cluster/scripts/llm/,
#                                     config/models/*.env → /opt/munin/config/models/
#   sudo ./deploy.sh model activate <slug>  - make config/models/<slug>.env the
#                                     production backbone: writes active-model.env,
#                                     stages its tokenizer, recreates retrieval.
#                                     vLLM itself restarts on the next
#                                     `vllm-service stop && vllm-service start`
#                                     (or pass --restart-vllm to do it now).
#   sudo ./deploy.sh model status   - active profile vs what vLLM / retrieval serve
#   sudo ./deploy.sh model validate <slug>  - check a profile file, change nothing
#   sudo ./deploy.sh instance up <slug> --name <n> [--vllm-port 8001] [--api-port 8082]
#                                     [--corpus shadow] [--vllm <other-instance>]
#                                     [--gpu-util 0.90] [--download]
#                                   - a SECOND backbone beside production: its own
#                                     vLLM job on GPU 0 (or another instance's vLLM)
#                                     and its own retrieval container, gated, never
#                                     routed to users. Eval only.
#   sudo ./deploy.sh instance down <n> | ls | gates <n> | logs <n> [docker-logs args]
#   sudo ./deploy.sh sudoers        - passwordless deploy.sh / vllm-service /
#                                     munin-maintenance for MUNIN_OPERATOR
#                                     (default: the invoking sudo user), so the
#                                     benchmark driver can run unattended
#   sudo ./deploy.sh maintenance    - maintenance-mode toggle → munin-maintenance
#   sudo ./deploy.sh deepresearch   - LEGACY MiroThinker path (disabled; not in `all`)
#                                     add --with-model to fetch the 17 GB weights
#   sudo ./deploy.sh tunnel         - munin-tunnel.service (+ daemon-reload + restart)
#   sudo ./deploy.sh knowledge      - §15 embedding-map script + nightly timer
#   sudo ./deploy.sh pipeline       - paper_pipeline.py → /opt/cluster/scripts/pipeline/ (§28)
#   sudo ./deploy.sh retrieval      - retrieval/ code, rebuild + restart container
#   sudo ./deploy.sh searxng        - searxng settings.yml + restart container
#   sudo ./deploy.sh monitoring     - prometheus + grafana on the metrics endpoint
#   sudo ./deploy.sh verify         - smoke-test /api/status, /api/personas,
#                                     the paper encoder/collection pairing and
#                                     the deep research router
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
    echo "Usage: sudo $0 [--dry-run] <mode> [--with-model]"
    echo "Modes: all dirs compose personas agents models vllm model instance sudoers maintenance deepresearch tunnel knowledge pipeline retrieval sandbox searxng monitoring verify"
    exit 1
fi
shift

# Opt-in to the 17 GB MiroThinker download in the legacy `deepresearch` mode.
# Off by default because the feature is disabled (see deploy_deepresearch).
WITH_MODEL=${DEEPRESEARCH_WITH_MODEL:-0}
# `model` takes a sub-command and its own flags; everything else takes none.
MODEL_ARGS=()
if [ "$MODE" = "model" ] || [ "$MODE" = "instance" ]; then
    MODEL_ARGS=("$@")
else
    for arg in "$@"; do
        case "$arg" in
            --with-model) WITH_MODEL=1 ;;
            *) echo "[ERROR] Unknown argument: $arg"; exit 1 ;;
        esac
    done
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

# The production backbone is named in exactly one place: the active model
# profile (/opt/munin/config/active-model.env, a copy of config/models/<slug>.env
# made by `deploy.sh model activate`). scripts/vllm/model-env.sh loads it and
# exports the variables docker compose substitutes into the retrieval
# container. Until 2026-09-15 this file carried its own VLLM_MODEL_DIR literal
# that had to be kept in step with the SLURM script by hand; missing it left
# the container budgeting context with the previous model's tokenizer.
MODEL_ENV_SH=$REPO_DIR/scripts/vllm/model-env.sh
MODEL_PROFILES_SRC=$REPO_DIR/config/models
MODEL_PROFILES_DST=$MUNIN_ROOT/config/models
INSTANCES_DIR=$MUNIN_ROOT/instances
INSTANCE_SCRIPT=$CLUSTER_SCRIPTS/start-vllm-instance.sh
GATES_PY=$CLUSTER_SCRIPTS/backbone_gates.py
ACTIVE_MODEL_ENV=$MUNIN_ROOT/config/active-model.env
# The profile a box with no active-model.env yet is assumed to run: the
# reference deployment. `model activate` replaces the assumption with a file.
DEFAULT_MODEL_SLUG=qwen3.8-27b

# Embedding models the retrieval container mounts read-only. bge-large is
# the live paper encoder since the 2026-07 cutover; specter/papers is the
# rollback pair. Keep these paths in sync with the retrieval volume mounts
# in docker/docker-compose.yml.
MODELS_DIR=$MUNIN_ROOT/data/models
BGE_LARGE_DIR=$MODELS_DIR/bge-large
BGE_LARGE_HF_ID="BAAI/bge-large-en-v1.5"
BGE_BASE_DIR=$MODELS_DIR/bge-base
SPECTER_DIR=$MODELS_DIR/specter

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
    if [ "$MODE" = "model" ] && [ "${MODEL_ARGS[0]:-}" != "activate" ]; then
        return 0   # status / validate only read
    fi
    if [ "$MODE" = "instance" ] && [ "${MODEL_ARGS[0]:-}" = "ls" ]; then
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
    # Runtime dirs the retrieval container writes into via the /data mount.
    # The code mkdirs them on first use, so this is mostly so a fresh cluster
    # has the layout visible (and documented) before anything runs. See
    # docs/RUNTIME-CONFIG.md for what writes where and how each one grows.
    #   deep_research/  in-process Deep Research job checkpoints (dr_*.json)
    #   agent_traces/   per-turn agent traces (AGENT_TRACE_DIR)
    #   agent_extracts/ source-agent extractions (AGENT_EXTRACT_DIR)
    #   papers_cached/  read_paper full-text cache (grows; PAPERS_CACHE_WARN_GB)
    run "mkdir -p $MUNIN_DATA/deep_research $MUNIN_DATA/agent_traces"
    run "mkdir -p $MUNIN_DATA/agent_extracts $MUNIN_DATA/papers_cached"
    # LEGACY MiroThinker queue (disabled 2026-07; kept so old reports resolve).
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
# ------------------------------------------------------------------------------
# Compose project guard.
#
# docker-compose.yml now pins `name: ${MUNIN_PREFIX:-munin}`. Before that it had
# no `name:`, so Compose derived the project from the directory basename —
# `docker` for /opt/munin/docker, and ALSO `docker` for a clone's own
# backend/docker. Two unrelated checkouts looked like one stack: a `compose up`
# from a clone recreated the live containers against the clone's empty data
# dirs, and a `compose down` took production out (2026-08-14).
#
# Consequence for the cluster: containers deployed before the pin carry
# project=docker and will collide by container_name with the newly-named
# project. Catch it here with the exact remedy rather than letting `up` fail
# halfway through a deploy.
# ------------------------------------------------------------------------------
COMPOSE_PROJECT_EXPECTED=${MUNIN_PREFIX:-munin}

# Returns the compose project of the running stack, or empty if nothing runs.
running_compose_project() {
    docker inspect -f '{{index .Config.Labels "com.docker.compose.project"}}' \
        "${COMPOSE_PROJECT_EXPECTED}-retrieval" 2>/dev/null || true
}

print_compose_migration() {
    local running="$1"
    echo ""
    echo "        One-time migration. ORDER MATTERS: the pinned name only takes"
    echo "        effect once the new compose file is installed, and the old"
    echo "        stack must be torn down by its OLD project name."
    echo ""
    echo "          # 1. install the compose file that pins the project name"
    echo "          sudo ./deploy.sh compose"
    echo ""
    echo "          # 2. tear down the OLD project. -p is required (the new file"
    echo "          #    now says '$COMPOSE_PROJECT_EXPECTED'), and the profile flags are"
    echo "          #    required or retrieval/sandbox/prometheus are left behind."
    echo "          cd $MUNIN_DOCKER"
    echo "          sudo docker compose -p $running --profile rag --profile monitoring down"
    echo ""
    echo "          # 3. bring it back up under the pinned name"
    echo "          sudo docker compose --profile rag --profile monitoring up -d"
    echo ""
    echo "        Application data is on bind mounts and is untouched. Prometheus"
    echo "        history is the one casualty: its volume is project-scoped, so"
    echo "        '${running}_prometheus_data' is orphaned (not deleted)."
    echo ""
}

# Load the active model profile into this shell (exports the compose-visible
# variables). Falls back to the repo's default profile with a warning on a box
# that has never run `model activate`, so a first deploy still works.
MODEL_ENV_LOADED=0
load_model_env() {
    [ "$MODEL_ENV_LOADED" = "1" ] && return 0
    need_file "$MODEL_ENV_SH"
    # shellcheck disable=SC1090
    source "$MODEL_ENV_SH"
    if [ -f "$ACTIVE_MODEL_ENV" ]; then
        munin_load_model_env "$ACTIVE_MODEL_ENV" || exit 1
    else
        echo "[model] no $ACTIVE_MODEL_ENV yet; assuming the $DEFAULT_MODEL_SLUG profile."
        echo "        Make it explicit with: sudo ./deploy.sh model activate $DEFAULT_MODEL_SLUG"
        munin_load_model_env "$MODEL_PROFILES_SRC/$DEFAULT_MODEL_SLUG.env" || exit 1
    fi
    MODEL_ENV_LOADED=1
}

# Blocks a mode that STARTS containers. Installing files is safe and must not
# be blocked -- `deploy.sh compose` is step 1 of the migration itself, so
# refusing it here would deadlock: the file that pins the name could never be
# installed. (It did, on 2026-08-18.)
check_compose_project() {
    local running
    running=$(running_compose_project)

    [ -z "$running" ] && return 0
    [ "$running" = "$COMPOSE_PROJECT_EXPECTED" ] && return 0

    echo ""
    echo "[ERROR] Compose project mismatch."
    echo "        Running containers belong to project '$running', but"
    echo "        docker-compose.yml pins project '$COMPOSE_PROJECT_EXPECTED'."
    echo "        Starting containers now would fail on name conflicts."
    print_compose_migration "$running"
    return 1
}

# Advisory version for `compose`, which only writes files.
warn_compose_project() {
    local running
    running=$(running_compose_project)

    [ -z "$running" ] && return 0
    [ "$running" = "$COMPOSE_PROJECT_EXPECTED" ] && return 0

    echo ""
    echo "[NOTE] The running stack is project '$running'; this file pins"
    echo "       '$COMPOSE_PROJECT_EXPECTED'. Installing it is safe and changes nothing"
    echo "       until you recreate the containers, but until you do, a plain"
    echo "       'docker compose up' here will try to create a SECOND stack and"
    echo "       fail on container-name conflicts."
    print_compose_migration "$running"
    return 0
}

deploy_compose() {
    echo "[compose] Installing docker-compose.yml..."
    warn_compose_project
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
# models: make sure the embedding models retrieval mounts actually exist
# ------------------------------------------------------------------------------
# The retrieval container mounts three model dirs read-only. If bge-large is
# missing, database.get_paper_encoder() silently falls back to pulling
# BAAI/bge-large-en-v1.5 from HuggingFace into the container's ephemeral
# layer: 1.3 GB re-downloaded on every rebuild, and a hard failure on a host
# with no outbound network. Staging it here makes the live encoder a
# deployed artifact like everything else.
#
# specter + bge-base are checked but never downloaded: specter is the
# rollback encoder and bge-base serves user docs, both provisioned during
# initial cluster setup (SETUP-CLUSTER.md).
hf_cli() {
    # The HF CLI was renamed huggingface-cli -> hf. Both spellings exist in
    # the wild depending on when the venv was built; prefer the new one.
    if [ -x "$VLLM_VENV/bin/hf" ]; then
        echo "$VLLM_VENV/bin/hf"
    elif [ -x "$VLLM_VENV/bin/huggingface-cli" ]; then
        echo "$VLLM_VENV/bin/huggingface-cli"
    fi
}

deploy_models() {
    echo "[models] Checking embedding models under $MODELS_DIR..."
    run "install -d -m 0755 $MODELS_DIR"

    for d in "$SPECTER_DIR:specter (rollback paper encoder)" \
             "$BGE_BASE_DIR:bge-base (user documents)"; do
        local path=${d%%:*}
        local what=${d#*:}
        if [ -d "$path" ]; then
            echo "  [OK] $what present"
        else
            echo "  [warn] $what MISSING at $path"
            echo "         retrieval falls back to a HuggingFace pull at runtime;"
            echo "         see SETUP-CLUSTER.md for the intended provisioning."
        fi
    done

    if [ -f "$BGE_LARGE_DIR/config.json" ]; then
        echo "  [OK] bge-large (live paper encoder) present"
        echo "[OK] models"
        return 0
    fi

    echo "  bge-large not found at $BGE_LARGE_DIR"
    echo "  Model: $BGE_LARGE_HF_ID (~1.3 GB)"
    if [ "$DRY_RUN" = "1" ]; then
        echo "  [dry-run] would download $BGE_LARGE_HF_ID → $BGE_LARGE_DIR"
        return 0
    fi

    local cli
    cli=$(hf_cli)
    if [ -z "$cli" ]; then
        echo "[WARN] no HuggingFace CLI in $VLLM_VENV — download manually:"
        echo "       $VLLM_VENV/bin/hf download $BGE_LARGE_HF_ID \\"
        echo "           --local-dir $BGE_LARGE_DIR"
        echo "       Until then the paper encoder is fetched from HF on every"
        echo "       container start."
        return 0
    fi

    run "install -d -m 0755 $BGE_LARGE_DIR"
    if "$cli" download "$BGE_LARGE_HF_ID" --local-dir "$BGE_LARGE_DIR"; then
        echo "[OK] models — bge-large staged to $BGE_LARGE_DIR"
    else
        echo "[WARN] bge-large download failed; retrieval will fetch it from HF"
        echo "       at runtime. Retry: sudo $REPO_DIR/deploy.sh models"
    fi
}

# ------------------------------------------------------------------------------
# vllm: copy SLURM job + cron wrapper into /opt/cluster/scripts/llm/
# ------------------------------------------------------------------------------
deploy_vllm() {
    echo "[vllm] Installing vLLM scripts..."
    need_file "$REPO_DIR/scripts/vllm/start-vllm-service.sh"
    need_file "$REPO_DIR/scripts/vllm/start-vllm-service-tp2.sh"
    need_file "$REPO_DIR/scripts/vllm/schedule-vllm.sh"
    need_file "$REPO_DIR/scripts/vllm/check-context-window.sh"
    need_file "$REPO_DIR/scripts/vllm/check-vllm-health.sh"
    need_file "$REPO_DIR/config/munin-vllm-health.service"
    need_file "$REPO_DIR/config/munin-vllm-health.timer"
    run "install -d -m 0755 $CLUSTER_SCRIPTS"
    run "install -m 0755 $REPO_DIR/scripts/vllm/start-vllm-service.sh $CLUSTER_SCRIPTS/start-vllm-service.sh"
    run "install -m 0755 $REPO_DIR/scripts/vllm/start-vllm-service-tp2.sh $CLUSTER_SCRIPTS/start-vllm-service-tp2.sh"
    run "install -m 0755 $REPO_DIR/scripts/vllm/schedule-vllm.sh $CLUSTER_SCRIPTS/schedule-vllm.sh"
    run "install -m 0755 $REPO_DIR/scripts/vllm/check-context-window.sh $CLUSTER_SCRIPTS/check-context-window.sh"
    run "install -m 0755 $REPO_DIR/scripts/vllm/check-vllm-health.sh $CLUSTER_SCRIPTS/check-vllm-health.sh"
    run "install -m 0755 $REPO_DIR/scripts/vllm/model-env.sh $CLUSTER_SCRIPTS/model-env.sh"
    run "install -m 0755 $REPO_DIR/scripts/vllm/start-vllm-instance.sh $CLUSTER_SCRIPTS/start-vllm-instance.sh"
    run "install -m 0755 $REPO_DIR/scripts/vllm/backbone_gates.py $CLUSTER_SCRIPTS/backbone_gates.py"
    run "ln -sf $CLUSTER_SCRIPTS/schedule-vllm.sh /usr/local/bin/vllm-service"
    # Model profiles. The SLURM scripts source the ACTIVE one at job start.
    run "install -d -m 0755 $MODEL_PROFILES_DST"
    local _prof
    for _prof in "$MODEL_PROFILES_SRC"/*.env; do
        source "$MODEL_ENV_SH"
        munin_validate_model_env "$_prof" || { echo "[ERROR] bad profile $_prof"; exit 1; }
        run "install -m 0644 $_prof $MODEL_PROFILES_DST/$(basename "$_prof")"
    done
    run "install -m 0644 $MODEL_PROFILES_SRC/README.md $MODEL_PROFILES_DST/README.md"
    echo "[OK] model profiles -> $MODEL_PROFILES_DST ($(ls "$MODEL_PROFILES_SRC"/*.env | wc -l))"
    if [ ! -f "$ACTIVE_MODEL_ENV" ]; then
        echo "[NOTE] no active profile yet: the SLURM scripts will refuse to start until"
        echo "       sudo ./deploy.sh model activate $DEFAULT_MODEL_SLUG   (the reference deployment)"
    fi
    echo "[OK] vllm - scripts installed. Takes effect on the NEXT vLLM start."
    echo "     Profiles (the choice PERSISTS, so the nightly 6am start restores it):"
    echo "       single : 1 GPU,  64k, --max-num-seqs 2   sudo vllm-service start single"
    echo "       tp2    : 2 GPUs, 64k, --max-num-seqs 8   sudo vllm-service start tp2"
    echo "     Cut over with: sudo vllm-service stop && sudo vllm-service start"
    echo "     Do NOT sbatch the tp2 script directly: that leaves the profile"
    echo "     unset, so the 6am cron silently brings back the single-GPU one."
    echo "     Large-window variant (128k, drop --max-num-seqs to 4):"
    echo "       MAX_MODEL_LEN=131072 MAX_NUM_SEQS=4 sudo -E sbatch \\"
    echo "         $CLUSTER_SCRIPTS/start-vllm-service-tp2.sh"

    # Health check. A systemd TIMER rather than a /etc/cron.d entry because the
    # cron file is written by HuginSLURM setup/07-scheduling-setup.sh, which
    # runs once per node and would never reach an already-provisioned cluster.
    echo "[vllm] Installing the health-check timer..."
    run "install -m 0644 $REPO_DIR/config/munin-vllm-health.service \
        $SYSTEMD_DIR/munin-vllm-health.service"
    run "install -m 0644 $REPO_DIR/config/munin-vllm-health.timer \
        $SYSTEMD_DIR/munin-vllm-health.timer"
    run "systemctl daemon-reload"
    run "systemctl enable --now munin-vllm-health.timer"
    echo "[OK] vllm-health - hourly at :15, skips the 02:00-06:00 downtime window."
    echo "     Alerts go to /var/log/cluster-admin/vllm-health.log and wall."
    echo "     For off-machine alerts set MUNIN_ALERT_WEBHOOK in"
    echo "     /opt/hugin/config/cluster.env (unset = log + wall only)."
    echo "     Pause it during planned maintenance:"
    echo "       touch /opt/munin/logs/vllm-health.hold"
    echo "     Dry run: sudo $CLUSTER_SCRIPTS/check-vllm-health.sh --dry-run"
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
# deepresearch: LEGACY MiroThinker path (disabled; NOT part of `all`)
# ------------------------------------------------------------------------------
# This is the retired standalone Deep Research: research.muninai.org, the
# /deepresearch/* endpoints, the deepresearch-daemon, and a SLURM job running
# MiroThinker-30B. Disabled 2026-07 (DECISIONS.md) and deliberately dropped
# from `all`, so a fresh cluster does not provision a feature that is off.
#
# The Deep Research users actually get runs IN-PROCESS inside retrieval
# (/api/research/*, deep_research_manager.py) and needs no separate deploy
# step beyond the dirs created by `deploy.sh dirs`.
#
# The 17 GB MiroThinker download is opt-in: `deploy.sh deepresearch --with-model`
# or DEEPRESEARCH_WITH_MODEL=1. Weights already on disk are left alone.
deploy_deepresearch() {
    echo "[deepresearch] LEGACY MiroThinker path. Deep Research submission is"
    echo "[deepresearch] DISABLED; read endpoints stay up for past reports."
    echo "[deepresearch] Re-enable needs BOTH:"
    echo "[deepresearch]   1. DEEPRESEARCH_ENABLED=1 in $HUGIN_ENV, then"
    echo "[deepresearch]      docker compose --profile rag up -d retrieval"
    echo "[deepresearch]   2. systemctl enable --now deepresearch-daemon"
    echo "[deepresearch] The in-chat Deep Research (/api/research/*) is separate"
    echo "[deepresearch] and unaffected by any of this."
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
    if [ -d "$MIROTHINKER_MODEL_DIR" ]; then
        echo "[deepresearch] MiroThinker weights present at $MIROTHINKER_MODEL_DIR (~17 GB)."
        echo "[deepresearch] Kept on disk per DECISIONS.md; delete manually if the"
        echo "[deepresearch] retirement is made final."
    elif [ "$WITH_MODEL" != "1" ]; then
        echo "[deepresearch] MiroThinker weights NOT present and NOT downloaded."
        echo "[deepresearch] The feature is disabled, so 17 GB is not spent by default."
        echo "[deepresearch] To fetch them: sudo $0 deepresearch --with-model"
    else
        echo "[deepresearch] Downloading MiroThinker (opt-in via --with-model)..."
        echo "               $MIROTHINKER_MODEL_ID (~17 GB, 10-30 min)"
        local cli
        cli=$(hf_cli)
        if [ "$DRY_RUN" = "1" ]; then
            echo "  [dry-run] would download $MIROTHINKER_MODEL_ID → $MIROTHINKER_MODEL_DIR"
        elif [ -z "$cli" ]; then
            echo "[WARN] no HuggingFace CLI in $VLLM_VENV — download manually:"
            echo "       $VLLM_VENV/bin/hf download $MIROTHINKER_MODEL_ID \\"
            echo "           --local-dir $MIROTHINKER_MODEL_DIR"
        elif "$cli" download "$MIROTHINKER_MODEL_ID" \
                --local-dir "$MIROTHINKER_MODEL_DIR"; then
            echo "[OK] MiroThinker downloaded"
        else
            echo "[WARN] MiroThinker download failed"
        fi
    fi

    echo "[OK] deepresearch (legacy) — daemon stays disabled unless you enable it"
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
    # Daily-schedule wrapper. The cluster drives these jobs with systemd
    # timers and does not need it, but it lives one level up at the scripts
    # root so the containerised equivalents (compose `pipeline` profile, which
    # mounts that root) work on any host without a second sync path.
    need_file "$REPO_DIR/scripts/run-daily.sh"
    run "install -m 0755 $REPO_DIR/scripts/run-daily.sh \
        $(dirname $PIPELINE_DIR)/run-daily.sh"
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
    # Author-name hygiene (Phase C, 2026-08). author_names.py is imported by
    # paper_pipeline.py at startup, so it MUST land alongside it or ingest
    # breaks. audit_authors.py is the read-only corpus report.
    need_file "$REPO_DIR/scripts/pipeline/author_names.py"
    need_file "$REPO_DIR/scripts/pipeline/audit_authors.py"
    need_file "$REPO_DIR/scripts/pipeline/repair_authors.py"
    run "install -m 0644 $REPO_DIR/scripts/pipeline/author_names.py \
        $PIPELINE_DIR/author_names.py"
    run "install -m 0755 $REPO_DIR/scripts/pipeline/audit_authors.py \
        $PIPELINE_DIR/audit_authors.py"
    # repair_authors.py is the one-way backfill (Qdrant payload writes).
    # Deployed but never run by any timer or service: it is operator-invoked,
    # dry-run by default, and needs --apply.
    run "install -m 0755 $REPO_DIR/scripts/pipeline/repair_authors.py \
        $PIPELINE_DIR/repair_authors.py"
    # repair_author_graph.py reconciles the Neo4j author graph with the
    # (already repaired) Qdrant payloads. Same rules: operator-invoked,
    # dry-run by default, and --apply refuses to run without --backup.
    need_file "$REPO_DIR/scripts/pipeline/repair_author_graph.py"
    run "install -m 0755 $REPO_DIR/scripts/pipeline/repair_author_graph.py \
        $PIPELINE_DIR/repair_author_graph.py"
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

    # The VPS host is deployment-specific and deliberately NOT in the repo
    # (it used to be a hardcoded public IP). It comes from MUNIN_VPS_HOST in
    # cluster.env; without it we would install a unit that tries to ssh to
    # the literal string __MUNIN_VPS_HOST__, so fail loudly instead.
    local vps_host
    vps_host=$(clusterenv_get MUNIN_VPS_HOST)
    if [ -z "$vps_host" ]; then
        echo "[ERROR] MUNIN_VPS_HOST is not set in $HUGIN_ENV."
        echo "        Add it (the VPS hostname or IP the tunnel connects to):"
        echo "          echo 'MUNIN_VPS_HOST=vps.example.org' >> $HUGIN_ENV"
        exit 1
    fi
    echo "[tunnel] Target host: $vps_host"

    if [ "$DRY_RUN" = "1" ]; then
        echo "  [dry-run] would render __MUNIN_VPS_HOST__ -> $vps_host and install the unit"
    else
        local rendered
        rendered=$(mktemp)
        sed "s#__MUNIN_VPS_HOST__#${vps_host}#g" \
            "$REPO_DIR/config/munin-tunnel.service" > "$rendered"
        install -m 0644 "$rendered" "$SYSTEMD_DIR/munin-tunnel.service"
        rm -f "$rendered"
    fi
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
stage_tokenizer() {
    # Copy the served backbone's tokenizer files out of its checkpoint dir
    # ($MODEL_PATH from the loaded profile) into a small dedicated dir the
    # retrieval container mounts read-only ($TOKENIZER_HOST_DIR; P1 #8). The
    # container budgets context tokens with the real tokenizer instead of a
    # char heuristic. If the checkpoint has not been downloaded yet (first
    # deploy) this is a graceful no-op: chat_context falls back to the
    # heuristic and picks the tokenizer up on a later deploy. Prints the
    # sha256 so a stale copy is visible in the deploy log.
    load_model_env
    echo "[tokenizer] Staging $MODEL_NAME tokenizer for retrieval..."
    run "install -d -m 0755 $TOKENIZER_HOST_DIR"
    if [ -f "$MODEL_PATH/tokenizer.json" ]; then
        run "install -m 0644 $MODEL_PATH/tokenizer.json \
            $TOKENIZER_HOST_DIR/tokenizer.json"
        if [ -f "$MODEL_PATH/tokenizer_config.json" ]; then
            run "install -m 0644 $MODEL_PATH/tokenizer_config.json \
                $TOKENIZER_HOST_DIR/tokenizer_config.json"
        fi
        # Some checkpoints (gpt-oss) carry a special_tokens_map.json the
        # tokenizers library consults; harmless when absent.
        if [ -f "$MODEL_PATH/special_tokens_map.json" ]; then
            run "install -m 0644 $MODEL_PATH/special_tokens_map.json \
                $TOKENIZER_HOST_DIR/special_tokens_map.json"
        fi
        echo "[OK] tokenizer staged to $TOKENIZER_HOST_DIR" \
             "(sha256 $(sha256sum "$MODEL_PATH/tokenizer.json" | cut -c1-12) from $MODEL_PATH)"
    else
        echo "[WARN] $MODEL_PATH/tokenizer.json not found"
        echo "       (checkpoint not downloaded yet) — retrieval will use"
        echo "       the char-heuristic fallback until a later deploy."
    fi
}

deploy_retrieval() {
    echo "[retrieval] Syncing code to $MUNIN_RETRIEVAL..."
    # This mode ends in `compose up`, so refuse before doing any work if the
    # running stack predates the project-name pin.
    check_compose_project || exit 1
    need_file "$REPO_DIR/retrieval"
    run "install -d -m 0755 $MUNIN_RETRIEVAL"

    # Load the active backbone profile (exports VLLM_MODEL_NAME, thinking mode,
    # sampling, ... for the compose substitution below) and stage its tokenizer.
    load_model_env
    stage_tokenizer

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

# Read a single KEY=value from cluster.env without sourcing the whole
# secret file. Empty if the key is absent or the file isn't readable
# (e.g. standalone `verify` run without sudo — cluster.env is 0600 root).
clusterenv_get() {
    local key="$1"
    [ -r "$HUGIN_ENV" ] || return 0
    grep -E "^${key}=" "$HUGIN_ENV" 2>/dev/null | tail -n1 | cut -d= -f2-
}

# first6/last4 fingerprint of a secret — matches the format the manual
# MONITORING.md cross-check prints, so a mismatch here can be diffed
# against that doc's commands. Never prints the full token.
kb_fingerprint() {
    local t="$1"
    [ -n "$t" ] || { printf 'unset'; return; }
    printf 'first6=%s last4=%s' \
        "$(printf %s "$t" | head -c 6)" "$(printf %s "$t" | tail -c 4)"
}

# KB_GATE_TOKEN is a shared secret with no auto-sync: the cluster holds
# it in cluster.env, the VPS in frontend/.env. If they drift, the admin
# Metrics tab silently breaks (proxy 502 / check-role 401 — exactly the
# 2026-06-23 incident). This compares fingerprints and WARNS on mismatch.
# Always returns 0 — a token drift must never fail a retrieval deploy.
#
# Cross-host comparison is opt-in: set METRICS_VPS_SSH (and optionally
# METRICS_VPS_SSH_KEY) in cluster.env to the VPS ssh target. Unset, or an
# unreachable VPS, downgrades to a skip note rather than a failure.
check_kb_token_sync() {
    local cluster_token cluster_fp
    cluster_token=$(clusterenv_get KB_GATE_TOKEN)
    cluster_fp=$(kb_fingerprint "$cluster_token")

    if [ ! -r "$HUGIN_ENV" ]; then
        echo "  [skip] KB_GATE_TOKEN sync — $HUGIN_ENV not readable (run via sudo to check)"
        return 0
    fi

    local vps_ssh vps_key
    vps_ssh=$(clusterenv_get METRICS_VPS_SSH)
    vps_key=$(clusterenv_get METRICS_VPS_SSH_KEY)
    if [ -z "$vps_ssh" ]; then
        echo "  [skip] KB_GATE_TOKEN VPS sync — set METRICS_VPS_SSH in cluster.env to enable (cluster fp: $cluster_fp)"
        return 0
    fi

    local ssh_opts="-o ConnectTimeout=8 -o BatchMode=yes"
    [ -n "$vps_key" ] && ssh_opts="$ssh_opts -i $vps_key"
    local vps_token vps_fp
    vps_token=$(ssh $ssh_opts "$vps_ssh" \
        'docker exec frontend-munin-auth-1 sh -c "printf %s \"\$KB_GATE_TOKEN\""' \
        2>/dev/null || true)
    if [ -z "$vps_token" ]; then
        echo "  [warn] KB_GATE_TOKEN sync — couldn't read the VPS token over ssh ($vps_ssh); skipped (cluster fp: $cluster_fp)"
        return 0
    fi
    vps_fp=$(kb_fingerprint "$vps_token")

    if [ "$cluster_token" = "$vps_token" ]; then
        echo "  [OK] KB_GATE_TOKEN matches VPS ($cluster_fp)"
    else
        echo "[WARN] KB_GATE_TOKEN MISMATCH — admin Metrics tab will be broken"
        echo "       cluster: $cluster_fp"
        echo "       vps:     $vps_fp"
        echo "       Fix: copy the VPS value into $HUGIN_ENV in place (do NOT"
        echo "       overwrite the /opt/munin/docker/.env symlink). See"
        echo "       shared/docs/MONITORING.md → 'Existing deploys: adding KB_GATE_TOKEN'."
    fi
    return 0
}

# The vLLM SLURM scripts live in the repo but run from $CLUSTER_SCRIPTS. When
# the deployed copy lags the repo, the drift is silent and only bites on the
# next restart: e.g. the launch script that boots misses the post-launch
# context-window assertion, or serves a different window than the code budgets
# against. This compares each repo script byte-for-byte against its deployed
# copy and WARNS on drift/absence. Always returns 0 — a stale script must never
# fail a retrieval deploy (vllm is a separate deploy target on its own cadence).
check_vllm_scripts_sync() {
    local drift=0 missing=0 checked=0 f
    for f in start-vllm-service.sh start-vllm-service-tp2.sh \
             schedule-vllm.sh check-context-window.sh; do
        local repo="$REPO_DIR/scripts/vllm/$f"
        local live="$CLUSTER_SCRIPTS/$f"
        [ -f "$repo" ] || continue
        checked=$((checked + 1))
        if [ ! -f "$live" ]; then
            missing=$((missing + 1))
            echo "[WARN] vLLM script not deployed: $live"
        elif ! cmp -s "$repo" "$live"; then
            drift=$((drift + 1))
            echo "[WARN] vLLM script drift: $live differs from repo"
        fi
    done
    if [ $drift -eq 0 ] && [ $missing -eq 0 ]; then
        echo "  [OK] vLLM scripts in sync ($checked matched against $CLUSTER_SCRIPTS)"
    else
        echo "       Redeploy to sync: sudo $REPO_DIR/deploy.sh vllm"
        echo "       (takes effect on the next: sudo vllm-service stop && start)"
    fi
    return 0
}

# Expected production pairing. Change these two together, and only when the
# live paper corpus really moves (an encoder migration), never to paper over
# a deploy that came up wrong.
EXPECTED_PAPER_ENCODER=${EXPECTED_PAPER_ENCODER:-bge-large}
EXPECTED_PAPERS_COLLECTION=${EXPECTED_PAPERS_COLLECTION:-papers_bge}

# Assert the running service embeds and searches in the same space, and that
# the space is the one production is supposed to be in. Reads the paper_space
# block /api/status now returns. Returns 1 on mismatch (fails the deploy).
check_paper_space() {
    local body="$1" fields
    fields=$(python3 - "$body" <<'PY' 2>/dev/null
import json, sys
sp = (json.loads(sys.argv[1]) or {}).get("paper_space")
# No block at all means the service predates the check: print nothing so the
# caller can tell "missing" apart from "present but wrong".
if sp:
    print("%s %s %s %s" % (sp.get("encoder", "?"), sp.get("collection", "?"),
                           sp.get("encoder_dim", "?"), sp.get("collection_dim", "?")))
PY
)
    if [ -z "$fields" ]; then
        echo "[WARN] /api/status has no paper_space block — retrieval predates"
        echo "       the encoder check; redeploy retrieval to enable it."
        return 0
    fi

    local enc coll enc_dim coll_dim
    read -r enc coll enc_dim coll_dim <<< "$fields"

    if [ "$enc_dim" = "None" ]; then
        echo "[FAIL] paper encoder $enc did not load — every paper search will"
        echo "       fail. Check: docker logs --tail 200 munin-retrieval"
        return 1
    fi

    if [ "$enc_dim" != "$coll_dim" ] && [ "$coll_dim" != "None" ]; then
        echo "[FAIL] paper space mismatch: encoder $enc emits ${enc_dim}d but"
        echo "       collection $coll stores ${coll_dim}d. PAPER_ENCODER and"
        echo "       PAPERS_COLLECTION must be set together in $HUGIN_ENV."
        return 1
    fi

    if [ "$enc" != "$EXPECTED_PAPER_ENCODER" ] || \
       [ "$coll" != "$EXPECTED_PAPERS_COLLECTION" ]; then
        echo "[FAIL] paper space is $enc/$coll, expected"
        echo "       $EXPECTED_PAPER_ENCODER/$EXPECTED_PAPERS_COLLECTION."
        echo "       If this is a deliberate rollback, re-run with:"
        echo "         EXPECTED_PAPER_ENCODER=$enc EXPECTED_PAPERS_COLLECTION=$coll \\"
        echo "           sudo $0 verify"
        return 1
    fi

    if [ "$coll_dim" = "None" ]; then
        echo "  [warn] paper space: $enc (${enc_dim}d), collection $coll not"
        echo "         readable yet (fresh cluster or Qdrant down)"
    else
        echo "  [OK] paper space — $enc (${enc_dim}d) / $coll (${coll_dim}d)"
    fi

    # The encoder should be a deployed artifact, not an HF pull at boot.
    if [ "$enc" = "bge-large" ] && [ ! -f "$BGE_LARGE_DIR/config.json" ]; then
        echo "[WARN] $BGE_LARGE_DIR is missing — the container is fetching"
        echo "       bge-large from HuggingFace on every start."
        echo "       Fix: sudo $0 models"
    fi
    return 0
}

# ------------------------------------------------------------------------------
# model: the production backbone, as one file
#   activate <slug> [--download] [--restart-vllm]
#   status
#   validate <slug>
# ------------------------------------------------------------------------------
model_profile_src() {
    # $1 = slug or path -> repo profile path, or fail loudly listing the slugs.
    local arg=$1
    if [ -f "$arg" ]; then echo "$arg"; return 0; fi
    if [ -f "$MODEL_PROFILES_SRC/$arg.env" ]; then echo "$MODEL_PROFILES_SRC/$arg.env"; return 0; fi
    echo "[ERROR] no profile '$arg'. Known: $(ls "$MODEL_PROFILES_SRC"/*.env | xargs -n1 basename | sed 's/\.env$//' | tr '\n' ' ')" >&2
    return 1
}

model_served_by() {
    # $1 = vLLM base URL -> served model id, or "offline".
    curl -sf --max-time 5 "$1/v1/models" 2>/dev/null \
        | python3 -c 'import json,sys; d=json.load(sys.stdin)["data"]; print(d[0]["id"] if d else "none")' 2>/dev/null \
        || echo "offline"
}

deploy_model_activate() {
    local slug=$1; shift
    local do_download=0 do_restart=0 arg
    for arg in "$@"; do
        case "$arg" in
            --download) do_download=1 ;;
            --restart-vllm) do_restart=1 ;;
            *) echo "[ERROR] model activate: unknown flag $arg"; exit 1 ;;
        esac
    done
    local src; src=$(model_profile_src "$slug") || exit 1
    # shellcheck disable=SC1090
    source "$MODEL_ENV_SH"
    echo "[model] Validating $src ..."
    munin_validate_model_env "$src" || exit 1
    # Load the CANDIDATE (not the active file) to learn its paths.
    munin_load_model_env "$src" || exit 1
    echo "[model] Candidate:"; munin_model_env_summary

    # 1. Checkpoint present, or downloadable on request.
    if [ ! -f "$MODEL_PATH/config.json" ]; then
        local excl="" g
        for g in $HF_EXCLUDE; do excl="$excl --exclude $g"; done
        if [ "$do_download" = "1" ]; then
            echo "[model] Downloading $MODEL_ID -> $MODEL_PATH ..."
            run "$VLLM_VENV/bin/hf download $MODEL_ID --local-dir $MODEL_PATH $excl"
        else
            echo "[ERROR] checkpoint missing: $MODEL_PATH/config.json"
            echo "        Download it first (or re-run with --download):"
            echo "        sudo $VLLM_VENV/bin/hf download $MODEL_ID --local-dir $MODEL_PATH $excl"
            exit 1
        fi
    fi
    # Parsers must exist in the pinned vLLM build; a wrong name breaks every
    # tool-using turn while plain chat keeps working (README step 14).
    local tp_dir="$VLLM_VENV/lib/python3.12/site-packages/vllm/tool_parsers"
    local rp_dir="$VLLM_VENV/lib/python3.12/site-packages/vllm/reasoning"
    if [ -d "$tp_dir" ] && ! grep -rqs "\"$VLLM_TOOL_PARSER\"" "$tp_dir"; then
        echo "[WARN] tool parser '$VLLM_TOOL_PARSER' not found by name in $tp_dir (check the registry before starting vLLM)"
    fi
    if [ -d "$rp_dir" ] && ! grep -rqs "\"$VLLM_REASONING_PARSER\"" "$rp_dir"; then
        echo "[WARN] reasoning parser '$VLLM_REASONING_PARSER' not found by name in $rp_dir"
    fi

    # 2. Install the profile set (so the active file has siblings) and write
    #    the active copy with provenance lines appended.
    run "install -d -m 0755 $MODEL_PROFILES_DST"
    run "install -m 0644 $src $MODEL_PROFILES_DST/$(basename "$src")"
    local prev="none"
    if [ -f "$ACTIVE_MODEL_ENV" ]; then
        prev=$(bash -c ". '$ACTIVE_MODEL_ENV'; printf '%s' \"\$MODEL_SLUG\"")
    fi
    if [ "$DRY_RUN" = "1" ]; then
        echo "  [dry-run] write $ACTIVE_MODEL_ENV from $src (previous: $prev)"
    else
        {
            cat "$src"
            echo ""
            echo "# --- written by deploy.sh model activate; do not edit, edit config/models/ ---"
            echo "ACTIVATED_AT=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
            echo "PREVIOUS_SLUG=$prev"
            echo "ACTIVATED_FROM=$src"
        } > "$ACTIVE_MODEL_ENV.tmp" && mv "$ACTIVE_MODEL_ENV.tmp" "$ACTIVE_MODEL_ENV"
        chmod 0644 "$ACTIVE_MODEL_ENV"
    fi
    echo "[OK] active profile -> $ACTIVE_MODEL_ENV (was: $prev)"

    # 3. Tokenizer + retrieval container on the new profile.
    MODEL_ENV_LOADED=0; load_model_env
    stage_tokenizer
    check_compose_project || exit 1
    run "install -m 0644 $REPO_DIR/docker/docker-compose.yml $MUNIN_DOCKER/docker-compose.yml"
    echo "[model] Recreating the retrieval container with the new profile..."
    run "cd $MUNIN_DOCKER && docker compose --profile rag up -d --force-recreate --no-deps retrieval"
    local i
    for i in $(seq 1 90); do
        curl -sf --max-time 2 http://127.0.0.1:8080/health > /dev/null 2>&1 && break
        [ "$DRY_RUN" = "1" ] && break
        sleep 2
    done
    local api_model
    api_model=$(curl -sf --max-time 5 http://127.0.0.1:8080/api/models 2>/dev/null \
        | python3 -c 'import json,sys; print(json.load(sys.stdin)["data"][0]["id"])' 2>/dev/null || echo "?")
    if [ "$api_model" = "$MODEL_NAME" ]; then
        echo "[OK] retrieval /api/models reports $api_model"
    elif [ "$DRY_RUN" != "1" ]; then
        echo "[FAIL] retrieval /api/models reports '$api_model', expected '$MODEL_NAME'"
        echo "       (old image without the route, or the container did not pick up the env)"
        exit 1
    fi

    # 4. vLLM: restart now, or say what still serves.
    local serving; serving=$(model_served_by http://127.0.0.1:8000)
    if [ "$do_restart" = "1" ]; then
        echo "[model] Restarting vLLM on the persisted profile ($(cat /opt/munin/logs/vllm_profile 2>/dev/null || echo single)) ..."
        run "vllm-service stop || true"
        # `start` refuses while the old job is still COMPLETING, and the dying
        # job keeps answering /health: wait for the queue to clear first.
        if [ "$DRY_RUN" != "1" ]; then
            for i in $(seq 1 60); do
                squeue -h -n vllm-service,vllm-service-tp2 -o %i | grep -q . || break; sleep 5
            done
        fi
        run "vllm-service start"
        echo "[model] Waiting for vLLM (up to 15 min: model load + JIT) ..."
        for i in $(seq 1 180); do
            [ "$DRY_RUN" = "1" ] && break
            serving=$(model_served_by http://127.0.0.1:8000)
            [ "$serving" = "$MODEL_NAME" ] && break
            sleep 5
        done
    fi
    if [ "$serving" = "$MODEL_NAME" ]; then
        echo "[OK] vLLM serves $serving"
        local job; job=$(cat /opt/munin/logs/current_job.txt 2>/dev/null || echo "")
        if [ -n "$job" ] && [ -f "/opt/munin/logs/vllm-service-$job.out" ]; then
            grep -h "GPU KV cache size\|Maximum concurrency\|Mxfp4 MoE backend" \
                "/opt/munin/logs/vllm-service-$job.out" | tail -3 | sed 's/^/       /'
        fi
        # A real ~40k-token completion against production: never in a dry run.
        if [ "$DRY_RUN" != "1" ] && [ -x "$CLUSTER_SCRIPTS/check-context-window.sh" ]; then
            "$CLUSTER_SCRIPTS/check-context-window.sh" "$VLLM_MAX_MODEL_LEN" || true
        fi
    elif [ "$serving" = "offline" ]; then
        echo "[NOTE] vLLM is offline; it starts on $MODEL_NAME at the next vllm-service start."
    else
        echo "[NOTE] vLLM still serves '$serving'. The retrieval container now expects"
        echo "       '$MODEL_NAME'; cut over with:  sudo vllm-service stop && sudo vllm-service start"
        echo "       (or re-run with --restart-vllm). Until then chat turns will 404 on the model name."
    fi
}

deploy_model_status() {
    # shellcheck disable=SC1090
    source "$MODEL_ENV_SH"
    echo "[model] Active profile:"
    if [ -f "$ACTIVE_MODEL_ENV" ]; then
        munin_load_model_env "$ACTIVE_MODEL_ENV" > /dev/null && munin_model_env_summary
        grep -E "^(ACTIVATED_AT|PREVIOUS_SLUG)=" "$ACTIVE_MODEL_ENV" | sed 's/^/  /'
    else
        echo "  (none) - assuming $DEFAULT_MODEL_SLUG; run: sudo ./deploy.sh model activate $DEFAULT_MODEL_SLUG"
        munin_load_model_env "$MODEL_PROFILES_SRC/$DEFAULT_MODEL_SLUG.env" > /dev/null
    fi
    echo "[model] Running:"
    echo "  vLLM :8000         serves $(model_served_by http://127.0.0.1:8000)   (profile file: $(cat /opt/munin/logs/vllm_profile 2>/dev/null || echo single), job $(cat /opt/munin/logs/current_job.txt 2>/dev/null || echo none))"
    local api; api=$(curl -sf --max-time 5 http://127.0.0.1:8080/api/models 2>/dev/null || echo "")
    if [ -n "$api" ]; then
        echo "  retrieval :8080    $(printf '%s' "$api" | python3 -c 'import json,sys; d=json.load(sys.stdin)["data"][0]; print(d["id"], "| served:", d.get("served"), "| thinking:", d.get("thinking_mode"), "| effort:", d.get("reasoning_effort"))' 2>/dev/null)"
    else
        echo "  retrieval :8080    no /api/models (container down or pre-2026-09-15 image)"
    fi
    if [ -f "$TOKENIZER_HOST_DIR/tokenizer.json" ] && [ -f "$MODEL_PATH/tokenizer.json" ]; then
        local a b
        a=$(sha256sum "$TOKENIZER_HOST_DIR/tokenizer.json" | cut -c1-12)
        b=$(sha256sum "$MODEL_PATH/tokenizer.json" | cut -c1-12)
        if [ "$a" = "$b" ]; then echo "  tokenizer          staged copy matches checkpoint ($a)"
        else echo "  tokenizer          MISMATCH: staged $a vs checkpoint $b  -> sudo ./deploy.sh model activate $MODEL_SLUG"; fi
    else
        echo "  tokenizer          not staged (or checkpoint missing)"
    fi
    echo "[model] Profiles: $(ls "$MODEL_PROFILES_SRC"/*.env | xargs -n1 basename | sed 's/\.env$//' | tr '\n' ' ')"
}

deploy_model() {
    local sub=${MODEL_ARGS[0]:-}
    case "$sub" in
        activate)
            [ -n "${MODEL_ARGS[1]:-}" ] || { echo "Usage: sudo $0 model activate <slug> [--download] [--restart-vllm]"; exit 1; }
            deploy_model_activate "${MODEL_ARGS[@]:1}" ;;
        status)   deploy_model_status ;;
        validate)
            [ -n "${MODEL_ARGS[1]:-}" ] || { echo "Usage: $0 model validate <slug>"; exit 1; }
            # shellcheck disable=SC1090
            source "$MODEL_ENV_SH"
            local f; f=$(model_profile_src "${MODEL_ARGS[1]}") || exit 1
            munin_validate_model_env "$f" && munin_load_model_env "$f" && munin_model_env_summary && echo "[OK] $f is valid" ;;
        *) echo "Usage: sudo $0 model {activate <slug> [--download] [--restart-vllm] | status | validate <slug>}"; exit 1 ;;
    esac
}

# ------------------------------------------------------------------------------
# instance: a second backbone beside production (eval only)
#
# State per instance under $INSTANCES_DIR/<name>/: profile, vllm_port, api_port,
# job_id (or vllm_of when it shares another instance's vLLM), compose.yml (the
# rendered override), gates.json. The container is munin-retrieval-<name>, an
# `extends` of the production retrieval service that differs in exactly: name,
# port, VLLM_URL, the model variables, the tokenizer mount and, with
# --corpus shadow, the two collection names. Verified by `instance gates`.
# ------------------------------------------------------------------------------
_yaml_sq() { printf "'%s'" "$(printf '%s' "$1" | sed "s/'/''/g")"; }

instance_render_compose() {
    # $1 name, $2 api_port, $3 vllm_port, $4 tokenizer_dir, $5 corpus (live|shadow), $6 out
    local name=$1 api_port=$2 vllm_port=$3 tok=$4 corpus=$5 out=$6
    {
        echo "# Rendered by deploy.sh instance up on $(date -u +%Y-%m-%dT%H:%M:%SZ). Do not edit; re-run instance up."
        echo "# extends the production retrieval service; only the keys below differ."
        echo "services:"
        echo "  retrieval-$name:"
        echo "    extends:"
        echo "      file: docker-compose.yml"
        echo "      service: retrieval"
        echo "    container_name: munin-retrieval-$name"
        echo "    build: !reset null"
        echo "    restart: \"no\""
        echo "    ports: !override"
        echo "      - \"127.0.0.1:$api_port:8080\""
        echo "    volumes:"
        echo "      - $tok:/models/qwen:ro"
        echo "    environment:"
        echo "      - VLLM_URL=http://host.docker.internal:$vllm_port"
        echo "      - $(_yaml_sq "VLLM_MODEL_NAME=$MODEL_NAME")"
        echo "      - $(_yaml_sq "LLM_THINKING_MODE=$LLM_THINKING_MODE")"
        echo "      - $(_yaml_sq "LLM_REASONING_EFFORT=$LLM_REASONING_EFFORT")"
        echo "      - $(_yaml_sq "SAMPLING_DEFAULT=$SAMPLING_DEFAULT")"
        echo "      - $(_yaml_sq "SAMPLING_CODE=$SAMPLING_CODE")"
        echo "      - MUNIN_INSTANCE=$name"
        if [ "$corpus" = "shadow" ]; then
            echo "      - PAPERS_COLLECTION=papers_shadow"
            echo "      - CHUNKS_COLLECTION=papers_chunks_shadow"
        fi
        echo "    profiles:"
        echo "      - rag"
    } > "$out"
}

instance_compose() {
    # docker compose invocation for one instance's service, from the production
    # project dir so the .env symlink (cluster.env secrets) applies. Never touches
    # other services (--no-deps at the call sites).
    local name=$1; shift
    (cd "$MUNIN_DOCKER" && docker compose -p munin --profile rag \
        -f docker-compose.yml -f "$INSTANCES_DIR/$name/compose.yml" "$@")
}

deploy_instance_up() {
    local slug=$1; shift
    local name="" vllm_port=8001 api_port=8082 corpus=live vllm_of="" gpu_util="" do_download=0
    while [ $# -gt 0 ]; do
        case "$1" in
            --name) name=$2; shift 2 ;;
            --vllm-port) vllm_port=$2; shift 2 ;;
            --api-port) api_port=$2; shift 2 ;;
            --corpus) corpus=$2; shift 2 ;;
            --vllm) vllm_of=$2; shift 2 ;;
            --gpu-util) gpu_util=$2; shift 2 ;;
            --download) do_download=1; shift ;;
            *) echo "[ERROR] instance up: unknown flag $1"; exit 1 ;;
        esac
    done
    [ -n "$name" ] || { echo "[ERROR] instance up needs --name <n>"; exit 1; }
    case "$name" in *[!a-z0-9-]*) echo "[ERROR] --name: lowercase letters, digits, dashes"; exit 1 ;; esac
    case "$corpus" in live|shadow) ;; *) echo "[ERROR] --corpus live|shadow"; exit 1 ;; esac
    if [ "$name" = "prod" ] || [ "$api_port" = "8080" ] || [ "$vllm_port" = "8000" ]; then
        echo "[ERROR] :8080 / :8000 / 'prod' are production; an instance never takes them"; exit 1
    fi
    local src; src=$(model_profile_src "$slug") || exit 1
    if [ "$DRY_RUN" != "1" ]; then
        need_file "$INSTANCE_SCRIPT"; need_file "$GATES_PY"   # installed by deploy.sh vllm
    fi
    # shellcheck disable=SC1090
    source "$MODEL_ENV_SH"
    munin_validate_model_env "$src" || exit 1
    TOKENIZER_HOST_DIR=""; munin_load_model_env "$src" || exit 1
    local tok_dir="$MUNIN_ROOT/data/models/tokenizer-$MODEL_SLUG"
    local state="$INSTANCES_DIR/$name"
    # A dry run renders into a scratch dir so nothing under /opt/munin is touched.
    [ "$DRY_RUN" = "1" ] && state=$(mktemp -d) && echo "  [dry-run] state dir -> $state"
    echo "[instance] '$name' <- $MODEL_SLUG ($MODEL_NAME) vllm=:$vllm_port api=:$api_port corpus=$corpus"
    munin_model_env_summary | sed 's/^/  /'

    # checkpoint
    if [ ! -f "$MODEL_PATH/config.json" ]; then
        local excl="" g; for g in $HF_EXCLUDE; do excl="$excl --exclude $g"; done
        if [ "$do_download" = "1" ]; then
            run "$VLLM_VENV/bin/hf download $MODEL_ID --local-dir $MODEL_PATH $excl"
        else
            echo "[ERROR] checkpoint missing: $MODEL_PATH (re-run with --download)"; exit 1
        fi
    fi
    if [ "$corpus" = "shadow" ]; then
        local n
        for c in papers_shadow papers_chunks_shadow; do
            n=$(curl -sf "http://127.0.0.1:6333/collections/$c" 2>/dev/null \
                | python3 -c 'import json,sys; print(json.load(sys.stdin)["result"]["points_count"])' 2>/dev/null || echo "")
            if [ -z "$n" ] || [ "$n" = "0" ]; then
                echo "[ERROR] shadow corpus: collection $c missing or empty. Build both first"
                echo "        (snapshot-recover + removed_dois delete; RESULTS.md 2026-09-15 recipe)."
                exit 1
            fi
            echo "  shadow collection $c: $n points"
        done
    fi

    run "install -d -m 0755 $state $INSTANCES_DIR"
    run "install -d -m 0755 $tok_dir"
    # tokenizer for THIS model, never production's dir
    for f in tokenizer.json tokenizer_config.json special_tokens_map.json; do
        [ -f "$MODEL_PATH/$f" ] && run "install -m 0644 $MODEL_PATH/$f $tok_dir/$f"
    done
    echo "[OK] tokenizer -> $tok_dir ($(sha256sum "$MODEL_PATH/tokenizer.json" | cut -c1-12))"

    # vLLM: own job, or another instance's
    local job_out=""
    if [ -n "$vllm_of" ]; then
        [ -f "$INSTANCES_DIR/$vllm_of/vllm_port" ] || { echo "[ERROR] no instance '$vllm_of' to share vLLM with"; exit 1; }
        vllm_port=$(cat "$INSTANCES_DIR/$vllm_of/vllm_port")
        [ "$(cat "$INSTANCES_DIR/$vllm_of/model_name" 2>/dev/null)" = "$MODEL_NAME" ] \
            || { echo "[ERROR] instance '$vllm_of' serves $(cat "$INSTANCES_DIR/$vllm_of/model_name"), not $MODEL_NAME"; exit 1; }
        run "echo $vllm_of > $state/vllm_of"
        echo "[instance] sharing vLLM of '$vllm_of' on :$vllm_port"
    else
        if curl -sf --max-time 3 "http://127.0.0.1:$vllm_port/health" > /dev/null 2>&1; then
            echo "[ERROR] something already answers on :$vllm_port"; exit 1
        fi
        local jobname="vllm-inst-$name"
        if squeue -h -n "$jobname" -o %i | grep -q .; then
            echo "[ERROR] SLURM job $jobname already exists: $(squeue -h -n "$jobname" -o '%i %T')"; exit 1
        fi
        echo "[instance] sbatch $INSTANCE_SCRIPT as $jobname (gpu:batch:1) ..."
        local export_vars="ALL,MUNIN_MODEL_PROFILE=$MODEL_PROFILES_DST/$MODEL_SLUG.env,INSTANCE_NAME=$name,INSTANCE_PORT=$vllm_port,INSTANCE_STATE_DIR=$state,TOKENIZER_HOST_DIR=$tok_dir"
        [ -n "$gpu_util" ] && export_vars="$export_vars,INSTANCE_GPU_UTIL=$gpu_util"
        run "install -m 0644 $src $MODEL_PROFILES_DST/$MODEL_SLUG.env"
        if [ "$DRY_RUN" = "1" ]; then
            echo "  [dry-run] sbatch --job-name=$jobname --export=$export_vars $INSTANCE_SCRIPT"
        else
            local jid
            jid=$(sbatch --parsable --job-name="$jobname" --export="$export_vars" "$INSTANCE_SCRIPT") \
                || { echo "[ERROR] sbatch failed"; exit 1; }
            echo "$jid" > "$state/job_id"
            job_out="/opt/munin/logs/vllm-inst-$jobname-$jid.out"
            echo "[instance] job $jid submitted; log $job_out"
            echo "[instance] waiting for vLLM on :$vllm_port (up to 20 min) ..."
            local i
            for i in $(seq 1 240); do
                if curl -sf --max-time 3 "http://127.0.0.1:$vllm_port/health" > /dev/null 2>&1; then break; fi
                if ! squeue -h -j "$jid" -o %T | grep -q .; then
                    echo "[ERROR] job $jid left the queue before vLLM answered; see $job_out"
                    tail -30 "$job_out" 2>/dev/null; exit 1
                fi
                sleep 5
            done
            curl -sf --max-time 3 "http://127.0.0.1:$vllm_port/health" > /dev/null 2>&1 \
                || { echo "[ERROR] vLLM not healthy after 20 min; see $job_out"; exit 1; }
        fi
    fi
    echo "$vllm_port" > "$state/vllm_port"; echo "$api_port" > "$state/api_port"
    echo "$MODEL_NAME" > "$state/model_name"; echo "$MODEL_PROFILES_DST/$MODEL_SLUG.env" > "$state/profile"
    echo "$corpus" > "$state/corpus"; echo "$tok_dir" > "$state/tokenizer_dir"
    [ -n "$job_out" ] && echo "$job_out" > "$state/job_out"

    # retrieval container
    instance_render_compose "$name" "$api_port" "$vllm_port" "$tok_dir" "$corpus" "$state/compose.yml"
    check_compose_project || exit 1
    echo "[instance] docker compose up retrieval-$name (:$api_port) ..."
    if [ "$DRY_RUN" = "1" ]; then
        echo "  [dry-run] compose up -d --no-deps retrieval-$name with $state/compose.yml"; cat "$state/compose.yml"
    else
        instance_compose "$name" up -d --no-deps "retrieval-$name" || exit 1
        local i
        for i in $(seq 1 90); do
            curl -sf --max-time 2 "http://127.0.0.1:$api_port/health" > /dev/null 2>&1 && break; sleep 2
        done
    fi
    if [ "$DRY_RUN" = "1" ]; then
        echo "  [dry-run] would run: deploy.sh instance gates $name"; rm -rf "$state"; return 0
    fi
    deploy_instance_gates "$name"
}

deploy_instance_gates() {
    local name=$1
    local state="$INSTANCES_DIR/$name"   # two lines: `local a=$1 b="$a"` expands b before a is set
    [ -f "$state/profile" ] || { echo "[ERROR] no instance '$name'"; exit 1; }
    # shellcheck disable=SC1090
    source "$MODEL_ENV_SH"
    TOKENIZER_HOST_DIR=$(cat "$state/tokenizer_dir"); munin_load_model_env "$(cat "$state/profile")" || exit 1
    local vllm_port api_port job_out=""
    vllm_port=$(cat "$state/vllm_port"); api_port=$(cat "$state/api_port")
    [ -f "$state/job_out" ] && job_out=$(cat "$state/job_out")
    [ -z "$job_out" ] && [ -f "$state/vllm_of" ] && [ -f "$INSTANCES_DIR/$(cat "$state/vllm_of")/job_out" ] \
        && job_out=$(cat "$INSTANCES_DIR/$(cat "$state/vllm_of")/job_out")
    # compose diff: exactly the intended keys
    echo "[instance] compose diff vs production (env keys that differ):"
    local diffkeys
    diffkeys=$(instance_compose "$name" config 2>/dev/null | python3 -c '
import sys, yaml
name = sys.argv[1]
d = yaml.safe_load(sys.stdin)["services"]
def env(svc):
    e = d[svc].get("environment") or {}
    return e if isinstance(e, dict) else dict(kv.split("=", 1) for kv in e)
p, i = env("retrieval"), env("retrieval-" + name)
keys = sorted(k for k in set(p) | set(i) if p.get(k) != i.get(k))
print(" ".join(keys))
pv = {m["target"]: m["source"] for m in d["retrieval"].get("volumes", [])}
iv = {m["target"]: m["source"] for m in d["retrieval-" + name].get("volumes", [])}
print("MOUNTS:" + " ".join(sorted(t for t in set(pv) | set(iv) if pv.get(t) != iv.get(t))))
' "$name" 2>/dev/null) || diffkeys="(compose config failed)"
    echo "  $diffkeys"
    local allowed="VLLM_URL VLLM_MODEL_NAME LLM_THINKING_MODE LLM_REASONING_EFFORT SAMPLING_DEFAULT SAMPLING_CODE MUNIN_INSTANCE PAPERS_COLLECTION CHUNKS_COLLECTION"
    local k bad=""
    for k in $(printf '%s' "$diffkeys" | head -1); do
        case " $allowed " in *" $k "*) ;; *) bad="$bad $k" ;; esac
    done
    if [ -n "$bad" ]; then echo "[FAIL] unexpected env differences:$bad"; [ "$DRY_RUN" = "1" ] || exit 1; fi
    [ "$DRY_RUN" = "1" ] && return 0
    python3 "$GATES_PY" --vllm "http://127.0.0.1:$vllm_port" --api "http://127.0.0.1:$api_port" \
        --model "$MODEL_NAME" --thinking-mode "$LLM_THINKING_MODE" --reasoning-effort "$LLM_REASONING_EFFORT" \
        --max-num-seqs "${INSTANCE_MAX_NUM_SEQS:-$VLLM_MAX_NUM_SEQS_SINGLE}" --max-model-len "$VLLM_MAX_MODEL_LEN" \
        ${job_out:+--job-out "$job_out"} --tokenizer "$TOKENIZER_HOST_DIR/tokenizer.json" --checkpoint "$MODEL_PATH" \
        --out "$state/gates.json" || { echo "[FAIL] gates failed for instance '$name' (see $state/gates.json)"; exit 1; }
    echo "[OK] instance '$name' is up and gated: vLLM :$vllm_port, API :$api_port, $MODEL_NAME"
}

deploy_instance_refresh() {
    # Recreate an instance's retrieval container (new image or profile) without
    # touching its vLLM job, then re-run the gates.
    local name=$1
    local state="$INSTANCES_DIR/$name"
    [ -f "$state/profile" ] || { echo "[ERROR] no instance '$name'"; exit 1; }
    # shellcheck disable=SC1090
    source "$MODEL_ENV_SH"
    TOKENIZER_HOST_DIR=$(cat "$state/tokenizer_dir"); munin_load_model_env "$(cat "$state/profile")" || exit 1
    instance_render_compose "$name" "$(cat "$state/api_port")" "$(cat "$state/vllm_port")" \
        "$(cat "$state/tokenizer_dir")" "$(cat "$state/corpus")" "$state/compose.yml"
    check_compose_project || exit 1
    echo "[instance] recreating retrieval-$name on the current image ..."
    run "install -m 0644 $REPO_DIR/docker/docker-compose.yml $MUNIN_DOCKER/docker-compose.yml"
    instance_compose "$name" up -d --force-recreate --no-deps "retrieval-$name" || exit 1
    local i
    for i in $(seq 1 90); do
        curl -sf --max-time 2 "http://127.0.0.1:$(cat "$state/api_port")/health" > /dev/null 2>&1 && break; sleep 2
    done
    deploy_instance_gates "$name"
}

deploy_instance_down() {
    local name=$1
    local state="$INSTANCES_DIR/$name"   # two lines: `local a=$1 b="$a"` expands b before a is set
    [ -d "$state" ] || { echo "[ERROR] no instance '$name'"; exit 1; }
    echo "[instance] down '$name' ..."
    run "docker rm -f munin-retrieval-$name 2>/dev/null || true"
    if [ -f "$state/job_id" ]; then
        local jid; jid=$(cat "$state/job_id")
        # refuse if another instance still shares this vLLM
        local other
        for other in "$INSTANCES_DIR"/*/vllm_of; do
            [ -f "$other" ] && [ "$(cat "$other")" = "$name" ] && [ -f "$(dirname "$other")/api_port" ] \
                && docker ps --format '{{.Names}}' | grep -q "^munin-retrieval-$(basename "$(dirname "$other")")$" \
                && { echo "[ERROR] instance '$(basename "$(dirname "$other")")' still uses this vLLM; down it first"; exit 1; }
        done
        run "scancel $jid || true"
        run "rm -f $state/job_id"
    fi
    run "echo offline > $state/status"
    echo "[OK] instance '$name' down (state kept in $state; remove the dir to forget it)"
}

deploy_instance_ls() {
    [ -d "$INSTANCES_DIR" ] || { echo "(no instances)"; return 0; }
    printf "%-12s %-16s %-6s %-6s %-8s %-10s %-10s %s\n" NAME MODEL VLLM API CORPUS JOB CONTAINER STATUS
    local d name jid jstate cstate
    for d in "$INSTANCES_DIR"/*/; do
        [ -f "$d/profile" ] || continue
        name=$(basename "$d")
        jid=$(cat "$d/job_id" 2>/dev/null || cat "$d/vllm_of" 2>/dev/null | sed 's/^/via:/')
        jstate=""; [ -n "$jid" ] && [ "${jid#via:}" = "$jid" ] && jstate=$(squeue -h -j "$jid" -o %T 2>/dev/null)
        cstate=$(docker inspect -f '{{.State.Health.Status}}' "munin-retrieval-$name" 2>/dev/null || echo "absent")
        printf "%-12s %-16s %-6s %-6s %-8s %-10s %-10s %s\n" "$name" "$(cat "$d/model_name")" \
            "$(cat "$d/vllm_port")" "$(cat "$d/api_port")" "$(cat "$d/corpus" 2>/dev/null)" \
            "${jid:-none}${jstate:+ ($jstate)}" "$cstate" "$(cat "$d/status" 2>/dev/null || echo "")"
    done
}

deploy_instance() {
    local sub=${MODEL_ARGS[0]:-}
    case "$sub" in
        up)
            [ -n "${MODEL_ARGS[1]:-}" ] || { echo "Usage: sudo $0 instance up <slug> --name <n> [flags]"; exit 1; }
            deploy_instance_up "${MODEL_ARGS[@]:1}" ;;
        down)  [ -n "${MODEL_ARGS[1]:-}" ] || { echo "Usage: sudo $0 instance down <n>"; exit 1; }
               deploy_instance_down "${MODEL_ARGS[1]}" ;;
        gates) [ -n "${MODEL_ARGS[1]:-}" ] || { echo "Usage: sudo $0 instance gates <n>"; exit 1; }
               deploy_instance_gates "${MODEL_ARGS[1]}" ;;
        refresh) [ -n "${MODEL_ARGS[1]:-}" ] || { echo "Usage: sudo $0 instance refresh <n>"; exit 1; }
               deploy_instance_refresh "${MODEL_ARGS[1]}" ;;
        logs)  [ -n "${MODEL_ARGS[1]:-}" ] || { echo "Usage: sudo $0 instance logs <n> [docker logs args]"; exit 1; }
               docker logs "${MODEL_ARGS[@]:2}" "munin-retrieval-${MODEL_ARGS[1]}" ;;
        ls)    deploy_instance_ls ;;
        *) echo "Usage: sudo $0 instance {up <slug> --name <n> [--vllm-port P] [--api-port P] [--corpus live|shadow] [--vllm <n>] [--gpu-util U] [--download] | refresh <n> | down <n> | gates <n> | logs <n> | ls}"; exit 1 ;;
    esac
}

# ------------------------------------------------------------------------------
# sudoers: let the operator account drive the backbone lifecycle unattended
# ------------------------------------------------------------------------------
deploy_sudoers() {
    local operator=${MUNIN_OPERATOR:-${SUDO_USER:-}}
    if [ -z "$operator" ] || [ "$operator" = "root" ]; then
        echo "[ERROR] set MUNIN_OPERATOR=<login> (the account that runs the benchmark driver)"
        exit 1
    fi
    id "$operator" > /dev/null 2>&1 || { echo "[ERROR] no such user: $operator"; exit 1; }
    need_file "$REPO_DIR/config/sudoers.d/munin-operator.template"
    local tmp; tmp=$(mktemp)
    sed -e "s#@REPO_DIR@#$REPO_DIR#g" -e "s#@OPERATOR@#$operator#g" \
        "$REPO_DIR/config/sudoers.d/munin-operator.template" > "$tmp"
    echo "[sudoers] Rendered for operator '$operator':"
    grep -v '^#' "$tmp" | sed 's/^/    /'
    if ! visudo -cf "$tmp" > /dev/null; then
        echo "[ERROR] rendered sudoers file does not validate; nothing installed"; rm -f "$tmp"; exit 1
    fi
    run "install -m 0440 -o root -g root $tmp /etc/sudoers.d/munin-operator"
    rm -f "$tmp"
    echo "[OK] sudoers -> /etc/sudoers.d/munin-operator (remove with: sudo rm /etc/sudoers.d/munin-operator)"
    echo "     Test as $operator:  sudo -n $REPO_DIR/deploy.sh model status"
}

deploy_verify() {
    echo "[verify] Smoke-testing retrieval endpoints..."

    if [ "$DRY_RUN" = "1" ]; then
        echo "  [dry-run] would poll $RETRIEVAL_BASE/health until 200, then curl /api/status + /api/personas"
        echo "  [dry-run] would assert paper_space == $EXPECTED_PAPER_ENCODER/$EXPECTED_PAPERS_COLLECTION with matching dims"
        echo "  [dry-run] would probe /api/research/jobs for the deep research router"
        echo "  [dry-run] would compare KB_GATE_TOKEN fingerprint against the VPS (METRICS_VPS_SSH)"
        echo "  [dry-run] would compare deployed vLLM scripts in $CLUSTER_SCRIPTS against the repo"
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

    # 2b. Paper vector space. The encoder and the collection are a pair, and
    #     the pairing lives in cluster.env, not in git — so a host that lost
    #     (or never had) those keys used to come up quietly on the retired
    #     SPECTER stack. Retrieval refuses to boot on a dimension mismatch;
    #     this additionally FAILS the deploy when the live pairing is not the
    #     expected production one, so a rollback can never be silent.
    check_paper_space "$status_body" || return 1

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

    # 4. /api/research/jobs — the in-process Deep Research router must be
    #    mounted. Cheap to break (it is included by one line in main.py) and
    #    invisible until a user starts a report, so probe it here.
    local research_code
    research_code=$(curl -s -o /dev/null -w '%{http_code}' -m 5 \
        -H "X-Munin-Email: $VERIFY_EMAIL" "$RETRIEVAL_BASE/api/research/jobs" \
        2>/dev/null || true)
    if [ "$research_code" = "200" ]; then
        echo "  [OK] /api/research/jobs — deep research router mounted"
    else
        echo "[FAIL] /api/research/jobs returned $research_code (expected 200)"
        echo "       The in-chat Deep Research feature is down."
        return 1
    fi

    # 5. KB_GATE_TOKEN must match the VPS or the admin Metrics tab breaks.
    #    Warning-only: never fails the deploy.
    check_kb_token_sync

    # 6. Deployed vLLM scripts must match the repo, or restarts run stale
    #    launch scripts. Warning-only: never fails the deploy.
    check_vllm_scripts_sync

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
    models)       deploy_models ;;
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
    model)        deploy_model ;;
    sudoers)      deploy_sudoers ;;
    instance)     deploy_instance ;;
    all)
        deploy_dirs
        deploy_compose
        deploy_personas
        deploy_agents
        deploy_models        # bge-large must exist before retrieval starts,
                             # else the container pulls it from HuggingFace
        deploy_vllm
        deploy_maintenance
        # deploy_deepresearch is NOT part of `all`: it provisions the retired
        # MiroThinker path (disabled 2026-07). Run it explicitly if you are
        # reviving that feature. The Deep Research in chat needs nothing here.
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
        echo "Modes: all dirs compose personas agents models vllm maintenance deepresearch tunnel knowledge pipeline retrieval sandbox searxng monitoring verify"
        exit 1
        ;;
esac

echo ""
echo "=============================================="
[ "$DRY_RUN" = "1" ] && echo "Dry run complete — no changes were made." || echo "Done."
echo "=============================================="
