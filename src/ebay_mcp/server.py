"""ebay-mcp entrypoint: wire the three tools + core deps + transport.

Run with ``uv run ebay-mcp``. All tools use the eBay Browse API, which requires
an OAuth client-credentials app token; EBAY_CLIENT_ID/EBAY_CLIENT_SECRET are read
here (app-level), never in core. Transport/keys/metrics are driven by env via
CoreConfig.
"""

from __future__ import annotations

import asyncio
import os

from mcp_platform_core import (
    CoreConfig,
    InMemoryRateLimiter,
    InMemoryResponseCache,
    LoggingUsageSink,
    MiddlewareDeps,
    ResilientCaller,
    ToolRegistry,
    build_mcp_server,
    build_metrics,
    create_logger,
    load_key_store,
    run_http,
    run_stdio,
)

from ebay_mcp.lib import EbayLib
from ebay_mcp.tools.browse import (
    make_get_item_by_legacy_id_tool,
    make_get_item_tool,
    make_search_items_tool,
)

SERVICE_NAME = "ebay-mcp"
SERVICE_VERSION = "0.1.0"


def main() -> None:
    config = CoreConfig()
    log = create_logger(
        service=SERVICE_NAME,
        version=SERVICE_VERSION,
        transport=config.transport,
        level=config.log_level,
    )
    metrics = build_metrics(config.metrics_backend, enabled=config.metrics_enabled)
    deps = MiddlewareDeps(
        key_store=load_key_store(config.keys_file),
        rate_limiter=InMemoryRateLimiter(),
        cache=InMemoryResponseCache(),
        usage_sink=LoggingUsageSink(log),
        metrics=metrics,
        logger=log,
        resilient=ResilientCaller(
            metrics=metrics,
            timeout_s=config.upstream_timeout_s,
            retries=config.upstream_retries,
            breaker_threshold=config.breaker_threshold,
            breaker_cooldown_s=config.breaker_cooldown_s,
        ),
    )

    # eBay OAuth credentials are app-level secrets, read here — never in core.
    lib = EbayLib(
        client_id=os.environ.get("EBAY_CLIENT_ID"),
        client_secret=os.environ.get("EBAY_CLIENT_SECRET"),
        marketplace_id=os.environ.get("EBAY_MARKETPLACE_ID", "EBAY_US"),
    )

    registry = ToolRegistry()
    registry.register_all(
        [
            make_search_items_tool(lib),
            make_get_item_tool(lib),
            make_get_item_by_legacy_id_tool(lib),
        ]
    )

    server = build_mcp_server(
        name=SERVICE_NAME, version=SERVICE_VERSION, registry=registry, deps=deps
    )

    async def _serve() -> None:
        try:
            if config.transport == "stdio":
                await run_stdio(server, api_key=config.api_key, log=log)
            else:
                await run_http(
                    server,
                    port=config.http_port,
                    mcp_path=config.http_path,
                    metrics=metrics,
                    metrics_port=config.metrics_port,
                    log=log,
                )
        finally:
            await lib.aclose()

    asyncio.run(_serve())


if __name__ == "__main__":
    main()
