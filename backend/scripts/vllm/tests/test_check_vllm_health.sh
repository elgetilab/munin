#!/bin/bash
# Incident dedup + recovery for check-vllm-health.sh, with curl, scontrol,
# squeue, wall and hostname stubbed on PATH and every state path in a temp dir.
# Nothing here touches the network, /opt or the real cluster.env.
#
# Usage: bash backend/scripts/vllm/tests/test_check_vllm_health.sh
set -u

HERE=$(cd "$(dirname "$0")" && pwd)
SCRIPT="$HERE/../check-vllm-health.sh"
T=$(mktemp -d)
trap 'rm -rf "$T"' EXIT
mkdir -p "$T/bin" "$T/state" "$T/log"

# curl: a POST records its body in posts.txt; anything else is the health
# probe, which succeeds iff $T/healthy exists.
cat > "$T/bin/curl" <<STUB
#!/bin/bash
post=0; body=""
while [ \$# -gt 0 ]; do
    case "\$1" in
        -X) [ "\$2" = POST ] && post=1; shift ;;
        -d) body="\$2"; shift ;;
    esac
    shift
done
if [ \$post = 1 ]; then printf '%s\n' "\$body" >> "$T/posts.txt"; exit 0; fi
[ -f "$T/healthy" ]
STUB
cat > "$T/bin/scontrol" <<STUB
#!/bin/bash
echo "NodeName=hugin State=\$(cat "$T/node_state")"
STUB
printf '#!/bin/bash\ncat "%s/jobs" 2>/dev/null\n' "$T" > "$T/bin/squeue"
printf '#!/bin/bash\ncat >/dev/null\n' > "$T/bin/wall"
printf '#!/bin/bash\necho testhost\n' > "$T/bin/hostname"
printf '#!/bin/bash\necho "schedule $*" >> "%s/schedule.txt"\n' "$T" > "$T/bin/schedule-vllm.sh"
chmod +x "$T/bin/"*
echo 'MUNIN_ALERT_WEBHOOK=https://discord.com/api/webhooks/0/test-token' > "$T/cluster.env"

FAIL=0
run() {
    PATH="$T/bin:$PATH" \
    VLLM_HEALTH_LOG_DIR="$T/log" VLLM_HEALTH_STATE_DIR="$T/state" \
    VLLM_HEALTH_CLUSTER_ENV="$T/cluster.env" \
    VLLM_HEALTH_SCHEDULE_VLLM="$T/bin/schedule-vllm.sh" \
    VLLM_HEALTH_HOUR=12 MUNIN_ALERT_FORMAT="" \
        bash "$SCRIPT" "$@" >/dev/null 2>&1
}
posts() { [ -f "$T/posts.txt" ] && wc -l < "$T/posts.txt" || echo 0; }
check() {  # check NAME CONDITION...
    local name="$1"; shift
    if "$@"; then echo "ok   $name"; else echo "FAIL $name"; FAIL=1; fi
}
last_post_has() { tail -1 "$T/posts.txt" | grep -q "$1"; }

# 1. First failure (node DOWN, so no restart attempt) opens an incident and posts.
echo "DOWN+DRAIN" > "$T/node_state"
run
check "first failure posts once"          [ "$(posts)" -eq 1 ]
check "post is a Discord content payload" last_post_has '^{"content":"\[munin/testhost\] vLLM is down AND'
check "incident file opened"              grep -q ' node_down$' "$T/state/vllm-health.incident"

# 2. Same failure an hour later: logged, not posted.
run
check "repeat failure is silent"          [ "$(posts)" -eq 1 ]
check "repeat is still logged"            grep -q 'ALERT (repeat' "$T/log/vllm-health.log"

# 3. Dry run against the open incident: no POST, state untouched.
cp "$T/state/vllm-health.incident" "$T/inc.before"
touch "$T/healthy"
run --dry-run
check "dry-run recovery does not POST"    [ "$(posts)" -eq 1 ]
check "dry-run leaves the incident open"  cmp -s "$T/inc.before" "$T/state/vllm-health.incident"
check "dry-run logs the recovery"         grep -q '\[dry-run\] vLLM recovered after' "$T/log/vllm-health.log"

# 4. Recovery: one "recovered after N min" post, incident closed.
# Backdate the start so the duration is non-trivial.
echo "$(( $(date +%s) - 1500 )) node_down" > "$T/state/vllm-health.incident"
run
check "recovery posts once"               [ "$(posts)" -eq 2 ]
check "recovery names the duration"       last_post_has 'recovered after 25 min'
check "incident file closed"              [ ! -f "$T/state/vllm-health.incident" ]

# 5. Healthy again: quiet.
run
check "healthy after recovery is quiet"   [ "$(posts)" -eq 2 ]

# 6. A class change inside one incident posts again (new information):
# restart submitted, then a stuck job.
rm -f "$T/healthy"
echo "IDLE" > "$T/node_state"
run
check "restart path posts"                [ "$(posts)" -eq 3 ]
check "restart was submitted"             grep -q 'schedule start' "$T/schedule.txt"
echo "123 PENDING" > "$T/jobs"
run
check "class change posts again"          [ "$(posts)" -eq 4 ]
check "class change names the old class"  last_post_has 'was: restart_submitted'
run
check "same class after change is silent" [ "$(posts)" -eq 4 ]

# 7. Placeholder webhook: never POSTs.
rm -f "$T/state/vllm-health.incident" "$T/jobs"
echo 'MUNIN_ALERT_WEBHOOK=https://ntfy.sh/your-topic' > "$T/cluster.env"
echo "DOWN" > "$T/node_state"
run
check "placeholder webhook never POSTs"   [ "$(posts)" -eq 4 ]

# 8. Outside the serving window nothing is probed or posted.
VLLM_HEALTH_HOUR=3 run
check "outside window is a no-op"         [ "$(posts)" -eq 4 ]

[ $FAIL = 0 ] && echo "PASS" || { echo "FAILED"; tail -30 "$T/log/vllm-health.log"; }
exit $FAIL
