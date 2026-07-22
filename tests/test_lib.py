"""Tests for the standalone EbayLib — focus on the OAuth token lifecycle."""

from __future__ import annotations

import base64

import httpx
import pytest
import respx

from ebay_mcp.lib import (
    EBAY_BROWSE_URL,
    EBAY_OAUTH_URL,
    EbayLib,
    MissingCredentialsError,
)


class FakeClock:
    def __init__(self, start: float = 1000.0) -> None:
        self._now = start

    def __call__(self) -> float:
        return self._now

    def advance(self, seconds: float) -> None:
        self._now += seconds


def _token_response(token: str = "APP-TOKEN", expires_in: int = 7200) -> httpx.Response:
    return httpx.Response(
        200, json={"access_token": token, "expires_in": expires_in, "token_type": "Application"}
    )


async def test_missing_credentials_raises() -> None:
    async with EbayLib() as lib:
        with pytest.raises(MissingCredentialsError):
            await lib.search_items("camera")


@respx.mock
async def test_token_fetched_with_basic_auth_then_browse_uses_bearer() -> None:
    token_route = respx.post(EBAY_OAUTH_URL).mock(return_value=_token_response("TOK1"))
    search_route = respx.get(f"{EBAY_BROWSE_URL}/item_summary/search").mock(
        return_value=httpx.Response(
            200,
            json={
                "total": 1,
                "itemSummaries": [
                    {
                        "itemId": "v1|1|0",
                        "title": "Vintage camera",
                        "price": {"value": "50.00", "currency": "USD"},
                        "seller": {"username": "cams"},
                        "itemWebUrl": "https://ebay.com/itm/1",
                    }
                ],
            },
        )
    )

    async with EbayLib(client_id="id", client_secret="secret") as lib:
        result = await lib.search_items("camera")

    # token request used HTTP Basic auth over client_id:client_secret
    expected_basic = base64.b64encode(b"id:secret").decode()
    assert token_route.calls.last.request.headers["authorization"] == f"Basic {expected_basic}"
    # browse request used the Bearer app token + marketplace header
    browse_req = search_route.calls.last.request
    assert browse_req.headers["authorization"] == "Bearer TOK1"
    assert browse_req.headers["x-ebay-c-marketplace-id"] == "EBAY_US"
    assert result["items"][0]["price"] == "50.00"


@respx.mock
async def test_token_is_cached_across_calls() -> None:
    token_route = respx.post(EBAY_OAUTH_URL).mock(return_value=_token_response())
    respx.get(f"{EBAY_BROWSE_URL}/item_summary/search").mock(
        return_value=httpx.Response(200, json={"total": 0, "itemSummaries": []})
    )

    async with EbayLib(client_id="id", client_secret="secret") as lib:
        await lib.search_items("a")
        await lib.search_items("b")
        await lib.search_items("c")

    assert token_route.call_count == 1  # fetched once, reused


@respx.mock
async def test_token_refreshed_after_expiry() -> None:
    clock = FakeClock()
    token_route = respx.post(EBAY_OAUTH_URL).mock(return_value=_token_response(expires_in=7200))
    respx.get(f"{EBAY_BROWSE_URL}/item_summary/search").mock(
        return_value=httpx.Response(200, json={"total": 0, "itemSummaries": []})
    )

    async with EbayLib(client_id="id", client_secret="secret", clock=clock) as lib:
        await lib.search_items("a")  # fetch #1
        clock.advance(7100)  # still valid (expiry ~ 7200 - 60 margin = 7140)
        await lib.search_items("b")  # reuse
        assert token_route.call_count == 1
        clock.advance(200)  # now past expiry
        await lib.search_items("c")  # fetch #2

    assert token_route.call_count == 2


@respx.mock
async def test_get_item_maps_detail() -> None:
    respx.post(EBAY_OAUTH_URL).mock(return_value=_token_response())
    respx.get(f"{EBAY_BROWSE_URL}/item/v1%7C123%7C0").mock(
        return_value=httpx.Response(
            200,
            json={
                "itemId": "v1|123|0",
                "title": "A thing",
                "price": {"value": "9.99", "currency": "USD"},
                "condition": "New",
                "seller": {"username": "shop"},
                "itemWebUrl": "https://ebay.com/itm/123",
            },
        )
    )

    async with EbayLib(client_id="id", client_secret="secret") as lib:
        result = await lib.get_item("v1|123|0")

    assert result["condition"] == "New"
    assert result["price"] == "9.99"


@respx.mock
async def test_get_item_by_legacy_id_sends_param() -> None:
    respx.post(EBAY_OAUTH_URL).mock(return_value=_token_response())
    route = respx.get(f"{EBAY_BROWSE_URL}/item/get_item_by_legacy_id").mock(
        return_value=httpx.Response(200, json={"itemId": "v1|9|0", "title": "legacy"})
    )

    async with EbayLib(client_id="id", client_secret="secret") as lib:
        result = await lib.get_item_by_legacy_id("123456789")

    assert route.calls.last.request.url.params["legacy_item_id"] == "123456789"
    assert result["item_id"] == "v1|9|0"


@respx.mock
async def test_browse_http_error_propagates() -> None:
    respx.post(EBAY_OAUTH_URL).mock(return_value=_token_response())
    respx.get(f"{EBAY_BROWSE_URL}/item_summary/search").mock(return_value=httpx.Response(500))

    async with EbayLib(client_id="id", client_secret="secret") as lib:
        with pytest.raises(httpx.HTTPStatusError):
            await lib.search_items("x")
