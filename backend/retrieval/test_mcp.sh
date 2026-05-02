#!/bin/bash
# ==============================================================================
# MCP Tooling Test Script
# ==============================================================================
# Tests the MCP endpoints after deployment
# Usage: ./test_mcp.sh [host:port]
# ==============================================================================

HOST="${1:-localhost:8080}"
BASE_URL="http://$HOST"

echo "=============================================="
echo "MCP Tooling Test Script"
echo "Testing: $BASE_URL"
echo "=============================================="
echo ""

# Color codes
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

pass() { echo -e "${GREEN}[PASS]${NC} $1"; }
fail() { echo -e "${RED}[FAIL]${NC} $1"; }
warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }

# ------------------------------------------------------------------------------
# Test 1: Health Check
# ------------------------------------------------------------------------------
echo "1. Health Check"
HEALTH=$(curl -s "$BASE_URL/health" 2>/dev/null)
if echo "$HEALTH" | grep -q '"status"'; then
    pass "Service is running"
    echo "   Response: $HEALTH" | head -c 200
    echo ""
else
    fail "Service not responding"
    echo "   Make sure the container is running:"
    echo "   sudo docker compose --profile rag up -d retrieval"
    exit 1
fi
echo ""

# ------------------------------------------------------------------------------
# Test 2: List MCP Tools
# ------------------------------------------------------------------------------
echo "2. List MCP Tools"
TOOLS=$(curl -s "$BASE_URL/mcp/tools" 2>/dev/null)
if echo "$TOOLS" | grep -q '"tools"'; then
    TOOL_COUNT=$(echo "$TOOLS" | grep -o '"name"' | wc -l)
    pass "MCP tools endpoint working ($TOOL_COUNT tools)"
    echo "   Available tools:"
    echo "$TOOLS" | grep -o '"name": "[^"]*"' | sed 's/"name": "/ - /g' | sed 's/"//g'
else
    fail "MCP tools endpoint not working"
    echo "   Response: $TOOLS"
fi
echo ""

# ------------------------------------------------------------------------------
# Test 3: MCP JSON-RPC Initialize
# ------------------------------------------------------------------------------
echo "3. MCP JSON-RPC Initialize"
INIT=$(curl -s -X POST "$BASE_URL/mcp/messages" \
    -H "Content-Type: application/json" \
    -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}' 2>/dev/null)
if echo "$INIT" | grep -q '"protocolVersion"'; then
    pass "MCP initialize working"
else
    fail "MCP initialize failed"
    echo "   Response: $INIT"
fi
echo ""

# ------------------------------------------------------------------------------
# Test 4: MCP Tools List via JSON-RPC
# ------------------------------------------------------------------------------
echo "4. MCP Tools List (JSON-RPC)"
TOOLS_RPC=$(curl -s -X POST "$BASE_URL/mcp/messages" \
    -H "Content-Type: application/json" \
    -d '{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}' 2>/dev/null)
if echo "$TOOLS_RPC" | grep -q '"tools"'; then
    pass "MCP tools/list working"
else
    fail "MCP tools/list failed"
    echo "   Response: $TOOLS_RPC"
fi
echo ""

# ------------------------------------------------------------------------------
# Test 5: Web Search Tool (via REST)
# ------------------------------------------------------------------------------
echo "5. Web Search Tool (REST)"
WEB_SEARCH=$(curl -s -X POST "$BASE_URL/mcp/call" \
    -H "Content-Type: application/json" \
    -d '{"name":"web_search","arguments":{"query":"test","top_k":1}}' 2>/dev/null)
if echo "$WEB_SEARCH" | grep -q '"results"'; then
    pass "web_search tool working"
elif echo "$WEB_SEARCH" | grep -q '"error"'; then
    warn "web_search returned error (SearXNG may be offline)"
    echo "   Response: $WEB_SEARCH" | head -c 200
else
    fail "web_search failed"
    echo "   Response: $WEB_SEARCH"
fi
echo ""

# ------------------------------------------------------------------------------
# Test 6: Paper Search Tool (via REST)
# ------------------------------------------------------------------------------
echo "6. Paper Search Tool (REST)"
PAPER_SEARCH=$(curl -s -X POST "$BASE_URL/mcp/call" \
    -H "Content-Type: application/json" \
    -d '{"name":"paper_search","arguments":{"query":"neural networks","top_k":1}}' 2>/dev/null)
if echo "$PAPER_SEARCH" | grep -q '"results"'; then
    RESULT_COUNT=$(echo "$PAPER_SEARCH" | grep -o '"title"' | wc -l)
    pass "paper_search tool working ($RESULT_COUNT results)"
elif echo "$PAPER_SEARCH" | grep -q '"error"'; then
    warn "paper_search returned error (Qdrant may be offline or empty)"
    echo "   Response: $PAPER_SEARCH" | head -c 200
else
    fail "paper_search failed"
    echo "   Response: $PAPER_SEARCH"
fi
echo ""

# ------------------------------------------------------------------------------
# Test 7: MCP SSE Endpoint (POST for Streamable HTTP)
# ------------------------------------------------------------------------------
echo "7. MCP SSE Endpoint (POST)"
SSE_POST=$(curl -s -X POST "$BASE_URL/mcp/sse" \
    -H "Content-Type: application/json" \
    -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}' 2>/dev/null)
if echo "$SSE_POST" | grep -q '"protocolVersion"'; then
    pass "MCP SSE POST endpoint working (for Open WebUI)"
else
    fail "MCP SSE POST endpoint failed"
    echo "   Response: $SSE_POST"
fi
echo ""

# ------------------------------------------------------------------------------
# Summary
# ------------------------------------------------------------------------------
echo "=============================================="
echo "Test Complete"
echo "=============================================="
echo ""
echo "If all tests pass, MCP tooling is ready for:"
echo "  - Open WebUI: Add MCP server at $BASE_URL/mcp/sse"
echo "  - Deep Research: Tools call $BASE_URL/mcp/call"
echo ""
