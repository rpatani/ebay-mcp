"""Embeddable eBay Browse-API client — standalone, no dependency on mcp_platform_core.

Auth is OAuth 2 **client-credentials**: the library fetches an application access
token from eBay using a client id/secret (Basic auth), caches it, and refreshes
it shortly before expiry. Every Browse call needs a valid app token, so eBay
credentials are required for all tools (the free/premium split here is MCP tier
gating, not a keyless free tier).

The MCP tool handlers wrap these calls in ``ctx.resilient.call(...)``; auth/tier/
cache/retry concerns live in the platform, so this stays a pure API client.
"""

from __future__ import annotations

import asyncio
import base64
import time
from collections.abc import Callable
from types import TracebackType
from typing import Any

import httpx

EBAY_OAUTH_URL = "https://api.ebay.com/identity/v1/oauth2/token"
EBAY_BROWSE_URL = "https://api.ebay.com/buy/browse/v1"
DEFAULT_SCOPE = "https://api.ebay.com/oauth/api_scope"
_TOKEN_SAFETY_MARGIN_S = 60.0


class EbayLibError(Exception):
    """Base error for the eBay client."""


class MissingCredentialsError(EbayLibError):
    """Raised when a call is made without eBay client credentials. Never retried."""


class EbayLib:
    def __init__(
        self,
        client: httpx.AsyncClient | None = None,
        *,
        client_id: str | None = None,
        client_secret: str | None = None,
        marketplace_id: str = "EBAY_US",
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._client = client or httpx.AsyncClient(timeout=httpx.Timeout(10.0))
        self._owns_client = client is None
        self._client_id = client_id
        self._client_secret = client_secret
        self._marketplace = marketplace_id
        self._clock = clock
        self._token: str | None = None
        self._token_expiry = 0.0
        self._lock = asyncio.Lock()

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def __aenter__(self) -> EbayLib:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.aclose()

    # ---- OAuth client-credentials token lifecycle ---------------------------

    async def _ensure_token(self) -> str:
        if self._token is not None and self._clock() < self._token_expiry:
            return self._token
        async with self._lock:
            # Re-check under the lock: a concurrent caller may have refreshed it.
            if self._token is not None and self._clock() < self._token_expiry:
                return self._token
            if not (self._client_id and self._client_secret):
                raise MissingCredentialsError(
                    "eBay tools require EBAY_CLIENT_ID and EBAY_CLIENT_SECRET to be set"
                )
            basic = base64.b64encode(f"{self._client_id}:{self._client_secret}".encode()).decode()
            response = await self._client.post(
                EBAY_OAUTH_URL,
                headers={
                    "Authorization": f"Basic {basic}",
                    "Content-Type": "application/x-www-form-urlencoded",
                },
                data={"grant_type": "client_credentials", "scope": DEFAULT_SCOPE},
            )
            response.raise_for_status()
            data = response.json()
            self._token = data["access_token"]
            expires_in = float(data.get("expires_in", 7200))
            self._token_expiry = self._clock() + max(expires_in - _TOKEN_SAFETY_MARGIN_S, 0.0)
            return self._token

    async def _browse_get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        token = await self._ensure_token()
        response = await self._client.get(
            f"{EBAY_BROWSE_URL}{path}",
            params=params,
            headers={
                "Authorization": f"Bearer {token}",
                "X-EBAY-C-MARKETPLACE-ID": self._marketplace,
            },
        )
        response.raise_for_status()
        result: dict[str, Any] = response.json()
        return result

    # ---- Browse API ---------------------------------------------------------

    @staticmethod
    def _summary(row: dict[str, Any]) -> dict[str, Any]:
        price = row.get("price") or {}
        seller = row.get("seller") or {}
        return {
            "item_id": row.get("itemId"),
            "title": row.get("title"),
            "price": price.get("value"),
            "currency": price.get("currency"),
            "seller": seller.get("username"),
            "url": row.get("itemWebUrl"),
        }

    async def search_items(self, query: str, limit: int = 5) -> dict[str, Any]:
        data = await self._browse_get("/item_summary/search", {"q": query, "limit": limit})
        return {
            "query": query,
            "total": data.get("total"),
            "items": [self._summary(r) for r in data.get("itemSummaries", [])],
        }

    async def get_item(self, item_id: str) -> dict[str, Any]:
        data = await self._browse_get(f"/item/{item_id}")
        return self._item_detail(data)

    async def get_item_by_legacy_id(self, legacy_item_id: str) -> dict[str, Any]:
        data = await self._browse_get(
            "/item/get_item_by_legacy_id", {"legacy_item_id": legacy_item_id}
        )
        return self._item_detail(data)

    @staticmethod
    def _item_detail(data: dict[str, Any]) -> dict[str, Any]:
        price = data.get("price") or {}
        seller = data.get("seller") or {}
        return {
            "item_id": data.get("itemId"),
            "title": data.get("title"),
            "price": price.get("value"),
            "currency": price.get("currency"),
            "condition": data.get("condition"),
            "seller": seller.get("username"),
            "url": data.get("itemWebUrl"),
        }
