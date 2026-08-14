#!/bin/sh
# ==============================================================================
# run-daily.sh — run a command once a day at a fixed local time, forever.
# ==============================================================================
# The container-side replacement for the systemd timers that drive the nightly
# jobs (munin-paper-reattribute.timer at 04:30, munin-embedding-map.timer at
# 01:30). Deliberately a ~40-line shell loop rather than a cron image: the
# schedule is one time-of-day per job, and a cron daemon would add a package,
# a config file and a second log stream for that.
#
#   usage: run-daily.sh HH:MM <command> [args...]
#
# Behaviour, and how it differs from the systemd timers it replaces:
#
#   - Sleeps until the next occurrence of HH:MM in the container's local time,
#     runs the command, then sleeps again. Because it recomputes the target
#     from the wall clock every cycle rather than sleeping a fixed 86400s, it
#     does not drift and it survives DST shifts.
#   - A failing command is logged and the loop continues, matching a systemd
#     oneshot: a bad night must not stop every later night.
#   - RUN_DAILY_ON_START=1 runs once immediately before the first sleep. This
#     is the rough equivalent of the timers' `Persistent=true` catch-up, and is
#     what you want on a fresh instance where the artifact does not exist yet
#     (an empty embedding map means an empty knowledge graph page all day).
#     Default is 0: no surprise work on every container restart.
#   - There is no `RandomizedDelaySec` equivalent. The embedding-map timer uses
#     one to spread load across a fleet; a single host does not need it.
# ==============================================================================
set -eu

if [ $# -lt 2 ]; then
    echo "usage: run-daily.sh HH:MM <command> [args...]" >&2
    exit 2
fi

AT="$1"
shift

# Validate early: a typo here would otherwise surface as a `date` error once a
# day, forever, with the job silently never running.
if ! echo "$AT" | grep -qE '^([01][0-9]|2[0-3]):[0-5][0-9]$'; then
    echo "[run-daily] FATAL: '$AT' is not a HH:MM time in 00:00-23:59" >&2
    exit 2
fi

run_once() {
    echo "[run-daily] $(date -Is) starting: $*"
    if "$@"; then
        echo "[run-daily] $(date -Is) finished ok"
    else
        # Keep looping. Mirrors a systemd oneshot: report and wait for tomorrow.
        echo "[run-daily] $(date -Is) FAILED (exit $?), continuing" >&2
    fi
}

if [ "${RUN_DAILY_ON_START:-0}" = "1" ]; then
    run_once "$@"
fi

while true; do
    now=$(date +%s)
    next=$(date -d "today $AT" +%s)
    # Already past today's slot, so aim at tomorrow's.
    [ "$next" -le "$now" ] && next=$(date -d "tomorrow $AT" +%s)

    echo "[run-daily] next run at $(date -d "@$next" -Is) ($((next - now))s from now)"
    sleep $((next - now))

    run_once "$@"
done
