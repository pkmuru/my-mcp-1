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
        "BACKENDS__SOMESERVICE__BASE_URL": "http://someservice",
        "BACKENDS__SOMESERVICE__SCOPE": "api://backend/.default",
    }
)

import pytest

from staat_assist_mcp import auth


@pytest.fixture
def fake_obo(monkeypatch):
    """Skip the real Entra OBO exchange; record requested scopes."""
    scopes: list[str] = []

    async def get_token(scope: str) -> str:
        scopes.append(scope)
        return "obo-token"

    monkeypatch.setattr(auth.obo, "get_token", get_token)
    return scopes
