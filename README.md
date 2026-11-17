# staat-assist-mcp

An MCP server that gives an AI agent (LangGraph) tools for looking up clients, reading their insight
documents and asking CIO questions. Each tool is a typed Python function that calls a backend
directly. Built with [FastMCP](https://gofastmcp.com).

```
agent ──user token──▶ staat-assist-mcp ──▶ Azure AI Search   (contact index)
        (MCP over HTTP)   validates the token, ──▶ STAAT insights    (insight documents)
                          runs the tool,       ──▶ CIO service       (streamed answers)
                          calls the backend        each with its own URL and auth, see config.yaml
```

It replaces the earlier .NET lookup service (`app-lookup-api`) and proxy (`app-proxy`): the contact
search, the split access check and the insight document conversion now live in the lookup tools.

- **Auth**: every request carries the user's Entra ID access token for this server's app registration.
  Each backend is then called the way `config.yaml` says: with a token exchanged for the user
  (On-Behalf-Of, "OBO"), this server's own token, an API key, or the user's token as it is.
- **Transport**: streamable HTTP at `/mcp` (stateless). Health check at `/health`.
- **Streaming**: when a backend streams its answer (SSE), each chunk is passed on to the agent as it
  arrives, and the full text is the tool result.

## Contents

- [Quick start](#quick-start)
- [Tools](#tools)
- [Project layout](#project-layout)
- [Configuration](#configuration)
- [Add a tool](#add-a-tool)
- [Add a backend](#add-a-backend)
- [Try the tools](#try-the-tools)
- [Use from LangGraph](#use-from-langgraph)
- [Troubleshooting](#troubleshooting)

## Quick start

Requirements: [uv](https://docs.astral.sh/uv/) (it installs Python 3.12 and the dependencies).

```bash
cp .env.example .env            # the two secrets: client secret and search key
# edit config.yaml: tenant id, client id, backend URLs and scopes
uv sync                         # install dependencies
uv run staat-assist-mcp         # serves http://localhost:8000/mcp
```

Useful while developing:

```bash
uv run staat-assist-mcp --check   # print the configured backends and tools, then exit
uv run pytest                     # tests (no network, no Entra ID needed)
uv run ruff check && uv run ruff format   # lint and format
```

**Without Entra ID (local only):** set `auth_enabled: false` in `config.yaml` (or `AUTH_ENABLED=false` in `.env`). The server then accepts
requests without a token and calls `obo` and `passthrough` backends without an `Authorization`
header, so it only works with mock or open backends. Never set it in a deployed environment.

## Tools

| Group | Tool | What it does | Backend |
|---|---|---|---|
| lookup | `lookup_contacts` | Search the clients the user may see, by free text and/or client id, first/last name, phone, area code | `contact_search`: Azure AI Search, `POST /docs/search` |
| lookup | `lookup_relationship_id` | The relationship id (ACE id) STAAT insights holds for one client | `staat_insights`: `POST /getRelationshipIdByClientId` |
| lookup | `lookup_insight_document` | One client's insight document (retention pack). `format`: `markdown` (default), `text` (smallest) or `html` (original) | `staat_insights`: `POST /getRelationshipIdByClientId`, then `POST /insightsByRelationshipId` |
| cio | `ask_cio` | Answer a CIO (Chief Investment Office) question; streamed | `cio`: `POST /cio/chat/streaming` |
| | `placeholder_tool` | Does nothing; a template to copy | none |

How the lookup tools decide what a user may see:

- **Contacts**: the user id from the token (`preferred_username`, `upn` or `email`) is looked up in
  `user-splits.json`. The search only returns contacts with an account in one of the user's splits,
  and each contact's accounts are limited to those splits. A user without splits gets an error.
- **Relationship ids and insight documents**: the financial advisor's GPN is sent to STAAT insights,
  which authorizes on it. It is the tool's optional `gpn` argument, or else the token claim named by
  `gpn_claim`; with neither, the tool fails. The argument lets the caller name any GPN, so STAAT insights'
  own check is what stops a user from reading another advisor's clients. Each lookup logs the user, the
  client or relationship id, where the GPN came from, the outcome and the time taken.

Open points:

- The path and request body of `ask_cio` are an assumption (marked `ASSUMPTION` in `tools/cio.py`).
- CRM interaction lookup/create are not built yet: the CRM backend contract is not known. They belong
  in `tools/lookup/crm.py`.

## Project layout

```
config.yaml                   all settings: sign-in, server, logging, lookup, and the backends
.env                          secrets only (copy of .env.example, not committed)
user-splits.json              which user may see which splits (contact lookup)
src/staat_assist_mcp/
  server.py                   entry point: starts the HTTP server
  app.py                      the FastMCP server object (`mcp`) and request logging
  config.py                   all settings and backend fields, loaded by pydantic-settings
  auth.py                     validates the user's token, gets backend tokens, reads the current user
  backend.py                  backend("name").get / .post / .stream: authorizes, logs, maps errors
  sse.py                      how a backend's SSE events become text
  logging_config.py           log format with a request id per tool call
  tools/                      the tools, one module per feature
    lookup/contacts.py          lookup_contacts
    lookup/_contact_search.py     builds the search request, maps index fields to readable names
    lookup/insights.py          lookup_relationship_id, lookup_insight_document
    lookup/_markdown.py           converts the document (HTML or JSON) to markdown
    lookup/_text.py               converts the document (HTML or JSON) to plain text
    lookup/_common.py           split access (user-splits.json) and other shared helpers
    cio.py                      ask_cio
    placeholder.py              template for a new tool
tests/                        pytest tests: tools against mocked backends, auth, config
```

A tool call flows through three files: the tool function in `tools/` calls `backend("name")` in
`backend.py`, which gets a token from `auth.py` and sends the HTTP request.

## Configuration

Read once at startup (restart the server after a change):

- **`config.yaml`**: every setting. Committed, no secrets.
- **`.env`**: the secrets, `AZURE_CLIENT_SECRET` and `BACKENDS__CONTACT_SEARCH__API_KEY`. Not committed.

An environment variable (or a line in `.env`) overrides `config.yaml`: the setting's name in capitals,
e.g. `PORT=9000` or `AUTH_ENABLED=false`; lists are comma-separated. To give an environment its own
file, set `CONFIG_FILE=config.test.yaml`.

### Server settings (`config.yaml`)

| Setting | Default | Meaning |
|---|---|---|
| `auth_enabled` | `true` | `false` turns off token validation and token exchange. Local development only |
| `azure_tenant_id`, `azure_client_id` | required | Entra ID app registration of this server. Its secret is `AZURE_CLIENT_SECRET` in `.env` |
| `required_scopes` | `[access_as_user]` | Scope(s) the user's token must carry |
| `host`, `port` | `0.0.0.0`, `8000` | Where the server listens |
| `base_url` | `http://localhost:8000` | Public URL of this server |
| `cors_origins` | `[]` (off) | Browser origins allowed to call the server, or `["*"]`. Only needed for browser clients such as the MCP Inspector in "Direct" mode (`http://localhost:6274`) |
| `log_level` | `INFO` | `DEBUG` adds tracebacks and `msal`/`httpx` logs |
| `log_payloads` | `false` | Also log tool arguments/results and backend bodies. Contains client data |
| `http_log` | `false` | Log every backend request and response in full: URL, all headers, body, status, time; SSE events one by one. **Tokens and client data included**: test environments only |
| `ssl_verify` | `false` | TLS certificate verification for all outgoing calls. **Off by default**; set `true` where certificates are trusted |
| `user_splits_file` | `user-splits.json` | Who may see which splits: a JSON list of `{"userid": "...", "splits": ["..."]}`. User ids match case-insensitively |
| `gpn_claim` | `gpn` | Token claim that holds the financial advisor's GPN |
| `insights_product` | `FA_PORTAL` | How this app identifies itself to STAAT insights |
| `dev_user_id`, `dev_gpn` | empty | Stand-in user for the lookup tools when `auth_enabled` is `false` |

### Backends (`backends:` in `config.yaml`)

One entry per backend: where it is and how calls to it are authorized.

```yaml
backends:
  contact_search:          # Azure AI Search; the URL ends with the index name
    base_url: https://search-service.search.windows.net/indexes/index-name
    auth: api
    api_key_header: api-key

  staat_insights:
    base_url: https://staat-insights.example.internal/insights-api/v2
    auth: app
    scopes: [api://staat-insights-app-id/.default]

  cio:
    base_url: https://cio.example.internal
    auth: obo
    scopes: [api://cio-app-id/.default]
    timeout_seconds: 60
```

| Field | Default | Meaning |
|---|---|---|
| `base_url` | required | Absolute URL; tool paths are appended to it |
| `auth` | `obo` | How calls are authorized, see below |
| `scopes` | `[]` | For `obo` and `app`: the scope(s) of the backend API, e.g. `api://<backend-app-id>/.default` |
| `api_key` | empty | For `api`: the key. Set it in `.env` as `BACKENDS__<NAME>__API_KEY`, not in the file |
| `api_key_header` | `X-Api-Key` | For `api`: the header the key is sent in |
| `headers` | `{}` | Extra fixed headers on every call |
| `timeout_seconds` | `30` | Timeout of one call |
| `sse` | `auto` | For streaming backends: how events are read, see below |
| `sse_field` | none | With `sse: auto`: the JSON property that holds the text, e.g. `delta` |
| `sse_read_timeout_seconds` | `300` | Longest silence between two events before the stream counts as dead |

**Auth modes**

| `auth` | What the backend receives |
|---|---|
| `obo` | A token for `scopes`, exchanged from the user's token. This server's app registration needs delegated permission and admin consent on the backend API |
| `app` | This server's own token for `scopes` (client credentials). The backend does not see who the user is. The app registration needs an application permission on the backend API |
| `api` | No user token; the API key in `api_key_header` |
| `passthrough` | The user's own token, unchanged. Only for backends that accept tokens issued for this server |
| `none` | No `Authorization` header |

**Overriding one backend field**

Any field of any backend can be set in the environment or `.env` as `BACKENDS__<NAME>__<FIELD>`. This
is where API keys go, and one way to change a URL or scope for a single environment:

```bash
BACKENDS__CONTACT_SEARCH__API_KEY=...
BACKENDS__STAAT_INSIGHTS__SCOPES=api://1234/.default  # several: comma-separated
```

A misspelled field or an invalid value stops startup. A missing setting (`obo` or `app` without scopes, `api`
without a key, an empty `AZURE_*`) does not: the server logs `not set: ...` with the variable names and starts.

**Streaming formats (`sse`)**

- `auto`: each event is plain text, or JSON with the text under `content`, `text`, `delta`, `answer`,
  `message` or `value` (or under `sse_field`). `[DONE]` is skipped.
- `typed`: each event names its kind, e.g. `{"key": "answer", "type": "streaming", "value": "Hello"}`.
  `streaming` values are the answer, `followup` values are suggested next questions, and `start`,
  `status` and `end` are dropped.

## Add a tool

A tool is a Python function with `@mcp.tool`. Put it in a module anywhere under
`src/staat_assist_mcp/tools/`. Modules are imported automatically, so there is nothing to register.

```python
# src/staat_assist_mcp/tools/orders.py
from typing import Annotated

from pydantic import Field

from ..app import mcp
from ..backend import backend


@mcp.tool(tags={"orders"}, annotations={"readOnlyHint": True})
async def orders_get(
    order_id: Annotated[str, Field(description="Order id")],
) -> dict:
    """Get one order by id, with its lines and status."""
    return await backend("orders").get(f"/orders/{order_id}")
```

What each part does:

- **Function name**: the tool name the agent calls.
- **Docstring**: what the LLM reads to decide when to call the tool. Say what it returns and when to use it.
- **Type hints and `Field(description=...)`**: become the input schema; FastMCP validates the arguments.
- **`backend("orders")`**: the `orders` entry under `backends:` in `config.yaml`. It adds the token or API key, logs the
  call and turns HTTP errors into tool errors the agent can read. Use `.get(path, params=...)` and
  `.post(path, json=...)`.
- **`tags`**: the group. Tools of one feature share a folder, a name prefix and a tag, like
  `tools/lookup/` with `lookup_*` tools tagged `lookup`.
- **`readOnlyHint`**: `True` for tools that only read. Leave it out for tools that change data.

For a **streaming (SSE) backend**, add `ctx: Context` as a parameter and use `.stream`; see `tools/cio.py`:

```python
answer = await backend("cio").stream("POST", "/cio/chat/streaming", json={"question": question}, ctx=ctx)
return answer.text          # answer.followups holds suggested next questions (sse: typed)
```

When a tool needs more than passing values through (mapping parameters, checking them, trimming the
result for the LLM), `tools/lookup/contacts.py` is the example to follow. Helper modules that are not
tools start with `_` (they are skipped by the automatic import).

Finally, add a test to `tests/test_tools.py`: an in-memory client calls the tool against a mocked
backend. Copy one of the existing tests.

## Add a backend

1. Add an entry under `backends:` in `config.yaml` (see [Backends](#backends-backends-in-configyaml)).
2. If it uses `auth: api`, put the key in `.env` as `BACKENDS__<NAME>__API_KEY`.
3. In Entra ID, for `auth: obo` give this server's app registration a delegated permission on the
   backend API and grant admin consent; for `auth: app` an application permission.
4. Run `uv run staat-assist-mcp --check` to see that it is picked up.

## Try the tools

MCP has no Swagger page; these are the equivalents:

| Need | Use |
|---|---|
| Browser UI: list tools, fill in a form, call, see results and progress | MCP Inspector |
| One-off calls from the terminal | `fastmcp list` / `fastmcp call` |
| Automated tests without network or auth | in-memory `fastmcp.Client`, see `tests/test_tools.py` |

```bash
# MCP Inspector: transport "Streamable HTTP", URL http://localhost:8000/mcp,
# and the user token under Authentication > Bearer token.
npx @modelcontextprotocol/inspector

# Terminal
TOKEN=<user access token for api://<client-id>/access_as_user>
uv run fastmcp list http://localhost:8000/mcp --auth "$TOKEN"
uv run fastmcp call http://localhost:8000/mcp lookup_contacts last_name=GAVINSKI first_name='JAM*' --auth "$TOKEN"
uv run fastmcp call http://localhost:8000/mcp lookup_relationship_id client_id=<client id> --auth "$TOKEN"
uv run fastmcp call http://localhost:8000/mcp lookup_insight_document client_id=<client id> format=text --auth "$TOKEN"
uv run fastmcp call http://localhost:8000/mcp ask_cio question="What is the house view?" --auth "$TOKEN"
```

## Use from LangGraph

`langchain-mcp-adapters` is archived; MCP support now lives in `langchain.mcp` (beta) on top of a
`fastmcp.Client`. This is not exercised in this repo, so check it against the LangChain version you install:

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

Build the client per user/request so each call carries that user's token. The token must be issued
for this server's app registration, otherwise the exchange for backend tokens fails.

## Troubleshooting

Every log line of one tool call shares a request id, which is also sent to backends as `x-request-id`:

```
INFO [8834d974a5f7] staat_assist_mcp: tool=lookup_contacts start user=jane@example.com oid=... scp=access_as_user
INFO [8834d974a5f7] staat_assist_mcp.backend: backend=contact_search -> POST https://.../indexes/contacts/docs/search params={...}
INFO [8834d974a5f7] staat_assist_mcp.backend: backend=contact_search <- 200 POST ... in 42ms (512 bytes)
INFO [8834d974a5f7] staat_assist_mcp: tool=lookup_contacts ok in 97ms
```

- `log_level: DEBUG`: tracebacks, where the backend token came from (cache or Entra ID), `msal` and `httpx` logs.
- `log_payloads: true`: tool arguments/results, backend bodies and SSE events. Contains client data.
- `http_log: true` (or `HTTP_LOG=true`): every backend request and response in full, logger
  `staat_assist_mcp.http`, lines starting `-->` and `<--`. Bodies are cut at 32K characters.
- Tokens are never logged, except by `http_log`, which logs the `Authorization` and API key headers as sent.

| Symptom | Usual cause |
|---|---|
| 401 on every call | The token's `aud` is not this app (`<client-id>` or `api://<client-id>`), or it is a v1 token (`iss` = `sts.windows.net`); set `accessTokenAcceptedVersion: 2` in the app manifest |
| 401/403 with scope challenge | The token lacks a `required_scopes` value in `scp` |
| Tool error `AADSTS65001` | Admin consent missing for this app on the backend API scope |
| Tool error `AADSTS50013` / `500131` | The incoming token was not issued for this app, so it cannot be exchanged |
| Tool error `AADSTS7000215` | Wrong `AZURE_CLIENT_SECRET` in `.env` |
| `Backend 'x' is not configured` | No `x` entry under `backends:` in `config.yaml` |
| Warning `not set: ...` at startup | Those settings are empty and calls that need them fail. Secrets go in `.env` (copy `.env.example`), the rest in `config.yaml` |
| Startup fails with a validation error | A misspelled field or an invalid value; the error names it |
| `lookup_insight_document` / `lookup_relationship_id`: "token has no GPN claim" | No `gpn` argument, and the user's token for this server lacks the claim named by `gpn_claim`; add it to the token configuration of this server's app registration |
| `lookup_contacts`: "not provisioned for any split" | The user id from the token is not in `user-splits.json` (restart after editing the file) |
