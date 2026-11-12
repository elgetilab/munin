#!/bin/bash
# ==============================================================================
# Layer-1 integration test: the real llm-failover image between two stub
# OpenAI servers, with the "vLLM" stub killed mid-test and started again.
#
#   bash backend/llm-failover/tests/integration_failover.sh
#
# Throwaway only: every container, the network and the image carry a unique
# name and are removed on exit. Publishes no host port (requests are made from
# inside the proxy container), never touches the production compose project,
# and never calls the Claude API (the secondary is a stub too).
# ==============================================================================
set -euo pipefail

HERE=$(cd "$(dirname "$0")" && pwd)
SRC=$(cd "$HERE/.." && pwd)
ID="llmfo-it-$$"
NET="$ID-net"
IMG="munin-llm-failover:$ID"
STUB_IMG=${STUB_IMG:-python:3.12-slim}
P="$ID-primary"
S="$ID-secondary"
X="$ID-proxy"

cleanup() {
    docker rm -f "$P" "$S" "$X" >/dev/null 2>&1 || true
    docker network rm "$NET" >/dev/null 2>&1 || true
    docker image rm "$IMG" >/dev/null 2>&1 || true
}
trap cleanup EXIT

fail() { echo "FAIL: $*"; docker logs "$X" 2>&1 | tail -30; exit 1; }
pass() { echo "  ok  $*"; }

start_stub() {  # name model label
    docker run -d --rm --name "$1" --network "$NET" \
        -v "$HERE/stub_openai.py:/stub.py:ro" "$STUB_IMG" \
        python /stub.py 8000 "$2" "$3" >/dev/null
}

# req METHOD PATH [JSON]  ->  prints "<status> <X-Munin-LLM-Upstream>\n<body>"
req() {
    docker exec -i "$X" python - "$1" "$2" "${3:-}" <<'PY'
import sys, urllib.request, urllib.error
method, path, body = sys.argv[1], sys.argv[2], sys.argv[3]
r = urllib.request.Request("http://127.0.0.1:8000" + path, method=method,
                           data=body.encode() if body else None,
                           headers={"Content-Type": "application/json"})
try:
    resp = urllib.request.urlopen(r, timeout=20)
    status, headers, data = resp.status, resp.headers, resp.read()
except urllib.error.HTTPError as e:
    status, headers, data = e.code, e.headers, e.read()
print(status, headers.get("X-Munin-LLM-Upstream", "-"))
print(data.decode())
PY
}

json_get() {  # json_get EXPR  (reads JSON on stdin; EXPR uses d)
    python3 -c "import json,sys; d=json.load(sys.stdin); print($1)"
}

wait_for() {  # wait_for SECONDS DESCRIPTION COMMAND...
    local deadline=$((SECONDS + $1)) what=$2; shift 2
    until "$@"; do
        [ $SECONDS -lt $deadline ] || fail "timed out waiting for: $what"
        sleep 0.5
    done
}

route_is() { [ "$(req GET /status | tail -n +2 | json_get 'd["route"]')" = "$1" ]; }

CHAT='{"model":"qwen3.8-27b","messages":[{"role":"user","content":"hi"}],"top_k":20,"min_p":0,"chat_template_kwargs":{"enable_thinking":false}}'
STREAM='{"model":"qwen3.8-27b","stream":true,"stream_options":{"include_usage":true},"messages":[{"role":"user","content":"hi"}]}'

echo "[build] $IMG"
docker build -q -t "$IMG" "$SRC" >/dev/null
docker network create "$NET" >/dev/null

start_stub "$P" qwen3.8-27b PRIMARY-ANSWER
start_stub "$S" claude-haiku-5-5 SECONDARY-ANSWER
docker run -d --rm --name "$X" --network "$NET" \
    -e FAILOVER_PRIMARY_URL="http://$P:8000" \
    -e FAILOVER_SECONDARY_URL="http://$S:8000" \
    -e FAILOVER_PRIMARY_MODEL=qwen3.8-27b \
    -e FAILOVER_SECONDARY_MODEL=claude-haiku-5-5 \
    -e FAILOVER_PROBE_INTERVAL_S=1 \
    -e FAILOVER_FAIL_AFTER=2 \
    -e FAILOVER_RECOVER_AFTER=3 \
    -e FAILOVER_MIN_HOLD_S=6 \
    "$IMG" >/dev/null

