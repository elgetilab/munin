#!/bin/bash
# ==============================================================================
# CHECK VLLM HEALTH
# ==============================================================================
# Verifies, inside the vLLM serving window, that the SLURM node is usable and
# that vLLM actually answers. Attempts ONE restart when it is simply absent,
# then reports either way.
#
# WHY THIS EXISTS. On 2026-08-31 the weekly reboot booted a kernel with no
# NVIDIA module, slurmd exited fatally, and the 06:00 cron submitted a vLLM job
# that could never schedule. Chat was down from 05:02 until a human happened to
# ask at 10:44. Nothing surfaced it: `wall` reaches only logged-in terminals,
# and the Prometheus counters (munin_vllm_request_total) only move when chat
# traffic flows, so a dead vLLM with no users emits precisely nothing.
#
# Usage: check-vllm-health.sh [--dry-run]
# Exit:  0 healthy or outside the window, 1 degraded
# ==============================================================================

LOG_FILE="/var/log/cluster-admin/vllm-health.log"
STAMP_FILE="/opt/munin/logs/vllm-health-restart.stamp"
HOLD_FILE="/opt/munin/logs/vllm-health.hold"
SCHEDULE_VLLM="/opt/cluster/scripts/llm/schedule-vllm.sh"
HEALTH_URL="${VLLM_HEALTH_URL:-http://127.0.0.1:8000/v1/models}"

# Serving window, matching the cron in HuginSLURM setup/07-scheduling-setup.sh
# (start 06:00, stop 02:00). Outside it vLLM is MEANT to be down and the GPUs
# belong to batch work, so a restart here would steal the nightly window that
# the chunk-index embed pass runs in.
WINDOW_START_H=6
WINDOW_STOP_H=2

# Do not resubmit more than once an hour. A vLLM that crashloops must not be
# fed back to SLURM every time the timer fires; one attempt, then it is a
# human's problem and the alert says so.
RESTART_COOLDOWN=3600

DRY_RUN=0
[ "$1" = "--dry-run" ] && DRY_RUN=1

mkdir -p /var/log/cluster-admin /opt/munin/logs

# Alerts name the host, so this reads correctly on any node, not just hugin.
HOST=$(hostname -s 2>/dev/null || echo unknown)

log() { echo "$(date '+%Y-%m-%d %H:%M:%S'): $1" | tee -a "$LOG_FILE"; }

# Optional out-of-band alert. Unset is a silent no-op so other sites deploy
# this cleanly without inventing a notification stack.
[ -f /opt/hugin/config/cluster.env ] && . /opt/hugin/config/cluster.env 2>/dev/null

# Refuse to POST to a placeholder. The documented value is an example URL, and
# an example URL pasted verbatim is a REAL destination: ntfy topics are public
# and unauthenticated, so a guessable topic name broadcasts cluster state to
# anyone who subscribes. Treat a placeholder as unset rather than as a target.
case "$MUNIN_ALERT_WEBHOOK" in
    *CHANGE-ME*|*CHANGE_ME*|*change-me*|*your-topic*|*example.com*|*EXAMPLE*)
        MUNIN_ALERT_WEBHOOK=""
        PLACEHOLDER_WEBHOOK=1
        ;;
esac

alert() {
    local msg="$1"
    log "ALERT: $msg"
    wall <<EOF 2>/dev/null || true

=============================================================
  MUNIN vLLM HEALTH CHECK
=============================================================
$msg

See $LOG_FILE
=============================================================
EOF
    if [ -n "$MUNIN_ALERT_WEBHOOK" ]; then
        # Each target wants a DIFFERENT payload key. Slack takes {"text":...},
        # Discord takes {"content":...} and 400s on anything else, Gotify takes
        # {"message":...}, and ntfy treats the raw body as the message so JSON
        # would be delivered as literal braces. Detect from the URL, with
        # MUNIN_ALERT_FORMAT as an explicit override for proxies and self-hosted
        # instances whose hostname gives nothing away.
        local fmt="${MUNIN_ALERT_FORMAT:-}"
        if [ -z "$fmt" ]; then
            case "$MUNIN_ALERT_WEBHOOK" in
                *hooks.slack.com*)          fmt=slack ;;
                *discord.com/api/webhooks*|*discordapp.com/api/webhooks*)
                                            fmt=discord ;;
                *ntfy*)                     fmt=ntfy ;;
                *gotify*|*/message?token=*) fmt=gotify ;;
                *)                          fmt=json ;;
            esac
        fi

        # Escape for JSON: backslashes, quotes, newlines.
        local esc ctype payload
        esc=$(printf '%s' "$msg" | sed 's/\\/\\\\/g; s/"/\\"/g' | tr '\n' ' ')
        case "$fmt" in
            slack)   ctype='application/json'
                     payload="{\"text\":\"[munin/$HOST] $esc\"}" ;;
            discord) ctype='application/json'
                     payload="{\"content\":\"[munin/$HOST] $esc\"}" ;;
            gotify)  ctype='application/json'
                     payload="{\"title\":\"munin/$HOST\",\"message\":\"$esc\"}" ;;
            ntfy)    ctype='text/plain'
                     payload="[munin/$HOST] $(printf '%s' "$msg" | tr '\n' ' ')" ;;
            *)       ctype='application/json'
                     payload="{\"text\":\"[munin/$HOST] $esc\"}" ;;
        esac

        if [ "$DRY_RUN" = "1" ]; then
            log "  [dry-run] would POST ($fmt) to \$MUNIN_ALERT_WEBHOOK"
            log "  [dry-run] Content-Type: $ctype"
            log "  [dry-run] body: $payload"
        else
            curl -fsS --max-time 15 -X POST "$MUNIN_ALERT_WEBHOOK" \
                 -H "Content-Type: $ctype" \
                 -d "$payload" >/dev/null 2>&1 \
                 && log "  webhook notified ($fmt)" \
                 || log "  WARNING: webhook POST failed ($fmt); check the URL and MUNIN_ALERT_FORMAT"
        fi
    elif [ "$PLACEHOLDER_WEBHOOK" = "1" ]; then
        log "  WARNING: MUNIN_ALERT_WEBHOOK still holds a PLACEHOLDER; refusing to POST."
        log "           Set a real, unguessable destination in /opt/hugin/config/cluster.env"
    else
        log "  (MUNIN_ALERT_WEBHOOK unset; log + wall only)"
    fi
}

