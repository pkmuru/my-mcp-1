"""Configuration, loaded from environment variables and an optional `.env` file."""

from functools import lru_cache

from pydantic import BaseModel, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class BackendSettings(BaseModel):
    """One backend service. Env: BACKENDS__<NAME>__BASE_URL, BACKENDS__<NAME>__SCOPE, ..."""

    base_url: str
    # Scope requested in the OBO exchange, e.g. "api://<backend-app-id>/.default".
    # Leave unset to call the backend without an Authorization header.
    scope: str | None = None
    timeout: float = 30.0
    # Max silence between two SSE events before the stream is considered dead.
    sse_read_timeout: float = 300.0


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_nested_delimiter="__", extra="ignore")

    # AUTH_ENABLED=false turns off BOTH inbound token validation and the outbound OBO exchange
    # (backends are then called without an Authorization header). Local development only.
    auth_enabled: bool = True

    # Entra ID app registration of THIS MCP server; required when auth is enabled
    azure_tenant_id: str = ""
    azure_client_id: str = ""
    azure_client_secret: str = ""
    azure_authority_host: str = "login.microsoftonline.com"
    # Comma-separated short scope names from "Expose an API", e.g. "access_as_user"
    required_scopes: str = "access_as_user"

    # HTTP server
    host: str = "0.0.0.0"
    port: int = 8000
    base_url: str = "http://localhost:8000"
    # Browser origins allowed to call the server directly (comma-separated, or "*"). Empty = CORS off.
    # Only needed for browser-based clients such as the MCP Inspector in "Direct" connection mode.
    cors_origins: str = ""

    # Diagnostics
    log_level: str = "INFO"
    log_payloads: bool = False  # log tool arguments/results and backend bodies (may contain client data)

    # TLS verification for ALL outbound calls (backends, Entra token endpoint, JWKS).
    # Off by default as requested; set SSL_VERIFY=true to re-enable.
    ssl_verify: bool = False

    backends: dict[str, BackendSettings] = {}

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
                    f"{', '.join(missing)} must be set (or set AUTH_ENABLED=false for local dev)"
                )
        return self

    @property
    def required_scope_list(self) -> list[str]:
        return [s.strip() for s in self.required_scopes.split(",") if s.strip()]

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def authority(self) -> str:
        return f"https://{self.azure_authority_host}/{self.azure_tenant_id}"


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
