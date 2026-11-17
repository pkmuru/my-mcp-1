"""End-to-end tool tests: in-memory MCP client -> tool -> BackendClient -> mocked backend."""

import json

import httpx
import pytest
import respx
from fastmcp import Client
from fastmcp.exceptions import ToolError

from staat_assist_mcp.server import mcp

SEARCH_URL = "http://search/indexes/contacts/docs/search"
RELATIONSHIP_URL = "http://insights/v2/getRelationshipIdByClientId"
DOCUMENT_URL = "http://insights/v2/insightsByRelationshipId"


@respx.mock
async def test_lookup_contacts_searches_the_index_within_the_users_splits(signed_in):
    route = respx.post(SEARCH_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "@odata.count": 7,
                "value": [
                    {
                        "@search.score": 1.5,
                        "ip_cli_i": "CL1",
                        "ip_frst_x": "JAMES",
                        "ip_midl_x": None,
                        "emailAddresses": [],
                        "phones": [{"tel_area_c": "212", "tel_n": "5550100", "tel_extnn_n": None}],
                        "faInfoList": [
                            {"prd_ow_i": "ab12", "acc_n": "X1", "acc_stat_c": ""},
                            {"prd_ow_i": "ZZ99", "acc_n": "X2", "acc_stat_c": "A"},
                        ],
                    }
                ],
            },
        )
    )
    async with Client(mcp) as client:
        result = await client.call_tool(
            "lookup_contacts", {"first_name": " jam* ", "last_name": "O'Neil", "phone": "555-0100", "top": 5}
        )

    request = route.calls.last.request
    assert request.headers["api-key"] == "search-key"
    assert "authorization" not in request.headers
    assert request.url.params["api-version"]
    assert json.loads(request.content) == {
        "search": "ip_frst_x_uc:(JAM*) AND ip_last_x_uc:(O'NEIL) AND phones/tel_n:(5550100)",
        "queryType": "full",
        "searchMode": "all",
        "filter": "faInfoList/any(a: search.in(a/prd_ow_i, 'AB12,CD34', ','))",
        "top": 5,
        "skip": 0,
        "count": True,
    }
    assert result.data == {
        "total": 7,
        "returned": 1,
        "skip": 0,
        "contacts": [
            {
                "clientId": "CL1",
                "firstName": "JAMES",
                "phones": [{"areaCode": "212", "number": "5550100"}],
                "accounts": [{"split": "ab12", "accountNumber": "X1"}],  # ZZ99 is not the user's split
            }
        ],
    }


@respx.mock
async def test_lookup_contacts_free_text(signed_in):
    route = respx.post(SEARCH_URL).mock(
        return_value=httpx.Response(200, json={"@odata.count": 0, "value": []})
    )
    async with Client(mcp) as client:
        result = await client.call_tool("lookup_contacts", {"text": "james gavinski"})

    body = json.loads(route.calls.last.request.content)
    assert body["search"] == "james gavinski" and body["queryType"] == "simple" and body["top"] == 10
    assert result.data == {"total": 0, "returned": 0, "skip": 0, "contacts": []}


@respx.mock
@pytest.mark.parametrize(
    ("arguments", "message"),
    [({"text": "  "}, "Provide `text`"), ({"phone": "n/a"}, "nothing to search for")],
)
async def test_lookup_contacts_rejects_an_empty_search(signed_in, arguments, message):
    route = respx.post(SEARCH_URL)
    async with Client(mcp) as client:
        with pytest.raises(ToolError, match=message):
            await client.call_tool("lookup_contacts", arguments)
    assert not route.called


@respx.mock
async def test_lookup_contacts_needs_split_access(signed_in, monkeypatch):
    from staat_assist_mcp.tools.lookup import _common

    monkeypatch.setattr(_common, "_splits_by_user", dict)
    route = respx.post(SEARCH_URL)
    async with Client(mcp) as client:
        with pytest.raises(ToolError, match="not provisioned for any split"):
            await client.call_tool("lookup_contacts", {"text": "james"})
    assert not route.called


@respx.mock
async def test_backend_error_is_readable(signed_in):
    respx.post(SEARCH_URL).mock(
        return_value=httpx.Response(
            400, json={"title": "Bad request", "errors": {"search": ["Invalid query."]}}
        )
    )
    async with Client(mcp) as client:
        with pytest.raises(ToolError, match=r"HTTP 400.*Bad request \| search: Invalid query\."):
            await client.call_tool("lookup_contacts", {"text": "james"})


