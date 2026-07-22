"""Server wiring tests: tools listed, free tool runs (OAuth mocked), premium gated."""

from __future__ import annotations

import json

import httpx
import respx
import structlog
from mcp.shared.memory import create_connected_server_and_client_session
from mcp_platform_core import (
    ApiKeyRecord,
    InMemoryKeyStore,
    InMemoryRateLimiter,
    InMemoryResponseCache,
    LoggingUsageSink,
    MiddlewareDeps,
    NullMetrics,
    ResilientCaller,
    ToolRegistry,
    build_mcp_server,
)
from mcp_platform_core.server import current_api_key

from ebay_mcp.lib import EBAY_BROWSE_URL, EBAY_OAUTH_URL, EbayLib
from ebay_mcp.tools.browse import (
    make_get_item_by_legacy_id_tool,
    make_get_item_tool,
    make_search_items_tool,
)

EXPECTED_TOOLS = {"search_items", "get_item", "get_item_by_legacy_id"}


def _build(lib: EbayLib):
    keys = InMemoryKeyStore(
        {
            "premium-key": ApiKeyRecord(
                api_key="premium-key", owner="pro", tier="premium", rate_limit_per_minute=100
            )
        }
    )
    deps = MiddlewareDeps(
        key_store=keys,
        rate_limiter=InMemoryRateLimiter(),
        cache=InMemoryResponseCache(),
        usage_sink=LoggingUsageSink(structlog.get_logger()),
        metrics=NullMetrics(),
        logger=structlog.get_logger(),
        resilient=ResilientCaller(),
    )
    registry = ToolRegistry()
    registry.register_all(
        [
            make_search_items_tool(lib),
            make_get_item_tool(lib),
            make_get_item_by_legacy_id_tool(lib),
        ]
    )
    return build_mcp_server(name="ebay-mcp", version="0.1.0", registry=registry, deps=deps)


async def test_lists_three_tools_with_flat_schemas() -> None:
    server = _build(EbayLib(client_id="id", client_secret="s"))
    async with create_connected_server_and_client_session(server) as client:
        result = await client.list_tools()
    tools = {t.name: t for t in result.tools}
    assert set(tools) == EXPECTED_TOOLS
    assert set(tools["search_items"].inputSchema["properties"]) == {"query", "limit"}


@respx.mock
async def test_free_search_runs_through_oauth() -> None:
    respx.post(EBAY_OAUTH_URL).mock(
        return_value=httpx.Response(200, json={"access_token": "T", "expires_in": 7200})
    )
    respx.get(f"{EBAY_BROWSE_URL}/item_summary/search").mock(
        return_value=httpx.Response(
            200,
            json={"total": 1, "itemSummaries": [{"itemId": "v1|1|0", "title": "cam"}]},
        )
    )
    server = _build(EbayLib(client_id="id", client_secret="s"))
    async with create_connected_server_and_client_session(server) as client:
        result = await client.call_tool("search_items", {"query": "camera"})

    assert result.isError is False
    assert json.loads(result.content[0].text)["items"][0]["item_id"] == "v1|1|0"


async def test_missing_credentials_surfaces_as_tool_error() -> None:
    server = _build(EbayLib())  # no client id/secret
    async with create_connected_server_and_client_session(server) as client:
        result = await client.call_tool("search_items", {"query": "camera"})

    # Graceful MCP error (isError), not a crash.
    assert result.isError is True
    message = result.content[0].text.lower()
    assert "ebay_client_id" in message or "credential" in message


async def test_premium_tool_rejected_for_anonymous() -> None:
    server = _build(EbayLib(client_id="id", client_secret="s"))
    async with create_connected_server_and_client_session(server) as client:
        result = await client.call_tool("get_item_by_legacy_id", {"legacy_item_id": "1"})
    assert result.isError is True
    assert "tier" in result.content[0].text.lower()


@respx.mock
async def test_premium_tool_allowed_with_premium_key() -> None:
    respx.post(EBAY_OAUTH_URL).mock(
        return_value=httpx.Response(200, json={"access_token": "T", "expires_in": 7200})
    )
    respx.get(f"{EBAY_BROWSE_URL}/item/get_item_by_legacy_id").mock(
        return_value=httpx.Response(200, json={"itemId": "v1|9|0", "title": "legacy"})
    )
    server = _build(EbayLib(client_id="id", client_secret="s"))
    token = current_api_key.set("premium-key")
    try:
        async with create_connected_server_and_client_session(server) as client:
            result = await client.call_tool("get_item_by_legacy_id", {"legacy_item_id": "9"})
    finally:
        current_api_key.reset(token)

    assert result.isError is False
    assert json.loads(result.content[0].text)["item_id"] == "v1|9|0"
