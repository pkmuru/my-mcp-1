# staat-assist-mcp

FastMCP server that exposes backend APIs as MCP tools for a LangGraph ReAct agent.

- **Auth**: every request needs the user's Entra ID (MSAL) access token for this server's app
  registration. The server exchanges it for backend tokens with the On-Behalf-Of (OBO) flow.
- **Transport**: streamable HTTP at `/mcp` (stateless), health check at `/health`.
- **SSE backends**: chunks are relayed as MCP progress notifications; the full text is the tool result.
- **TLS verification is off by default** (`SSL_VERIFY=false`) for backends, Entra and JWKS calls.

## Run

```bash
cp .env.example .env      # fill in tenant, client id, secret, backends
uv sync
uv run staat-assist-mcp   # http://localhost:8000/mcp
uv run pytest
```

### Running without auth (local dev)

`AUTH_ENABLED=false` turns off token validation **and** the OBO exchange: anyone who can reach the
port can call the tools, and backends are called without an `Authorization` header. The `AZURE_*`
settings are then optional. Use it with mock or unauthenticated backends, e.g. to try tools in the
MCP Inspector or with `fastmcp call` without a token. Never set it in a deployed environment.

## Add a tool

Create a module in `src/staat_assist_mcp/tools/` and add it to `tools/__init__.py`
(`tools/placeholder.py` is a template):

```python
from ..app import mcp
from ..backend import backend


@mcp.tool
async def get_account(account_id: str) -> dict:
    """Fetch account details by id."""  # the LLM reads this
    return await backend("someservice").get(f"/accounts/{account_id}")
```

`backend("<name>")` reads `BACKENDS__<NAME>__BASE_URL` / `__SCOPE`, gets the OBO token, logs the call
and turns HTTP errors into tool errors. For SSE endpoints use
`await backend("x").stream_sse("POST", "/path", json={...}, ctx=ctx)` with `ctx: Context` as a tool
parameter; pass `extract=` if the event payload is not plain text or `{"content": "..."}`.

Sample tools: `lookup_client` (`GET /find-contact`), `ask_cio` (SSE `POST /cio/chat/streaming`),
`placeholder_tool`. The request/response shapes of the two backend calls are assumptions, marked
`ASSUMPTION` in the code.

## Test tools interactively

MCP has no Swagger/Scalar page; the equivalents are:

| Need | Tool |
|---|---|
| Browser UI: list tools, fill a form, call, see results, progress and logs (the "Swagger UI" of MCP) | **MCP Inspector** |
| One-off calls from the terminal (the "curl" of MCP) | **`fastmcp list` / `fastmcp call`** |
| Automated tests without network or auth | **in-memory `fastmcp.Client`** (see `tests/test_tools.py`) |

```bash
# 1. MCP Inspector: choose transport "Streamable HTTP", URL http://localhost:8000/mcp,
#    and paste the user token under Authentication > Bearer token.
npx @modelcontextprotocol/inspector

# 2. CLI
TOKEN=<user access token for api://<client-id>/access_as_user>
uv run fastmcp list http://localhost:8000/mcp --auth "$TOKEN"
uv run fastmcp call http://localhost:8000/mcp lookup_client email=jane@example.com --auth "$TOKEN"
uv run fastmcp call http://localhost:8000/mcp ask_cio question="What is the house view?" --auth "$TOKEN"
```

## Use from LangGraph

`langchain-mcp-adapters` is archived; MCP support now lives in `langchain.mcp` (beta) on top of a
`fastmcp.Client`. Not exercised in this repo, so check it against the LangChain version you install:

```python
from fastmcp import Client
from langchain.agents import create_agent
from langchain.mcp import MCPAdapter
from langgraph.config import get_stream_writer


async def on_progress(progress, total, message):
    get_stream_writer()({"tool_chunk": message})  # arrives with stream_mode="custom"


client = Client(MCP_URL, auth=user_access_token, verify=False, progress_handler=on_progress)
async with MCPAdapter(client) as adapter:
    agent = create_agent(model, await adapter.list_tools())
```

Build the client per user/request so each call carries that user's token.

## Troubleshooting

Every log line of one tool call shares a request id (also sent to backends as `x-request-id`):

```
INFO [8834d974a5f7] staat_assist_mcp: tool=lookup_client start user=jane@example.com oid=... scp=access_as_user
INFO [8834d974a5f7] staat_assist_mcp.backend: backend=someservice -> GET http://someservice/find-contact params={...}
INFO [8834d974a5f7] staat_assist_mcp.backend: backend=someservice <- 200 GET ... in 42ms (512 bytes)
INFO [8834d974a5f7] staat_assist_mcp: tool=lookup_client ok in 97ms
```

- `LOG_LEVEL=DEBUG`: tracebacks, OBO cache source, `msal` and `httpx` logs.
- `LOG_PAYLOADS=true`: tool arguments/results, backend bodies and SSE events (contains client data).
- Tokens are never logged.

| Symptom | Usual cause |
|---|---|
| 401 on every call | Token `aud` is not this app (`<client-id>` or `api://<client-id>`), or it is a v1 token (`iss` = `sts.windows.net`); set `accessTokenAcceptedVersion: 2` in the app manifest |
| 401/403 with scope challenge | Token lacks the `REQUIRED_SCOPES` value in `scp` |
| Tool error `AADSTS65001` | Admin consent missing for this app on the backend API scope |
| Tool error `AADSTS50013` / `500131` | Incoming token was not issued for this app, so it cannot be used as OBO assertion |
| Tool error `AADSTS7000215` | Wrong `AZURE_CLIENT_SECRET` |
| `Backend 'x' is not configured` | Missing `BACKENDS__X__BASE_URL` |
