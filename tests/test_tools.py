"""End-to-end tool tests: in-memory MCP client -> tool -> BackendClient -> mocked backend."""

import httpx
import pytest
import respx
from fastmcp import Client
from fastmcp.exceptions import ToolError

from staat_assist_mcp.server import mcp


@respx.mock
async def test_lookup_client_single_match(fake_obo):
    route = respx.get("http://someservice/find-contact", params={"email": "a@b.com"}).mock(
        return_value=httpx.Response(200, json={"id": "C1", "name": "Alice"})
    )
    async with Client(mcp) as client:
        result = await client.call_tool("lookup_client", {"email": "a@b.com"})

    assert result.data == {"email": "a@b.com", "match_count": 1, "clients": [{"id": "C1", "name": "Alice"}]}
    assert route.calls.last.request.headers["authorization"] == "Bearer obo-token"
    assert route.calls.last.request.headers["x-request-id"] != "-"
    assert fake_obo == ["api://backend/.default"]


@respx.mock
async def test_lookup_client_multiple_matches(fake_obo):
    respx.get("http://someservice/find-contact").mock(
        return_value=httpx.Response(200, json={"contacts": [{"id": "C1"}, {"id": "C2"}]})
    )
    async with Client(mcp) as client:
        result = await client.call_tool("lookup_client", {"email": "shared@b.com"})

    assert result.data["match_count"] == 2
    assert [c["id"] for c in result.data["clients"]] == ["C1", "C2"]


@respx.mock
async def test_lookup_client_backend_error(fake_obo):
    respx.get("http://someservice/find-contact").mock(return_value=httpx.Response(503, text="upstream down"))
    async with Client(mcp) as client:
        with pytest.raises(ToolError, match="HTTP 503.*upstream down"):
            await client.call_tool("lookup_client", {"email": "a@b.com"})


@respx.mock
async def test_ask_cio_streams_progress_and_returns_full_answer(fake_obo):
    sse = 'data: {"content": "Equities "}\n\ndata: {"content": "look "}\n\ndata: attractive.\n\ndata: [DONE]\n\n'
    route = respx.post("http://someservice/cio/chat/streaming").mock(
        return_value=httpx.Response(200, headers={"content-type": "text/event-stream"}, content=sse.encode())
    )
    progress: list[str | None] = []

    async def on_progress(progress_value, total, message):
        progress.append(message)

    async with Client(mcp) as client:
        result = await client.call_tool("ask_cio", {"question": "Outlook?"}, progress_handler=on_progress)

    assert result.data == "Equities look attractive."
    assert progress == ["Equities ", "look ", "attractive."]
    assert route.calls.last.request.headers["authorization"] == "Bearer obo-token"


async def test_tools_are_listed():
    async with Client(mcp) as client:
        names = {tool.name for tool in await client.list_tools()}
    assert {"lookup_client", "ask_cio", "placeholder_tool"} <= names


@respx.mock
async def test_auth_disabled_calls_backend_without_token(monkeypatch, fake_obo):
    from staat_assist_mcp.config import get_settings

    monkeypatch.setattr(get_settings(), "auth_enabled", False)
    route = respx.get("http://someservice/find-contact").mock(return_value=httpx.Response(200, json=[]))
    async with Client(mcp) as client:
        result = await client.call_tool("lookup_client", {"email": "a@b.com"})

    assert result.data["match_count"] == 0
    assert "authorization" not in route.calls.last.request.headers
    assert fake_obo == []