@respx.mock
async def test_lookup_insight_document_converts_html_to_markdown(signed_in):
    relationship = respx.post(RELATIONSHIP_URL).mock(return_value=httpx.Response(200, text=" 0012345 \n"))
    document = respx.post(DOCUMENT_URL).mock(
        return_value=httpx.Response(
            200,
            text="<style>p{color:red}</style><h2>Gavinski household</h2>"
            "<table><tr><td>Risk</td><td>High</td></tr></table>",
        )
    )
    async with Client(mcp) as client:
        result = await client.call_tool("lookup_insight_document", {"client_id": " CL1 "})

    first = relationship.calls.last.request
    assert json.loads(first.content) == {"fa-gpn": "12345678", "client-id": "CL1", "productType": "FA_PORTAL"}
    assert first.headers["x_user_gpn"] == "12345678" and first.headers["product"] == "FA_PORTAL"
    assert first.headers["authorization"] == "Bearer app-token"
    assert json.loads(document.calls.last.request.content) == {
        "fa-gpn": "12345678",
        "relationship-id": "0012345",
        "productType": "FA_PORTAL",
    }
    assert result.data.startswith("## Gavinski household")
    assert "| Risk | High |" in result.data and "color" not in result.data


@respx.mock
async def test_lookup_insight_document_renders_json_documents(signed_in):
    respx.post(RELATIONSHIP_URL).mock(return_value=httpx.Response(200, text="REL-9"))
    respx.post(DOCUMENT_URL).mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "leadClientName": "James Gavinski",
                    "netWorth": 1000,
                    "goals": ["Retire", None],
                    "notes": "<b>VIP</b>",
                }
            ],
        )
    )
    async with Client(mcp) as client:
        result = await client.call_tool("lookup_insight_document", {"client_id": "CL1"})

    assert result.data == (
        "# James Gavinski\n\n- **leadClientName:** James Gavinski\n- **netWorth:** 1000\n\n"
        "## goals\n\n- Retire\n\n- **notes:** **VIP**\n"
    )


@respx.mock
@pytest.mark.parametrize(
    ("relationship", "document", "message"),
    [
        (httpx.Response(404), httpx.Response(200, text="<p>x</p>"), "no relationship"),
        (httpx.Response(200, text=""), httpx.Response(200, text="<p>x</p>"), "no relationship"),
        (httpx.Response(200, text="REL-9"), httpx.Response(404), "no document"),
        (httpx.Response(200, text="REL-9"), httpx.Response(200, json=[]), "no document"),
        (httpx.Response(200, text="REL-9"), httpx.Response(200, text="  "), "no document"),
    ],
)
async def test_lookup_insight_document_not_found(signed_in, relationship, document, message):
    respx.post(RELATIONSHIP_URL).mock(return_value=relationship)
    respx.post(DOCUMENT_URL).mock(return_value=document)
    async with Client(mcp) as client:
        with pytest.raises(ToolError, match=message):
            await client.call_tool("lookup_insight_document", {"client_id": "CL1"})


@pytest.mark.parametrize("tool", ["lookup_insight_document", "lookup_relationship_id"])
async def test_insight_lookups_need_a_gpn(signed_in, monkeypatch, tool):
    from staat_assist_mcp import auth
    from staat_assist_mcp.tools.lookup import insights

    monkeypatch.setattr(insights, "current_user", lambda: auth.User(id="jane@example.com", gpn=None))
    async with Client(mcp) as client:
        with pytest.raises(ToolError, match="no GPN claim"):
            await client.call_tool(tool, {"client_id": "CL1", "gpn": "  "})


@respx.mock
async def test_lookup_relationship_id(signed_in, caplog):
    relationship = respx.post(RELATIONSHIP_URL).mock(return_value=httpx.Response(200, text=" 0012345 \n"))
    document = respx.post(DOCUMENT_URL)
    async with Client(mcp) as client:
        result = await client.call_tool("lookup_relationship_id", {"client_id": " CL1 "})

    assert result.data == "0012345"
    request = relationship.calls.last.request
    assert json.loads(request.content) == {
        "fa-gpn": "12345678",
        "client-id": "CL1",
        "productType": "FA_PORTAL",
    }
    assert request.headers["x_user_gpn"] == "12345678" and request.headers["product"] == "FA_PORTAL"
    assert not document.called
    assert "Relationship id lookup by Jane@Example.com for client CL1 (GPN from token): found" in caplog.text


