from types import SimpleNamespace

import pytest
from fastmcp.exceptions import ToolError

from staat_assist_mcp import auth
from staat_assist_mcp.config import get_settings


class FakeMsalApp:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def acquire_token_on_behalf_of(self, user_assertion, scopes):
        self.calls.append((user_assertion, scopes))
        return self.result

    def acquire_token_for_client(self, scopes):
        self.calls.append(("app", scopes))
        return self.result


@pytest.fixture
def provider(monkeypatch):
    monkeypatch.setattr(auth, "get_access_token", lambda: SimpleNamespace(token="user-token", claims={}))
    return auth.OboTokenProvider(get_settings())


async def test_obo_exchanges_user_token(provider):
    provider._app = FakeMsalApp(
        {"access_token": "obo-123", "expires_in": 3600, "token_source": "identity_provider"}
    )
    assert await provider.get_token("api://backend/.default") == "obo-123"
    assert provider._app.calls == [("user-token", ["api://backend/.default"])]


async def test_obo_error_surfaces_aadsts_details(provider):
    provider._app = FakeMsalApp(
        {
            "error": "invalid_grant",
            "error_codes": [65001],
            "error_description": "AADSTS65001: consent required",
            "correlation_id": "corr-1",
        }
    )
    with pytest.raises(ToolError) as exc:
        await provider.get_token("api://backend/.default")
    message = str(exc.value)
    assert "65001" in message and "corr-1" in message and "consent required" in message


async def test_obo_requires_authenticated_request(monkeypatch):
    monkeypatch.setattr(auth, "get_access_token", lambda: None)
    with pytest.raises(ToolError, match="No authenticated user token"):
        await auth.OboTokenProvider(get_settings()).get_token("api://backend/.default")


async def test_app_token_needs_no_user(monkeypatch):
    monkeypatch.setattr(auth, "get_access_token", lambda: None)
    provider = auth.OboTokenProvider(get_settings())
    provider._app = FakeMsalApp({"access_token": "app-123"})
    assert await provider.get_app_token(["api://backend/.default"]) == "app-123"
    assert provider._app.calls == [("app", ["api://backend/.default"])]


def test_current_user_reads_the_token_claims(monkeypatch):
    claims = {"preferred_username": "jane@example.com", "gpn": " 12345678 "}
    monkeypatch.setattr(auth, "get_access_token", lambda: SimpleNamespace(token="t", claims=claims))
    assert auth.current_user() == auth.User(id="jane@example.com", gpn="12345678")


def test_allowed_splits_ignore_case_and_unknown_users(tmp_path, monkeypatch):
    from staat_assist_mcp.tools.lookup import _common

    file = tmp_path / "user-splits.json"
    file.write_text('[{"userid": "Jane@Example.com", "splits": ["AB12"]}]')
    monkeypatch.setattr(get_settings(), "user_splits_file", str(file))
    _common._splits_by_user.cache_clear()
    try:
        assert _common.allowed_splits("jane@example.COM") == ["AB12"]
        assert _common.allowed_splits("someone@else.com") == []
        assert _common.allowed_splits(None) == []
    finally:
        _common._splits_by_user.cache_clear()
