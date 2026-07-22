# ebay-mcp

An eBay **MCP server** (Browse API) built on
[`mcp-platform-core`](https://github.com/your-org/mcp-platform-py) — consumes the
core as an external, version-pinned library and adds only eBay tools. It
demonstrates the **OAuth 2 client-credentials** auth style: the app fetches an
application access token from eBay, caches it, and refreshes it before expiry.

## Tools

| Tool | Endpoint | Tier | Cache TTL | Cost |
|---|---|---|---|---|
| `search_items` | Browse `/item_summary/search` | free | 60 s | 1 |
| `get_item` | Browse `/item/{id}` | free | 5 min | 1 |
| `get_item_by_legacy_id` | Browse `/get_item_by_legacy_id` | premium | 5 min | 3 |

> Every eBay tool needs an OAuth app token, so **all** tools require
> `EBAY_CLIENT_ID` / `EBAY_CLIENT_SECRET` to actually return data. The free vs.
> premium split here is MCP tier gating, not a keyless free tier.

## Auth flow

`EbayLib` performs the client-credentials grant: `POST /identity/v1/oauth2/token`
with HTTP Basic auth over `client_id:client_secret`, caches the returned
`access_token`, and refreshes it ~60 s before `expires_in`. Browse calls send
`Authorization: Bearer <app_token>` and `X-EBAY-C-MARKETPLACE-ID`.

## Core dependency

Pinned via git tag in `pyproject.toml`. Local dev resolves from the local
`mcp-platform-py` repo; on push, change the one `git = "file://…"` line to the
GitHub URL.

## Run locally (Mac)

```bash
uv sync

# Set eBay credentials (from https://developer.ebay.com/ my-account/keys)
export EBAY_CLIENT_ID=... EBAY_CLIENT_SECRET=...

# stdio
MCP_TRANSPORT=stdio uv run ebay-mcp

# HTTP
MCP_TRANSPORT=http MCP_HTTP_PORT=8080 MCP_KEYS_FILE=keys.example.json uv run ebay-mcp

# smoke test (credentials-aware: passes with or without eBay creds)
./deploy/smoke-test.sh http://localhost:8080 http://localhost:9464
```

Without eBay credentials the server still starts, lists tools, and returns a
graceful credential error for tool calls (verified by the smoke test).
`get_item_by_legacy_id` additionally requires a `premium`-tier key
(`Authorization: Bearer premium-demo-key`).

## Tests & gates

```bash
uv run pytest    # includes OAuth token fetch/cache/refresh coverage
uv run ruff check . && uv run mypy .
uv run pip-audit && uv run bandit -r src
```

## Docker

`docker compose -f deploy/docker-compose.yml up --build` — the image build pins
core from git, so switch the local `file://` source to a reachable remote before
building. For local dev use `uv run`.
