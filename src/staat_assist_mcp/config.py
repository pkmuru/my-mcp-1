"""Configuration, loaded from environment variables and an optional `.env` file."""

from functools import lru_cache

from pydantic import BaseModel
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

    # Entra ID app registration of THIS MCP server
    azure_tenant_id: str
    azure_client_id: str
    azure_client_secret: str
    azure_authority_host: str = "login.microsoftonline.com"
    # Comma-separated short scope names from "Expose an API", e.g. "access_as_user"
    required_scopes: str = "access_as_user"

    # HTTP server
    host: str = "0.0.0.0"
    port: int = 8000
    base_url: str = "http://localhost:8000"

    # Diagnostics
    log_level: str = "INFO"
    log_payloads: bool = False  # log tool arguments/results and backend bodies (may contain client data)

    # TLS verification for ALL outbound calls (backends, Entra token endpoint, JWKS).
    # Off by default as requested; set SSL_VERIFY=true to re-enable.
    ssl_verify: bool = False

    backends: dict[str, BackendSettings] = {}

    @property
    def required_scope_list(self) -> list[str]:
        return [s.strip() for s in self.required_scopes.split(",") if s.strip()]

    @property
    def authority(self) -> str:
        return f"https://{self.azure_authority_host}/{self.azure_tenant_id}"


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
