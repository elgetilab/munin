#!/bin/bash
# ==============================================================================
# MUNIN MAINTENANCE MODE
# ==============================================================================
# One switch for taking Munin down for maintenance (e.g. freeing the GPU
# for other experiments). It:
#   - writes a flag file that /api/status reports, so the chat UI and the
#     static maintenance page both show a maintenance screen;
#   - disables the vLLM start/stop cron so vLLM does not auto-boot;
#   - stops a running vLLM SLURM job;
#   - places the vLLM health check's hold file, or munin-vllm-health.timer
#     would restart vLLM at its next run inside the serving window;
#   - makes /api/research/start refuse new Deep Research jobs (the retrieval
#     service reads the same flag). Deep Research runs in-process against
#     vLLM, so a job already running fails at its next model call.
#
# The retired MiroThinker deepresearch-daemon is not touched in either
# direction; `off` used to `enable --now` it, reviving a retired service.
#
# Usage:
#   sudo munin-maintenance on ["message shown to users"]
#   sudo munin-maintenance off
#   sudo munin-maintenance status
#
# `off` restores the normal 6am/2am vLLM schedule. If you were running
# `vllm-service enable-24x7`, re-enable it afterwards. This script is the
# single owner of the vLLM cron toggle while maintenance is on.
# ==============================================================================

set -e

ACTION=${1:-status}
MESSAGE=${2:-}

FLAG_FILE=${MUNIN_MAINTENANCE_FLAG:-/opt/munin/data/maintenance.json}
CRON_FILE=${MUNIN_MAINTENANCE_CRON:-/etc/cron.d/hugin-cluster}
# check-vllm-health.sh skips its check-and-restart while this file exists.
# Paths are overridable only so tests can run the script against a temp dir.
# Our hold carries a marker so `off` never removes an operator's own hold.
HOLD_FILE=${MUNIN_VLLM_HOLD_FILE:-/opt/munin/logs/vllm-health.hold}
HOLD_MARKER="munin-maintenance"
LOG_FILE=${MUNIN_MAINTENANCE_LOG:-/var/log/cluster-admin/maintenance.log}
TIMESTAMP=$(date '+%Y-%m-%d %H:%M:%S')

mkdir -p "$(dirname "$LOG_FILE")"

log() {
    echo "$TIMESTAMP: $1" | tee -a "$LOG_FILE"
}

comment_vllm_cron() {
    # Comment out both schedule-vllm cron lines (idempotent — an
    # already-commented line is not matched). Hour-agnostic.
    if [ -f "$CRON_FILE" ]; then
        sed -i 's|^\([^#].*schedule-vllm\.sh start.*\)|#\1|' "$CRON_FILE"
        sed -i 's|^\([^#].*schedule-vllm\.sh stop.*\)|#\1|' "$CRON_FILE"
    fi
}

uncomment_vllm_cron() {
    # Restore the normal vLLM schedule.
    if [ -f "$CRON_FILE" ]; then
        sed -i 's|^#\(.*schedule-vllm\.sh start.*\)|\1|' "$CRON_FILE"
        sed -i 's|^#\(.*schedule-vllm\.sh stop.*\)|\1|' "$CRON_FILE"
    fi
}

case "$ACTION" in
    on)
        log "Enabling maintenance mode..."

        # 1. Flag file — /api/status reports this; both UIs read it.
        #    python3 writes the JSON so the message is escaped correctly.
        SINCE=$(date -u '+%Y-%m-%dT%H:%M:%SZ')
        python3 - "$MESSAGE" "$SINCE" "$FLAG_FILE" <<'PY'
import json, sys
message, since, path = sys.argv[1], sys.argv[2], sys.argv[3]
with open(path, "w") as f:
    json.dump({"message": message, "since": since}, f)
PY
        chmod 644 "$FLAG_FILE"
        log "Flag written: $FLAG_FILE"

        # 2. Stop vLLM auto-boot.
        comment_vllm_cron
        log "vLLM cron disabled"

        # 3. Stop a running vLLM job, if any.
        if command -v vllm-service >/dev/null 2>&1; then
            vllm-service stop >/dev/null 2>&1 || true
            log "vLLM service stop requested"
        fi

        # 4. Keep the health check from restarting vLLM. Leave an existing
        #    hold alone (an operator's, or ours from an earlier `on`).
        if [ ! -e "$HOLD_FILE" ]; then
            mkdir -p "$(dirname "$HOLD_FILE")"
            echo "$HOLD_MARKER $SINCE" > "$HOLD_FILE"
            log "vLLM health hold placed: $HOLD_FILE"
        else
            log "vLLM health hold already present: $HOLD_FILE"
        fi

        echo "[OK] Maintenance mode is ON."
        [ -n "$MESSAGE" ] && echo "     Message: $MESSAGE"
        echo "     vLLM cron disabled, health-check restarts held,"
        echo "     new Deep Research jobs refused."
        echo "     Turn off with: sudo munin-maintenance off"
        ;;

    off)
        log "Disabling maintenance mode..."

        rm -f "$FLAG_FILE"
        log "Flag removed"

        uncomment_vllm_cron
        log "vLLM cron restored (normal 6am/2am schedule)"

        # Remove the hold only if `on` placed it.
        if [ -f "$HOLD_FILE" ] && grep -q "^$HOLD_MARKER " "$HOLD_FILE" 2>/dev/null; then
            rm -f "$HOLD_FILE"
            log "vLLM health hold removed"
        elif [ -e "$HOLD_FILE" ]; then
            log "vLLM health hold left in place (not placed by maintenance mode)"
            echo "[NOTE] $HOLD_FILE was not placed by maintenance mode; left in place."
        fi

        echo "[OK] Maintenance mode is OFF."
        echo "     vLLM follows the normal schedule again (next 6am start,"
        echo "     or run: sudo vllm-service start). Deep Research accepts jobs again."
        echo "     If you were running 24/7 mode, re-enable it:"
        echo "       sudo vllm-service enable-24x7"
        ;;

    status)
        if [ -f "$FLAG_FILE" ]; then
            echo "Maintenance mode: ON"
            echo "  Flag: $FLAG_FILE"
            python3 - "$FLAG_FILE" <<'PY' || true
import json, sys
try:
    d = json.load(open(sys.argv[1]))
    print(f"  Since:   {d.get('since', '?')}")
    print(f"  Message: {d.get('message') or '(none)'}")
except Exception as e:
    print(f"  (could not parse flag: {e})")
PY
        else
            echo "Maintenance mode: OFF"
        fi
        if grep -q '^#.*schedule-vllm\.sh start' "$CRON_FILE" 2>/dev/null; then
            echo "  vLLM start cron: disabled"
        else
            echo "  vLLM start cron: enabled"
        fi
        if [ -e "$HOLD_FILE" ]; then
            echo "  vLLM health hold: present ($(head -c 80 "$HOLD_FILE" 2>/dev/null))"
        else
            echo "  vLLM health hold: none"
        fi
        ;;

    *)
        echo "Usage: $0 [on [\"message\"] | off | status]"
        exit 1
        ;;
esac
