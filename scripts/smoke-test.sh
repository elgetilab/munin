#!/bin/bash
# ==============================================================================
# MUNIN BACKEND - END-TO-END SMOKE TEST
# ==============================================================================
# Exercises every Stream 1-5 endpoint against a running retrieval service.
# Intended to run on the cluster head against 127.0.0.1:8080, but works
# against any reachable base URL via $RETRIEVAL_BASE.
#
# Requires: curl, python3. No sudo, no jq.
#
# Usage:
#   ./scripts/smoke-test.sh                    # full suite
#   ./scripts/smoke-test.sh --skip-chat        # skip vLLM-dependent checks
#   RETRIEVAL_BASE=http://host:8080 ./...      # point elsewhere
#   SMOKE_EMAIL=you@example.com  ./...         # override synthetic email
# ==============================================================================

set -u

RETRIEVAL_BASE=${RETRIEVAL_BASE:-http://127.0.0.1:8080}
SMOKE_EMAIL=${SMOKE_EMAIL:-smoke-test@munin.local}
SKIP_CHAT=0

if [ "${1:-}" = "--skip-chat" ]; then
    SKIP_CHAT=1
fi

PASS=0
FAIL=0
FAILED_NAMES=()

H_EMAIL="X-Munin-Email: $SMOKE_EMAIL"

pass() { echo "  [PASS] $1"; PASS=$((PASS + 1)); }
fail() { echo "  [FAIL] $1"; FAIL=$((FAIL + 1)); FAILED_NAMES+=("$1"); }

# pyget '{"a":{"b":1}}' "d['a']['b']" → prints 1
pyget() {
    python3 -c "import json,sys; d=json.loads(sys.argv[1]); print($2)" "$1" 2>/dev/null
}

# Assert a JSON body has a python-expressible truthy value at the given path
pycheck() {
    python3 -c "import json,sys; d=json.loads(sys.argv[1]); assert $2, 'assertion failed'" "$1" 2>/dev/null
}

echo "=============================================="
echo "MUNIN BACKEND - Smoke Test"
echo "=============================================="
echo "Base:   $RETRIEVAL_BASE"
echo "Email:  $SMOKE_EMAIL"
echo "Chat:   $([ "$SKIP_CHAT" = "1" ] && echo "SKIPPED" || echo "included")"
echo ""

# ------------------------------------------------------------------------------
# 1. /health — the absolute minimum
# ------------------------------------------------------------------------------
echo "[1/8] /health"
if curl -fsS -m 5 -o /dev/null "$RETRIEVAL_BASE/health"; then
    pass "/health responds"
else
    fail "/health unreachable"
    echo ""
    echo "Retrieval service is not responding. Aborting."
    exit 1
fi

# ------------------------------------------------------------------------------
# 2. /api/status — shape + vllm state
# ------------------------------------------------------------------------------
echo ""
echo "[2/8] /api/status"
STATUS_BODY=$(curl -fsS -m 5 "$RETRIEVAL_BASE/api/status" 2>/dev/null || echo "")
if [ -z "$STATUS_BODY" ]; then
    fail "/api/status returned no body"
else
    if pycheck "$STATUS_BODY" "'vllm' in d and 'services' in d and 'timestamp' in d"; then
        pass "status shape (vllm/services/timestamp)"
    else
        fail "status shape missing expected keys"
    fi
    VLLM_STATE=$(pyget "$STATUS_BODY" "d['vllm']['status']")
    echo "         vllm=$VLLM_STATE"
    if [ "$VLLM_STATE" = "running" ]; then
        pass "vllm is running"
    elif [ "$SKIP_CHAT" = "0" ]; then
        echo "  [WARN] vllm status is '$VLLM_STATE' — chat completion tests will likely fail"
        echo "         start with: sudo vllm-service start"
    fi
fi

# ------------------------------------------------------------------------------
# 3. /api/personas
# ------------------------------------------------------------------------------
echo ""
echo "[3/8] /api/personas"
PERSONAS_BODY=$(curl -fsS -m 5 -H "$H_EMAIL" "$RETRIEVAL_BASE/api/personas" 2>/dev/null || echo "")
if [ -z "$PERSONAS_BODY" ]; then
    fail "/api/personas returned no body"
else
    PERSONA_COUNT=$(pyget "$PERSONAS_BODY" "len(d.get('personas', []))")
    if [ "${PERSONA_COUNT:-0}" -gt 0 ]; then
        IDS=$(pyget "$PERSONAS_BODY" "','.join(p['id'] for p in d['personas'])")
        pass "personas loaded ($PERSONA_COUNT: $IDS)"
        if pycheck "$PERSONAS_BODY" "'default_persona' in d and d['default_persona'] in [p['id'] for p in d['personas']]"; then
            pass "default_persona is valid"
        else
            fail "default_persona missing or unknown"
        fi
    else
        fail "no personas loaded (check /opt/munin/personas mount)"
    fi
fi

# ------------------------------------------------------------------------------
# 4. /api/chats CRUD + /api/chat/completions (streaming)
# ------------------------------------------------------------------------------
CONVERSATION_ID=""
if [ "$SKIP_CHAT" = "0" ]; then
    echo ""
    echo "[4/8] /api/chat/completions (SSE streaming)"

    SSE_OUT=$(mktemp)
    trap 'rm -f "$SSE_OUT"' EXIT

    # Fire a short, no-RAG request. vLLM's reasoning/tool machinery still
    # gets exercised because auto tool choice is on in chat_service.
    curl -sS -N -m 120 \
        -H "$H_EMAIL" \
        -H "Content-Type: application/json" \
        -d '{"persona":"chat","conversation_id":null,"messages":[{"role":"user","content":"Reply with just the number: what is 2+2?"}],"rag":{"enabled":false},"stream":true}' \
        "$RETRIEVAL_BASE/api/chat/completions" > "$SSE_OUT" 2>/dev/null || true

    if [ ! -s "$SSE_OUT" ]; then
        fail "chat completion produced no output"
    else
        # Count event types
        EVT_CONVERSATION=$(grep -c '^event: conversation' "$SSE_OUT" || true)
        EVT_TOKEN=$(grep -c '^event: token' "$SSE_OUT" || true)
        EVT_DONE=$(grep -c '^event: done' "$SSE_OUT" || true)
        EVT_ERROR=$(grep -c '^event: error' "$SSE_OUT" || true)

        echo "         events: conversation=$EVT_CONVERSATION token=$EVT_TOKEN done=$EVT_DONE error=$EVT_ERROR"

        if [ "$EVT_ERROR" -gt 0 ]; then
            ERR_MSG=$(grep -A1 '^event: error' "$SSE_OUT" | tail -1 | sed 's/^data: //')
            fail "chat stream emitted error: $ERR_MSG"
        elif [ "$EVT_CONVERSATION" -ge 1 ] && [ "$EVT_DONE" -ge 1 ]; then
            pass "stream emitted conversation + done"
            if [ "$EVT_TOKEN" -ge 1 ]; then
                pass "stream emitted $EVT_TOKEN token event(s)"
            else
                fail "stream had no token events (vLLM did not produce content)"
            fi
            # Extract the conversation id from the first conversation event
            CONVERSATION_ID=$(grep -A1 '^event: conversation' "$SSE_OUT" | head -2 | tail -1 | \
                sed 's/^data: //' | python3 -c "import json,sys; print(json.loads(sys.stdin.read())['id'])" 2>/dev/null || echo "")
            if [ -n "$CONVERSATION_ID" ]; then
                pass "conversation id extracted: $CONVERSATION_ID"
            else
                fail "could not extract conversation id from stream"
            fi
        else
            fail "stream missing required events"
            echo "  --- first 20 lines of SSE output ---"
            head -20 "$SSE_OUT" | sed 's/^/    /'
            echo "  -------------------------------------"
        fi
    fi

    # /api/chats list — newly-created conversation should show up
    echo ""
    echo "[5/8] /api/chats list + load + rename + delete"
    if [ -n "$CONVERSATION_ID" ]; then
        CHATS_BODY=$(curl -fsS -m 5 -H "$H_EMAIL" "$RETRIEVAL_BASE/api/chats?limit=50" 2>/dev/null || echo "")
        if pycheck "$CHATS_BODY" "any(c['id']=='$CONVERSATION_ID' for c in d.get('conversations',[]))"; then
            pass "new conversation appears in /api/chats"
        else
            fail "new conversation missing from /api/chats"
        fi

        # /api/chats/{id} — full load should include at least 2 messages
        LOADED=$(curl -fsS -m 5 -H "$H_EMAIL" "$RETRIEVAL_BASE/api/chats/$CONVERSATION_ID" 2>/dev/null || echo "")
        if pycheck "$LOADED" "len(d.get('messages',[])) >= 2"; then
            MSG_COUNT=$(pyget "$LOADED" "len(d['messages'])")
            pass "conversation loaded with $MSG_COUNT message(s)"
        else
            fail "conversation load missing messages"
        fi

        # PATCH — rename
        RENAMED=$(curl -fsS -m 5 -X PATCH -H "$H_EMAIL" \
            -H "Content-Type: application/json" \
            -d '{"title":"Smoke test chat"}' \
            "$RETRIEVAL_BASE/api/chats/$CONVERSATION_ID" 2>/dev/null || echo "")
        if pycheck "$RENAMED" "d.get('title') == 'Smoke test chat'"; then
            pass "PATCH /api/chats/{id} renamed"
        else
            fail "PATCH /api/chats/{id} did not apply title"
        fi

        # DELETE
        DELETED=$(curl -fsS -m 5 -X DELETE -H "$H_EMAIL" \
            "$RETRIEVAL_BASE/api/chats/$CONVERSATION_ID" 2>/dev/null || echo "")
        if pycheck "$DELETED" "d.get('deleted') is True"; then
            pass "DELETE /api/chats/{id} acknowledged"
        else
            fail "DELETE /api/chats/{id} did not acknowledge"
        fi

        # Confirm it's gone
        GONE=$(curl -s -o /dev/null -w "%{http_code}" -H "$H_EMAIL" "$RETRIEVAL_BASE/api/chats/$CONVERSATION_ID")
        if [ "$GONE" = "404" ]; then
            pass "deleted conversation is 404"
        else
            fail "deleted conversation still returns HTTP $GONE"
        fi
    else
        echo "  (skipped — no conversation id from chat step)"
    fi
else
    echo ""
    echo "[4/8] /api/chat/completions — SKIPPED (--skip-chat)"
    echo "[5/8] /api/chats CRUD — SKIPPED"
fi

# ------------------------------------------------------------------------------
# 6. /api/documents upload → list → delete
# ------------------------------------------------------------------------------
echo ""
echo "[6/8] /api/documents upload"
DOC_FILE=$(mktemp --suffix=.txt)
cat > "$DOC_FILE" <<'EOF'
Munin smoke test document.

Polymer crystallization occurs when long-chain molecules fold into ordered
lamellar structures. The kinetics depend on temperature, chain length, and
cooling rate. This is a tiny synthetic document for exercising the chunk +
embed pipeline end-to-end.
EOF

UPLOAD=$(curl -fsS -m 60 -H "$H_EMAIL" \
    -F "file=@$DOC_FILE;filename=smoke-test.txt" \
    "$RETRIEVAL_BASE/api/documents/upload" 2>/dev/null || echo "")
rm -f "$DOC_FILE"

DOC_ID=""
if pycheck "$UPLOAD" "'document_id' in d and d.get('status') in ('embedded','stored')"; then
    DOC_ID=$(pyget "$UPLOAD" "d['document_id']")
    CHUNKS=$(pyget "$UPLOAD" "d.get('chunks', 0)")
    STATUS=$(pyget "$UPLOAD" "d.get('status')")
    pass "upload acknowledged (id=$DOC_ID chunks=$CHUNKS status=$STATUS)"
else
    fail "upload did not return a document_id"
fi

echo ""
echo "[7/8] /api/documents list"
LIST=$(curl -fsS -m 5 -H "$H_EMAIL" "$RETRIEVAL_BASE/api/documents" 2>/dev/null || echo "")
if [ -n "$DOC_ID" ] && pycheck "$LIST" "any(x.get('document_id')=='$DOC_ID' for x in d.get('documents',[]))"; then
    pass "uploaded document appears in list"
else
    fail "uploaded document missing from list"
fi

echo ""
echo "[8/8] /api/documents delete"
if [ -n "$DOC_ID" ]; then
    DEL=$(curl -fsS -m 5 -X DELETE -H "$H_EMAIL" \
        "$RETRIEVAL_BASE/api/documents/$DOC_ID" 2>/dev/null || echo "")
    if pycheck "$DEL" "d.get('deleted') is True"; then
        pass "document delete acknowledged"
    else
        fail "document delete did not acknowledge"
    fi
else
    echo "  (skipped — no document id from upload step)"
fi

# ------------------------------------------------------------------------------
# Summary
# ------------------------------------------------------------------------------
echo ""
echo "=============================================="
echo "Results: $PASS passed, $FAIL failed"
echo "=============================================="
if [ "$FAIL" -gt 0 ]; then
    echo "Failed checks:"
    for name in "${FAILED_NAMES[@]}"; do
        echo "  - $name"
    done
    echo ""
    echo "For chat-stream debugging:"
    echo "  docker logs --tail 200 munin-retrieval"
    exit 1
fi
echo "All checks passed."
