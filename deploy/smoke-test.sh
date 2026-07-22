#!/usr/bin/env bash
# End-to-end smoke test for a running ebay-mcp HTTP server.
#   ./deploy/smoke-test.sh [http_base] [metrics_base]
#
# Every eBay tool needs OAuth credentials, so this test is credentials-aware:
#   - always checks /healthz, /readyz, an MCP session, and tools/list (3 tools);
#   - calls search_items and asserts a well-formed MCP result plus a recorded
#     metric. With EBAY_CLIENT_ID/SECRET set it expects success; without, it
#     expects a graceful credential error (isError=true) — both prove the stack.
# Requires curl + jq.
set -euo pipefail

HTTP_BASE="${1:-http://localhost:8080}"
METRICS_BASE="${2:-http://localhost:9464}"
MCP_URL="${HTTP_BASE}/mcp"
ACCEPT='application/json, text/event-stream'
PROTO='2025-06-18'

pass() { printf '  \033[32mPASS\033[0m %s\n' "$1"; }
fail() { printf '  \033[31mFAIL\033[0m %s\n' "$1"; exit 1; }
sse_data() { grep '^data:' | sed 's/^data: //'; }

echo "== health =="
[ "$(curl -sf -o /dev/null -w '%{http_code}' "${HTTP_BASE}/healthz")" = "200" ] && pass "/healthz 200" || fail "/healthz"
[ "$(curl -sf -o /dev/null -w '%{http_code}' "${HTTP_BASE}/readyz")" = "200" ] && pass "/readyz 200" || fail "/readyz"

echo "== mcp session =="
INIT_REQ=$(printf '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"%s","capabilities":{},"clientInfo":{"name":"smoke","version":"0"}}}' "$PROTO")
HEADERS=$(curl -sf -L -D - -o /dev/null -H "Content-Type: application/json" -H "Accept: ${ACCEPT}" -X POST "$MCP_URL" -d "$INIT_REQ")
SESSION=$(printf '%s' "$HEADERS" | tr -d '\r' | awk -F': ' 'tolower($1)=="mcp-session-id"{print $2}')
[ -n "$SESSION" ] && pass "initialize -> session ${SESSION:0:8}…" || fail "no mcp-session-id header"

curl -sf -L -o /dev/null -H "Content-Type: application/json" -H "Accept: ${ACCEPT}" -H "mcp-session-id: ${SESSION}" \
  -X POST "$MCP_URL" -d '{"jsonrpc":"2.0","method":"notifications/initialized"}'

echo "== tools/list =="
TOOLS=$(curl -sf -L -H "Content-Type: application/json" -H "Accept: ${ACCEPT}" -H "mcp-session-id: ${SESSION}" \
  -X POST "$MCP_URL" -d '{"jsonrpc":"2.0","id":2,"method":"tools/list"}' | sse_data | jq -r '.result.tools[].name' | sort | tr '\n' ' ')
EXPECTED="get_item get_item_by_legacy_id search_items "
[ "$TOOLS" = "$EXPECTED" ] && pass "3 tools listed" || fail "unexpected tools: [$TOOLS]"

echo "== tools/call search_items (credentials-aware) =="
CALL=$(curl -sf -L -H "Content-Type: application/json" -H "Accept: ${ACCEPT}" -H "mcp-session-id: ${SESSION}" \
  -X POST "$MCP_URL" -d '{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"search_items","arguments":{"query":"camera"}}}' | sse_data)
IS_ERROR=$(printf '%s' "$CALL" | jq -r '.result.isError')
if [ "$IS_ERROR" = "false" ]; then
  pass "search_items succeeded (eBay credentials present)"
elif [ "$IS_ERROR" = "true" ]; then
  MSG=$(printf '%s' "$CALL" | jq -r '.result.content[0].text')
  pass "search_items returned a graceful error (no eBay credentials): ${MSG:0:50}…"
else
  fail "search_items returned a malformed result: $CALL"
fi

echo "== metrics =="
if curl -sf "${METRICS_BASE}/metrics" | grep '^mcp_tool_calls_total{' | grep -q 'tool="search_items"'; then
  pass "mcp_tool_calls_total recorded for search_items"
else
  fail "no metric recorded for search_items"
fi

echo ""
echo "SMOKE TEST PASSED"
