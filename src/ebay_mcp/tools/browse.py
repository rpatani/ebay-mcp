"""Browse-API tools: search_items, get_item (free) and get_item_by_legacy_id (premium)."""

from __future__ import annotations

from typing import Any

from mcp_platform_core import ToolContext, ToolDefinition
from pydantic import BaseModel, Field

from ebay_mcp.lib import EbayLib

_60S_MS = 60 * 1000
_5MIN_MS = 5 * 60 * 1000


class SearchItemsInput(BaseModel):
    query: str = Field(description="Search keywords, e.g. 'vintage camera'.")
    limit: int = Field(default=5, ge=1, le=20, description="Max results (1-20).")


class GetItemInput(BaseModel):
    item_id: str = Field(description="eBay Browse item id, e.g. 'v1|123456789|0'.")


class GetItemByLegacyIdInput(BaseModel):
    legacy_item_id: str = Field(description="Legacy numeric eBay item id, e.g. '123456789'.")


def make_search_items_tool(lib: EbayLib) -> ToolDefinition:
    async def handler(args: SearchItemsInput, ctx: ToolContext) -> dict[str, Any]:
        return await ctx.resilient.call("ebay", lambda: lib.search_items(args.query, args.limit))

    return ToolDefinition(
        name="search_items",
        description="Search eBay listings by keyword via the Browse API. Free tier.",
        input_model=SearchItemsInput,
        min_tier="free",
        cost_units=1,
        cache_ttl_ms=_60S_MS,
        handler=handler,
    )


def make_get_item_tool(lib: EbayLib) -> ToolDefinition:
    async def handler(args: GetItemInput, ctx: ToolContext) -> dict[str, Any]:
        return await ctx.resilient.call("ebay", lambda: lib.get_item(args.item_id))

    return ToolDefinition(
        name="get_item",
        description="Get details (title, price, condition, seller) for an eBay item id. Free tier.",
        input_model=GetItemInput,
        min_tier="free",
        cost_units=1,
        cache_ttl_ms=_5MIN_MS,
        handler=handler,
    )


def make_get_item_by_legacy_id_tool(lib: EbayLib) -> ToolDefinition:
    async def handler(args: GetItemByLegacyIdInput, ctx: ToolContext) -> dict[str, Any]:
        return await ctx.resilient.call(
            "ebay", lambda: lib.get_item_by_legacy_id(args.legacy_item_id)
        )

    return ToolDefinition(
        name="get_item_by_legacy_id",
        description=(
            "Get item details by a legacy numeric eBay item id via the Browse API. Premium tier."
        ),
        input_model=GetItemByLegacyIdInput,
        min_tier="premium",
        cost_units=3,
        cache_ttl_ms=_5MIN_MS,
        handler=handler,
    )
