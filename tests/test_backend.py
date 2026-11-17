"""Backend auth modes, SSE formats and config.yaml loading."""

import json

import httpx
import pytest
import respx
from pydantic import ValidationError

from staat_assist_mcp.backend import backend
from staat_assist_mcp.config import BackendSettings, Settings, get_settings


@pytest.fixture
def backends(monkeypatch):
    """Replace the configured backends for one test."""

    def configure(**entries: dict) -> None:
        configured = {name: BackendSettings(**entry) for name, entry in entries.items()}
        monkeypatch.setattr(get_settings(), "backends", configured)

    return configure


@respx.mock
async def test_api_key_backend(backends, fake_obo):
    backends(
        orders={"base_url": "http://orders", "auth": "api", "api_key": "key-123", "headers": {"product": "X"}}
    )
    route = respx.get("http://orders/orders/1").mock(return_value=httpx.Response(200, json={"id": 1}))

    assert await backend("orders").get("/orders/1") == {"id": 1}

    headers = route.calls.last.request.headers
    assert headers["x-api-key"] == "key-123" and headers["product"] == "X"
    assert "authorization" not in headers and fake_obo == []


@respx.mock
async def test_passthrough_backend(backends, monkeypatch):
    backends(legacy={"base_url": "http://legacy", "auth": "passthrough"})
    monkeypatch.setattr("staat_assist_mcp.auth.user_token", lambda: "user-token")
    route = respx.get("http://legacy/x").mock(return_value=httpx.Response(200, json={}))

    await backend("legacy").get("/x")

    assert route.calls.last.request.headers["authorization"] == "Bearer user-token"


@respx.mock
async def test_backend_without_auth(backends, fake_obo):
    backends(open={"base_url": "http://open", "auth": "none"})
    route = respx.get("http://open/ping").mock(return_value=httpx.Response(200, json=["pong"]))

    await backend("open").get("/ping")

    assert "authorization" not in route.calls.last.request.headers and fake_obo == []


@respx.mock
async def test_typed_sse_collects_answer_and_followups(backends, fake_obo):
    backends(chat={"base_url": "http://chat", "scopes": ["api://chat/.default"], "sse": "typed"})
    events = [
        {"key": "answer", "type": "start", "value": None},
        {"key": "answer", "type": "status", "value": "thinking"},
        {"key": "answer", "type": "streaming", "value": "Hello"},
        {"key": "answer", "type": "streaming", "value": " world"},
        {"key": "suggest", "type": "followup", "value": ["Tell me more"]},
        {"key": "suggest", "type": "followup", "value": '["Why?"]'},
        {"key": "answer", "type": "end", "value": None},
    ]
    sse = "".join(f"data: {json.dumps(event)}\n\n" for event in events)
    respx.post("http://chat/ask").mock(
        return_value=httpx.Response(200, headers={"content-type": "text/event-stream"}, content=sse.encode())
    )

    result = await backend("chat").stream("POST", "/ask", json={"question": "Hi"})

    assert result.text == "Hello world"
    assert result.followups == ["Tell me more", "Why?"]
    assert fake_obo == ["api://chat/.default"]


def test_backends_come_from_the_file_and_the_environment_overrides_fields(
    tmp_path, monkeypatch, no_backend_env
):
    (tmp_path / "config.yaml").write_text(
        "backends:\n"
        "  lookup: {base_url: http://file, scopes: [api://file/.default], timeout_seconds: 5}\n"
        "  orders: {base_url: http://orders, auth: api}\n"
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("BACKENDS__LOOKUP__BASE_URL", "http://env")
    monkeypatch.setenv("BACKENDS__LOOKUP__SCOPES", "api://a/.default, api://b/.default")
    monkeypatch.setenv("BACKENDS__ORDERS__API_KEY", "secret")

    backends = Settings(_env_file=None).backends

    assert backends["lookup"].base_url == "http://env"
    assert backends["lookup"].scopes == ["api://a/.default", "api://b/.default"]
    assert backends["lookup"].timeout_seconds == 5
    assert backends["orders"].api_key == "secret"


@pytest.mark.parametrize(
    ("entry", "message"),
    [
        ({"base_url": "http://x", "auth": "none", "scops": ["a"]}, "scops"),
        ({"base_url": "http://x", "auth": "basic"}, "auth"),
    ],
)
def test_broken_backend_is_rejected(entry, message):
    with pytest.raises(ValidationError, match=message):
        BackendSettings(**entry)
