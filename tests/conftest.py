import os

# Must be set before staat_assist_mcp is imported (settings load at import time).
os.environ.update(
    {
        "AZURE_TENANT_ID": "11111111-1111-1111-1111-111111111111",
        "AZURE_CLIENT_ID": "22222222-2222-2222-2222-222222222222",
        "AZURE_CLIENT_SECRET": "test-secret",
        "LOG_LEVEL": "DEBUG",
        "LOG_PAYLOADS": "true",
        "SSL_VERIFY": "false",
        # The repository's backends.yaml is loaded; only the URLs and scopes are overridden.
        "BACKENDS__CONTACT_SEARCH__BASE_URL": "http://search/indexes/contacts",
        "BACKENDS__CONTACT_SEARCH__API_KEY": "search-key",
        "BACKENDS__STAAT_INSIGHTS__BASE_URL": "http://insights/v2",
        "BACKENDS__STAAT_INSIGHTS__SCOPES": "api://insights/.default",
        "BACKENDS__CIO__BASE_URL": "http://cio",
        "BACKENDS__CIO__SCOPES": "api://cio/.default",
    }
)

import pytest

from staat_assist_mcp import auth


@pytest.fixture
def fake_obo(monkeypatch):
    """Skip the real Entra OBO exchange; record requested scopes."""
    scopes: list[str] = []

    async def get_token(requested: list[str]) -> str:
        scopes.extend(requested)
        return "obo-token"

    monkeypatch.setattr(auth.obo, "get_token", get_token)
    return scopes


@pytest.fixture
def signed_in(monkeypatch):
    """A signed-in user with split access and a GPN, without a real token."""
    from staat_assist_mcp.tools.lookup import _common, contacts, insights

    user = auth.User(id="Jane@Example.com", gpn="12345678")
    monkeypatch.setattr(contacts, "current_user", lambda: user)
    monkeypatch.setattr(insights, "current_user", lambda: user)
    monkeypatch.setattr(_common, "_splits_by_user", lambda: {"jane@example.com": ["AB12", "CD34"]})

    async def get_app_token(scopes: list[str]) -> str:
        return "app-token"

    monkeypatch.setattr(auth.obo, "get_app_token", get_app_token)
    return user
