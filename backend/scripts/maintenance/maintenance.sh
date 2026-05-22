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
#   - stops the Deep Research (MiroThinker) daemon so no new jobs launch
#     (jobs already on SLURM are left to finish).
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

FLAG_FILE="/opt/munin/data/maintenance.json"
CRON_FILE="/etc/cron.d/hugin-cluster"
DEEPRESEARCH_UNIT="deepresearch-daemon"
LOG_FILE="/var/log/cluster-admin/maintenance.log"
TIMESTAMP=$(date '+%Y-%m-%d %H:%M:%S')

mkdir -p /var/log/cluster-admin

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

        # 4. Block Deep Research (MiroThinker). disable --now so it stays
        #    down across a reboot; in-flight SLURM jobs are left to finish.
        systemctl disable --now "$DEEPRESEARCH_UNIT" >/dev/null 2>&1 || true
        log "Deep Research daemon stopped"

        echo "[OK] Maintenance mode is ON."
        [ -n "$MESSAGE" ] && echo "     Message: $MESSAGE"
        echo "     vLLM cron disabled, Deep Research daemon stopped."
        echo "     Turn off with: sudo munin-maintenance off"
        ;;

    off)
        log "Disabling maintenance mode..."

        rm -f "$FLAG_FILE"
        log "Flag removed"

        uncomment_vllm_cron
        log "vLLM cron restored (normal 6am/2am schedule)"

        systemctl enable --now "$DEEPRESEARCH_UNIT" >/dev/null 2>&1 || true
        log "Deep Research daemon restarted"

        echo "[OK] Maintenance mode is OFF."
        echo "     vLLM follows the normal schedule again (next 6am start,"
        echo "     or run: sudo vllm-service start). Deep Research resumed."
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
        if systemctl is-active --quiet "$DEEPRESEARCH_UNIT" 2>/dev/null; then
            echo "  Deep Research daemon: running"
        else
            echo "  Deep Research daemon: stopped"
        fi
        ;;

    *)
        echo "Usage: $0 [on [\"message\"] | off | status]"
        exit 1
        ;;
esac