wait_for 30 "proxy healthy" sh -c "docker exec $X python -c \"import urllib.request as u; u.urlopen('http://127.0.0.1:8000/health', timeout=2)\" 2>/dev/null"
wait_for 10 "first probes" sh -c "[ \"\$(docker exec $X python -c \"import json,urllib.request as u; print(json.load(u.urlopen('http://127.0.0.1:8000/status'))['primary']['ok'])\")\" = True ]"

echo "[1] both up: primary answers"
out=$(req POST /v1/chat/completions "$CHAT")
[ "$(head -1 <<<"$out")" = "200 primary" ] || fail "expected 200 primary, got: $(head -1 <<<"$out")"
grep -q PRIMARY-ANSWER <<<"$out" || fail "primary body missing"
pass "non-streamed request served by primary"
out=$(req POST /v1/chat/completions "$STREAM")
[ "$(head -1 <<<"$out")" = "200 primary" ] && grep -q '\[DONE\]' <<<"$out" || fail "stream via primary: $out"
pass "streamed request served by primary"

echo "[2] kill the primary mid-test"
docker kill "$P" >/dev/null
out=$(req POST /v1/chat/completions "$CHAT")
[ "$(head -1 <<<"$out")" = "200 secondary" ] || fail "expected per-request fallback, got: $(head -1 <<<"$out")"
grep -q SECONDARY-ANSWER <<<"$out" || fail "secondary body missing"
pass "first request after the kill answered by the secondary (per-request fallback)"
last=$(docker exec "$S" python -c "import urllib.request as u; print(u.urlopen('http://127.0.0.1:8000/last').read().decode())")
[ "$(json_get 'd["model"]' <<<"$last")" = "claude-haiku-5-5" ] || fail "secondary saw model $(json_get 'd["model"]' <<<"$last")"
[ "$(json_get '"top_k" in d or "min_p" in d' <<<"$last")" = "False" ] || fail "vLLM sampling fields reached the secondary"
[ "$(json_get 'd["chat_template_kwargs"]' <<<"$last")" = "{'enable_thinking': False}" ] || fail "thinking-off marker lost"
pass "secondary got a Claude-safe body (model rewritten, top_k/min_p dropped, thinking-off kept)"

wait_for 10 "route=secondary" route_is secondary
pass "route moved to secondary"
models=$(req GET /v1/models | tail -n +2)
[ "$(json_get 'd["munin_llm"]["fallback_active"]' <<<"$models")" = "True" ] || fail "/v1/models does not flag the fallback"
[ "$(json_get 'd["data"][0]["id"]' <<<"$models")" = "qwen3.8-27b" ] || fail "/v1/models id changed"
pass "/v1/models keeps id qwen3.8-27b and reports munin_llm.fallback_active=true"
out=$(req POST /v1/chat/completions "$STREAM")
[ "$(head -1 <<<"$out")" = "200 secondary" ] && grep -q '\[DONE\]' <<<"$out" || fail "stream via secondary: $out"
pass "streamed request served by secondary"

echo "[3] primary returns: hold, then switch back"
start_stub "$P" qwen3.8-27b PRIMARY-ANSWER
t0=$SECONDS
sleep 3
route_is secondary || fail "switched back before the hold expired"
pass "still on secondary 3s after the primary returned (hold 6s, 3 good probes)"
wait_for 20 "route=primary" route_is primary
pass "switched back to primary after $((SECONDS - t0))s"
out=$(req POST /v1/chat/completions "$CHAT")
[ "$(head -1 <<<"$out")" = "200 primary" ] || fail "after recovery: $(head -1 <<<"$out")"
pass "requests served by primary again"

echo "[4] metrics and logs"
metrics=$(req GET /metrics | tail -n +2)
grep -q 'munin_llm_failover_transitions_total{to="secondary"} 1' <<<"$metrics" || fail "transition metric"
grep -q 'munin_llm_failover_transitions_total{to="primary"} 1' <<<"$metrics" || fail "transition metric back"
grep -q 'munin_llm_failover_active 0' <<<"$metrics" || fail "active gauge"
pass "exactly one transition each way (no flapping), active gauge back to 0"
docker logs "$X" 2>&1 | grep -q "failover route=secondary" || fail "no admin log line for the failover"
docker logs "$X" 2>&1 | grep -q "failover route=primary" || fail "no admin log line for the recovery"
pass "admin log lines present"

echo "[5] both down: fast 503"
docker kill "$P" "$S" >/dev/null
out=$(req POST /v1/chat/completions "$CHAT")
case "$(head -1 <<<"$out")" in 503*) pass "503 when no upstream answers";; *) fail "expected 503: $out";; esac

echo "PASS: layer-1 integration"