# ------------------------------------------------------------------------------
# Window guard
# ------------------------------------------------------------------------------
H=$(date +%-H)
if [ "$H" -ge "$WINDOW_STOP_H" ] && [ "$H" -lt "$WINDOW_START_H" ]; then
    log "outside the serving window (${WINDOW_STOP_H}:00-${WINDOW_START_H}:00); vLLM is meant to be down"
    exit 0
fi

if [ -f "$HOLD_FILE" ]; then
    log "hold file present ($HOLD_FILE); skipping check and restart"
    exit 0
fi

# ------------------------------------------------------------------------------
# Is vLLM answering?
# ------------------------------------------------------------------------------
if curl -fsS --max-time 10 "$HEALTH_URL" >/dev/null 2>&1; then
    log "OK: vLLM answering at $HEALTH_URL"
    rm -f "$STAMP_FILE"          # healthy again, so the next outage may restart
    exit 0
fi

log "vLLM NOT answering at $HEALTH_URL; diagnosing"

# ------------------------------------------------------------------------------
# Diagnose before acting
# ------------------------------------------------------------------------------
NODE_STATE=$(scontrol show node hugin 2>/dev/null | grep -oP 'State=\K[^ ]+' || echo "UNKNOWN")
JOB=$(squeue -h -n vllm-service,vllm-service-tp2 -o "%i %T" 2>/dev/null | head -1)
log "  node state: $NODE_STATE"
log "  vllm job  : ${JOB:-none}"

# A node that is DOWN/DRAINED cannot run anything, so resubmitting only creates
# another pending job. This is 2026-08-31's shape and it needs a human.
case "$NODE_STATE" in
    *DOWN*|*DRAIN*|*NOT_RESPONDING*|UNKNOWN)
        alert "vLLM is down AND the SLURM node is $NODE_STATE. Not resubmitting: a job would only pend. Check: systemctl status slurmd, nvidia-smi, and /lib/modules/\$(uname -r) for the NVIDIA module."
        exit 1
        ;;
esac

if [ -n "$JOB" ]; then
    alert "vLLM is not answering but a job already exists ($JOB). It may still be loading (~2 min) or be stuck. Not resubmitting. Check /opt/munin/logs/vllm-service-*.out"
    exit 1
fi

# ------------------------------------------------------------------------------
# Node is fine and no job exists: attempt ONE restart
# ------------------------------------------------------------------------------
NOW=$(date +%s)
LAST=0
[ -f "$STAMP_FILE" ] && LAST=$(cat "$STAMP_FILE" 2>/dev/null || echo 0)
AGE=$(( NOW - LAST ))

if [ "$AGE" -lt "$RESTART_COOLDOWN" ]; then
    alert "vLLM is down and was already restarted ${AGE}s ago (cooldown ${RESTART_COOLDOWN}s). NOT resubmitting again; this looks like a crashloop and needs a human. Check /opt/munin/logs/vllm-service-*.out"
    exit 1
fi

if [ "$DRY_RUN" = "1" ]; then
    log "  [dry-run] would run: $SCHEDULE_VLLM start"
    alert "[dry-run] vLLM down, node $NODE_STATE, would have restarted"
    exit 1
fi

if [ ! -x "$SCHEDULE_VLLM" ]; then
    alert "vLLM is down and $SCHEDULE_VLLM is missing or not executable. Cannot restart."
    exit 1
fi

echo "$NOW" > "$STAMP_FILE"
log "  restarting vLLM via $SCHEDULE_VLLM start"
if "$SCHEDULE_VLLM" start >>"$LOG_FILE" 2>&1; then
    alert "vLLM was down with node $NODE_STATE and no job queued; submitted a restart. Verify it comes up: squeue, then curl $HEALTH_URL"
else
    alert "vLLM was down and the restart command FAILED. Needs a human. See $LOG_FILE"
fi
exit 1
