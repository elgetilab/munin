#!/bin/bash
# ==============================================================================
# VLLM SERVICE SCHEDULER
# ==============================================================================
# Starts/stops the vLLM service as a SLURM job
# Called by cron:
#   - 6:00 AM: start
#   - 2:00 AM: stop (frees GPU for batch jobs)
#
# Usage: ./schedule-vllm.sh [start|stop|status|enable-24x7|disable-24x7]
# ==============================================================================

ACTION=${1:-status}
LOG_FILE="/var/log/cluster-admin/vllm-schedule.log"
TIMESTAMP=$(date '+%Y-%m-%d %H:%M:%S')
VLLM_JOB_NAME="vllm-service"
VLLM_USER="root"  # Or create dedicated llm-service user
VLLM_SCRIPT="/opt/cluster/scripts/llm/start-vllm-service.sh"
CRON_FILE="/etc/cron.d/hugin-cluster"

# Ensure log directory exists
mkdir -p /var/log/cluster-admin

log() {
    echo "$TIMESTAMP: $1" | tee -a "$LOG_FILE"
}

get_vllm_job_id() {
    squeue -n "$VLLM_JOB_NAME" -h -o "%i" 2>/dev/null | head -1
}

is_24x7() {
    # 24x7 mode is active when the cron stop line is commented out
    grep -q '^#.*schedule-vllm\.sh stop' "$CRON_FILE" 2>/dev/null
}

case "$ACTION" in
    start)
        log "Starting vLLM service..."

        # Check if already running
        JOB_ID=$(get_vllm_job_id)
        if [ -n "$JOB_ID" ]; then
            log "vLLM service already running (Job ID: $JOB_ID)"
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
            echo "vLLM service is RUNNING (Job ID: $JOB_ID)"
            squeue -j "$JOB_ID" 2>/dev/null
        else
            echo "vLLM service is NOT RUNNING"
        fi
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
        echo "Usage: $0 [start|stop|status|enable-24x7|disable-24x7]"
        exit 1
        ;;
esac