@respx.mock
@pytest.mark.parametrize("response", [httpx.Response(404), httpx.Response(200, text=" ")])
async def test_lookup_relationship_id_not_found(signed_in, response):
    respx.post(RELATIONSHIP_URL).mock(return_value=response)
    async with Client(mcp) as client:
        with pytest.raises(ToolError, match="no relationship"):
            await client.call_tool("lookup_relationship_id", {"client_id": "CL1"})


@respx.mock
async def test_insight_lookups_send_the_gpn_argument_instead_of_the_tokens(signed_in, caplog):
    relationship = respx.post(RELATIONSHIP_URL).mock(return_value=httpx.Response(200, text="REL-9"))
    document = respx.post(DOCUMENT_URL).mock(return_value=httpx.Response(200, text="<p>Pack</p>"))
    async with Client(mcp) as client:
        result = await client.call_tool("lookup_insight_document", {"client_id": "CL1", "gpn": " 99999999 "})

    assert result.data == "Pack"
    for route in (relationship, document):
        request = route.calls.last.request
        assert json.loads(request.content)["fa-gpn"] == "99999999"
        assert request.headers["x_user_gpn"] == "99999999"
    assert "(GPN from argument)" in caplog.text
    assert (
        "Insight document lookup by Jane@Example.com for relationship REL-9 as markdown: found, 4 chars"
        in (caplog.text)
    )


@respx.mock
async def test_lookup_insight_document_as_text_from_html(signed_in):
    respx.post(RELATIONSHIP_URL).mock(return_value=httpx.Response(200, text="REL-9"))
    respx.post(DOCUMENT_URL).mock(
        return_value=httpx.Response(
            200,
            text="<html><head><title>x</title></head><body><!-- note --><style>p{color:red}</style>"
            "<h2>Gavinski  household</h2><p>Line one<br>line&nbsp;two</p>"
            "<table><tr><th>Risk</th><td>High<br>for now</td></tr><tr><td>Goals</td><td>Retire</td></tr></table>"
            "<ul><li>A</li><li>B</li></ul></body></html>",
        )
    )
    async with Client(mcp) as client:
        result = await client.call_tool("lookup_insight_document", {"client_id": "CL1", "format": "text"})

    assert result.data == (
        "Gavinski household\n\nLine one\nline two\n\nRisk\tHigh for now\nGoals\tRetire\n\nA\nB"
    )


@respx.mock
async def test_lookup_insight_document_as_text_from_json(signed_in):
    respx.post(RELATIONSHIP_URL).mock(return_value=httpx.Response(200, text="REL-9"))
    respx.post(DOCUMENT_URL).mock(
        return_value=httpx.Response(
            200,
            text='[{"leadClientName": "James Gavinski", "netWorth": 1000.50, "active": true, "spouse": null,'
            ' "goals": ["Retire", null], "accounts": [{"id": "A1"}, {"id": "A2"}],'
            ' "notes": "<p>VIP</p><p>Call monthly</p>"}]',
        )
    )
    async with Client(mcp) as client:
        result = await client.call_tool("lookup_insight_document", {"client_id": "CL1", "format": "text"})

    assert result.data == (
        "James Gavinski\n\n"
        "leadClientName: James Gavinski\nnetWorth: 1000.50\nactive: true\n"
        "goals:\n  - Retire\n"
        "accounts:\n  id: A1\n\n  id: A2\n"
        "notes:\n  VIP\n  \n  Call monthly\n"
    )


