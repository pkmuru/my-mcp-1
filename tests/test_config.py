import pytest
from pydantic import ValidationError

from staat_assist_mcp.config import Settings


def test_azure_settings_required_when_auth_enabled(monkeypatch):
    monkeypatch.delenv("AZURE_CLIENT_SECRET")
    with pytest.raises(ValidationError, match="AZURE_CLIENT_SECRET"):
        Settings(_env_file=None)


def test_azure_settings_optional_when_auth_disabled(monkeypatch):
    for name in ("AZURE_TENANT_ID", "AZURE_CLIENT_ID", "AZURE_CLIENT_SECRET"):
        monkeypatch.delenv(name)
    monkeypatch.setenv("AUTH_ENABLED", "false")
    assert Settings(_env_file=None).auth_enabled is False
