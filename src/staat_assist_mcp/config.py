"""Configuration.

Settings are read from `config.yaml`; secrets come from the environment or a `.env` file.
An environment variable overrides the file: PORT=9000, or BACKENDS__CIO__BASE_URL for one field of
one backend. CONFIG_FILE names another YAML file. pydantic-settings does the loading and merging.
"""

import os
from functools import lru_cache
from typing import Annotated, Literal

from pydantic import BaseModel, BeforeValidator, ConfigDict, model_validator
from pydantic_settings import (
    BaseSettings,
    NoDecode,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    YamlConfigSettingsSource,
)


def _split_commas(value: object) -> object:
    """A list arrives as a YAML list, or as "a,b" from an environment variable."""
    return [item.strip() for item in value.split(",") if item.strip()] if isinstance(value, str) else value


# NoDecode: pydantic-settings would otherwise expect JSON in the environment variable.
StrList = Annotated[list[str], NoDecode, BeforeValidator(_split_commas)]


class BackendSettings(BaseModel):
    """One backend service: where it is and how calls to it are authorized."""

    model_config = ConfigDict(extra="forbid")  # a misspelled field is an error, not silently ignored

    base_url: str
    # obo: exchange the user's token for one with `scopes` (On-Behalf-Of).
    # app: this server's own token for `scopes` (client credentials); the backend does not see the user.
    # api: no user token; `api_key` is sent in the `api_key_header` header.
    # passthrough: forward the user's token unchanged.
    # none: no Authorization header.
    auth: Literal["obo", "app", "api", "passthrough", "none"] = "obo"
    scopes: StrList = []
    api_key: str = ""  # a secret: set it with BACKENDS__<NAME>__API_KEY in .env, not in config.yaml
    api_key_header: str = "X-Api-Key"
    headers: dict[str, str] = {}  # extra fixed headers sent with every call
    timeout_seconds: float = 30.0
    # Max silence between two SSE events before the stream is considered dead.
    sse_read_timeout_seconds: float = 300.0
    # How SSE events are read, see sse.py. "auto": text or JSON with a text field. "typed": events
    # that name their kind ({"type": "streaming", "value": ...}).
    sse: Literal["auto", "typed"] = "auto"
    sse_field: str | None = None  # "auto" only: the JSON property that holds the text, e.g. "delta"

    @model_validator(mode="after")
    def _check_auth(self) -> "BackendSettings":
        if self.auth in ("obo", "app") and not self.scopes:
            raise ValueError(f'auth "{self.auth}" needs scopes, e.g. [api://<backend-app-id>/.default]')
        if self.auth == "api" and not self.api_key:
            raise ValueError('auth "api" needs api_key (set BACKENDS__<NAME>__API_KEY in .env)')
        return self


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_nested_delimiter="__", extra="ignore")

    # AUTH_ENABLED=false turns off BOTH inbound token validation and the outbound OBO exchange
    # (backends are then called without an Authorization header). Local development only.
    auth_enabled: bool = True

    # Entra ID app registration of THIS MCP server; required when auth is enabled
    azure_tenant_id: str = ""
    azure_client_id: str = ""
    azure_client_secret: str = ""  # a secret: AZURE_CLIENT_SECRET in .env
    azure_authority_host: str = "login.microsoftonline.com"
    # Short scope names from "Expose an API" that the user's token must carry
    required_scopes: StrList = ["access_as_user"]

    # HTTP server
    host: str = "0.0.0.0"
    port: int = 8000
    base_url: str = "http://localhost:8000"
    # Browser origins allowed to call the server directly (or ["*"]). Empty = CORS off.
    # Only needed for browser-based clients such as the MCP Inspector in "Direct" connection mode.
    cors_origins: StrList = []

    # Diagnostics
    log_level: str = "INFO"
    log_payloads: bool = False  # log tool arguments/results and backend bodies (may contain client data)

    # TLS verification for ALL outbound calls (backends, Entra token endpoint, JWKS).
    # Off by default as requested; set SSL_VERIFY=true to re-enable.
    ssl_verify: bool = False

    # Lookup tools
    # Who may see which splits: a JSON list of {"userid": ..., "splits": [...]}.
    user_splits_file: str = "user-splits.json"
    # Token claim that holds the financial advisor's GPN.
    gpn_claim: str = "gpn"
    # Identifies this application to STAAT insights ("product" header and "productType" in the body).
    insights_product: str = "FA_PORTAL"
    # Stand-in user when AUTH_ENABLED=false (there is no token to read the user from).
    dev_user_id: str = ""
    dev_gpn: str = ""

    # The `backends:` section of config.yaml, overridden per field by BACKENDS__<NAME>__<FIELD>.
    backends: dict[str, BackendSettings] = {}

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        # Earlier sources win: environment, then .env, then config.yaml.
        yaml_file = os.environ.get("CONFIG_FILE", "config.yaml")
        yaml_settings = YamlConfigSettingsSource(settings_cls, yaml_file=yaml_file)
        return init_settings, env_settings, dotenv_settings, yaml_settings

    @model_validator(mode="after")
    def _require_azure_settings_when_auth_enabled(self) -> "Settings":
        if self.auth_enabled:
            missing = [
                name.upper()
                for name in ("azure_tenant_id", "azure_client_id", "azure_client_secret")
                if not getattr(self, name)
            ]
            if missing:
                raise ValueError(
                    f"{', '.join(missing)} must be set: ids in config.yaml, the secret in .env "
                    "(or auth_enabled: false for local dev)"
                )
        return self

    @property
    def authority(self) -> str:
        return f"https://{self.azure_authority_host}/{self.azure_tenant_id}"


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