@respx.mock
@pytest.mark.parametrize(
    ("response", "expected"),
    [
        (
            httpx.Response(200, text="<h1>Pack</h1><p style='x'>Text</p>"),
            "<h1>Pack</h1><p style='x'>Text</p>",
        ),
        (httpx.Response(200, json="<h1>Pack</h1>"), "<h1>Pack</h1>"),
        (
            httpx.Response(200, text='{"leadClientName": "J", "netWorth": 1.50}'),
            '{"leadClientName": "J", "netWorth": 1.50}',
        ),
    ],
)
async def test_lookup_insight_document_as_html_is_the_original(signed_in, response, expected):
    respx.post(RELATIONSHIP_URL).mock(return_value=httpx.Response(200, text="REL-9"))
    respx.post(DOCUMENT_URL).mock(return_value=response)
    async with Client(mcp) as client:
        result = await client.call_tool("lookup_insight_document", {"client_id": "CL1", "format": "html"})

    assert result.data == expected


@respx.mock
async def test_lookup_insight_document_keeps_numbers_as_written(signed_in):
    respx.post(RELATIONSHIP_URL).mock(return_value=httpx.Response(200, text="REL-9"))
    respx.post(DOCUMENT_URL).mock(
        return_value=httpx.Response(200, text='{"leadClientName": "J", "netWorth": 1000.50, "count": 3}')
    )
    async with Client(mcp) as client:
        result = await client.call_tool("lookup_insight_document", {"client_id": "CL1"})

    assert "- **netWorth:** 1000.50\n- **count:** 3" in result.data


@respx.mock
@pytest.mark.parametrize("fmt", ["text", "html"])
async def test_lookup_insight_document_empty_json_is_not_found_in_every_format(signed_in, fmt):
    respx.post(RELATIONSHIP_URL).mock(return_value=httpx.Response(200, text="REL-9"))
    respx.post(DOCUMENT_URL).mock(return_value=httpx.Response(200, json=[]))
    async with Client(mcp) as client:
        with pytest.raises(ToolError, match="no document"):
            await client.call_tool("lookup_insight_document", {"client_id": "CL1", "format": fmt})


@respx.mock
async def test_ask_cio_streams_progress_and_returns_full_answer(fake_obo):
    sse = 'data: {"content": "Equities "}\n\ndata: {"content": "look "}\n\ndata: attractive.\n\ndata: [DONE]\n\n'
    route = respx.post("http://cio/cio/chat/streaming").mock(
        return_value=httpx.Response(200, headers={"content-type": "text/event-stream"}, content=sse.encode())
    )
    progress: list[str | None] = []

    async def on_progress(progress_value, total, message):
        progress.append(message)

    async with Client(mcp) as client:
        result = await client.call_tool("ask_cio", {"question": "Outlook?"}, progress_handler=on_progress)

    assert result.data == "Equities look attractive."
    assert json.loads(route.calls.last.request.content) == {"question": "Outlook?"}
    assert progress == ["Equities ", "look ", "attractive."]
    assert route.calls.last.request.headers["authorization"] == "Bearer obo-token"


async def test_tools_are_listed():
    async with Client(mcp) as client:
        names = {tool.name for tool in await client.list_tools()}
    assert {
        "lookup_contacts",
        "lookup_relationship_id",
        "lookup_insight_document",
        "ask_cio",
        "placeholder_tool",
    } <= names
    assert "lookup_client" not in names


@respx.mock
async def test_auth_disabled_calls_backend_without_token(monkeypatch, fake_obo):
    from staat_assist_mcp.config import get_settings

    monkeypatch.setattr(get_settings(), "auth_enabled", False)
    sse = "data: hello\n\n"
    route = respx.post("http://cio/cio/chat/streaming").mock(
        return_value=httpx.Response(200, headers={"content-type": "text/event-stream"}, content=sse.encode())
    )
    async with Client(mcp) as client:
        result = await client.call_tool("ask_cio", {"question": "Outlook?"})

    assert result.data == "hello"
    assert "authorization" not in route.calls.last.request.headers
    assert fake_obo == []


@respx.mock
async def test_lookup_insight_document_keeps_numeric_ids_and_reads_html_in_a_json_string(signed_in):
    respx.post(RELATIONSHIP_URL).mock(return_value=httpx.Response(200, text="1e5"))
    document = respx.post(DOCUMENT_URL).mock(
        return_value=httpx.Response(200, json="<h1>Pack</h1><p>Text</p>")
    )
    async with Client(mcp) as client:
        result = await client.call_tool("lookup_insight_document", {"client_id": "CL1"})

    assert json.loads(document.calls.last.request.content)["relationship-id"] == "1e5"
    assert result.data == "# Pack\n\nText"
