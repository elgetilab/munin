#!/bin/bash
# ==============================================================================
# VLLM SERVICE SCHEDULER
# ==============================================================================
# Starts/stops the vLLM service as a SLURM job
# Called by cron:
#   - 6:00 AM: start
#   - 2:00 AM: stop (frees GPU for batch jobs)
#
# Usage: ./schedule-vllm.sh [start [single|tp2]|stop|status|enable-24x7|disable-24x7]
#
# PROFILES (added 2026-08-25). Two SBATCH scripts serve the same model:
#   single  start-vllm-service.sh      1 GPU,  64k, --max-num-seqs 2
#   tp2     start-vllm-service-tp2.sh  2 GPUs, 64k, --max-num-seqs 8
# `start tp2` PERSISTS the choice, so the 6am cron brings the same profile back
# up rather than silently reverting. Before this existed the scheduler matched
# only the job name "vllm-service" while the TP=2 script is named
# "vllm-service-tp2", so a hand-started TP=2 job was INVISIBLE to it: the 2am
# stop found nothing and left both GPUs held all night (blocking every batch
# job), and the 6am start then queued a single-GPU job behind it that took over
# at 64k/2 seqs whenever the TP=2 job finally hit its walltime.
# ==============================================================================

ACTION=${1:-status}
LOG_FILE="/var/log/cluster-admin/vllm-schedule.log"
TIMESTAMP=$(date '+%Y-%m-%d %H:%M:%S')
# BOTH job names, or a TP=2 job is invisible to stop/status/start. squeue -n
# takes a comma-separated list.
VLLM_JOB_NAMES="vllm-service,vllm-service-tp2"
VLLM_USER="root"  # Or create dedicated llm-service user
SCRIPT_DIR="/opt/cluster/scripts/llm"
# Which profile the 6am cron should bring back. Survives reboots; defaults to
# `single` when absent so a cluster that never opted in behaves exactly as before.
PROFILE_FILE="/opt/munin/logs/vllm_profile"
CRON_FILE="/etc/cron.d/hugin-cluster"

# Ensure log directory exists
mkdir -p /var/log/cluster-admin

log() {
    echo "$TIMESTAMP: $1" | tee -a "$LOG_FILE"
}

get_vllm_job_id() {
    squeue -n "$VLLM_JOB_NAMES" -h -o "%i" 2>/dev/null | head -1
}

get_vllm_job_name() {
    squeue -n "$VLLM_JOB_NAMES" -h -o "%j" 2>/dev/null | head -1
}

get_profile() {
    local p
    p=$(cat "$PROFILE_FILE" 2>/dev/null | tr -d '[:space:]')
    case "$p" in
        tp2|single) echo "$p" ;;
        *)          echo "single" ;;
    esac
}

set_profile() {
    mkdir -p "$(dirname "$PROFILE_FILE")" 2>/dev/null
    echo "$1" > "$PROFILE_FILE"
}

script_for_profile() {
    case "$1" in
        tp2) echo "$SCRIPT_DIR/start-vllm-service-tp2.sh" ;;
        *)   echo "$SCRIPT_DIR/start-vllm-service.sh" ;;
    esac
}

is_24x7() {
    # 24x7 mode is active when the cron stop line is commented out
    grep -q '^#.*schedule-vllm\.sh stop' "$CRON_FILE" 2>/dev/null
}

case "$ACTION" in
    start)
        # `start tp2` / `start single` sets the profile AND persists it; a bare
        # `start` (what cron runs) reuses whatever was last chosen.
        case "${2:-}" in
            tp2|single) set_profile "$2" ;;
            "")         ;;
            *)          echo "Unknown profile '${2}'. Use: single | tp2"; exit 1 ;;
        esac
        PROFILE=$(get_profile)
        VLLM_SCRIPT=$(script_for_profile "$PROFILE")
        log "Starting vLLM service (profile: $PROFILE)..."

        # Check if already running (EITHER profile)
        JOB_ID=$(get_vllm_job_id)
        if [ -n "$JOB_ID" ]; then
            log "vLLM service already running (Job ID: $JOB_ID, $(get_vllm_job_name))"
            exit 0
        fi

        # Check if vLLM script exists
        if [ ! -f "$VLLM_SCRIPT" ]; then
            log "ERROR: vLLM script not found: $VLLM_SCRIPT"
            log "Phase 2 (Munin) may not be installed yet"
            exit 1
        fi

        # Submit job
        JOB_ID=$(sbatch --parsable "$VLLM_SCRIPT" 2>/dev/null)
        if [ -n "$JOB_ID" ]; then
            log "vLLM service started (Job ID: $JOB_ID)"
        else
            log "ERROR: Failed to start vLLM service"
            exit 1
        fi
        ;;

    stop)
        log "Stopping vLLM service..."

        JOB_ID=$(get_vllm_job_id)
        if [ -z "$JOB_ID" ]; then
            log "vLLM service is not running"
            exit 0
        fi

        # Cancel job
        scancel "$JOB_ID" 2>/dev/null
        log "vLLM service stopped (Job ID: $JOB_ID)"
        ;;

    status)
        JOB_ID=$(get_vllm_job_id)
        if [ -n "$JOB_ID" ]; then
            echo "vLLM service is RUNNING (Job ID: $JOB_ID, job: $(get_vllm_job_name))"
            squeue -j "$JOB_ID" 2>/dev/null
        else
            echo "vLLM service is NOT RUNNING"
        fi
        echo "Profile on next start: $(get_profile)  (from $PROFILE_FILE)"
        if is_24x7; then
            echo "Mode: 24/7 (cron scheduling disabled)"
        else
            echo "Mode: scheduled (6am - 2am)"
        fi
        ;;

    enable-24x7)
        log "Enabling 24/7 mode..."

        if [ ! -f "$CRON_FILE" ]; then
            log "ERROR: Cron file not found: $CRON_FILE"
            exit 1
        fi

        # Comment out the vLLM start/stop cron lines
        sed -i 's|^\(0 6 .*schedule-vllm\.sh start\)|#\1|' "$CRON_FILE"
        sed -i 's|^\(0 2 .*schedule-vllm\.sh stop\)|#\1|' "$CRON_FILE"

        log "24/7 mode enabled — cron start/stop disabled"

        # Start if not already running
        JOB_ID=$(get_vllm_job_id)
        if [ -z "$JOB_ID" ]; then
            log "vLLM not running, starting now..."
            exec "$0" start
        else
            log "vLLM already running (Job ID: $JOB_ID)"
        fi
        ;;

    disable-24x7)
        log "Disabling 24/7 mode..."

        if [ ! -f "$CRON_FILE" ]; then
            log "ERROR: Cron file not found: $CRON_FILE"
            exit 1
        fi

        # Uncomment the vLLM start/stop cron lines
        sed -i 's|^#\(0 6 .*schedule-vllm\.sh start\)|\1|' "$CRON_FILE"
        sed -i 's|^#\(0 2 .*schedule-vllm\.sh stop\)|\1|' "$CRON_FILE"

        log "24/7 mode disabled — cron scheduling restored (6am - 2am)"
        ;;

    *)
        echo "Usage: $0 [start [single|tp2]|stop|status|enable-24x7|disable-24x7]"
        exit 1
        ;;
esac
