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


def test_settings_come_from_config_yaml_and_the_environment_overrides_them(
    tmp_path, monkeypatch, no_backend_env
):
    (tmp_path / "config.yaml").write_text(
        "port: 9001\nlog_level: WARNING\nrequired_scopes: [read, write]\ncors_origins: [http://a]\n"
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("LOG_LEVEL", "ERROR")
    monkeypatch.setenv("CORS_ORIGINS", "http://b, http://c")

    settings = Settings(_env_file=None)

    assert settings.port == 9001
    assert settings.required_scopes == ["read", "write"]
    assert settings.log_level == "ERROR"
    assert settings.cors_origins == ["http://b", "http://c"]


def test_config_file_names_another_yaml_file(tmp_path, monkeypatch, no_backend_env):
    other = tmp_path / "config.test.yaml"
    other.write_text("port: 9002\n")
    monkeypatch.setenv("CONFIG_FILE", str(other))

    assert Settings(_env_file=None).port == 9002
