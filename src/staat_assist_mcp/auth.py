"""Entra ID authentication.

Inbound:  validate the user's bearer token (issued by MSAL for this server's app registration).
Outbound: exchange that token for a backend-scoped token via the On-Behalf-Of (OBO) flow.
"""

import logging
import threading

import anyio
import httpx2
import msal
from fastmcp.exceptions import ToolError
from fastmcp.server.auth import RemoteAuthProvider
from fastmcp.server.auth.providers.azure import AzureJWTVerifier
from fastmcp.server.dependencies import get_access_token
from pydantic import AnyHttpUrl

from .config import Settings, get_settings

log = logging.getLogger(__name__)


class _EntraVerifier(AzureJWTVerifier):
    """AzureJWTVerifier whose JWKS download can skip TLS verification."""

    def __init__(self, *, ssl_verify: bool, **kwargs):
        super().__init__(**kwargs)
        if not ssl_verify:
            # AzureJWTVerifier does not expose JWTVerifier's http_client argument, so set it directly.
            self._http_client = httpx2.AsyncClient(verify=False, timeout=httpx2.Timeout(10.0))


def build_auth(settings: Settings) -> RemoteAuthProvider:
    """Resource-server auth: requests without a valid Entra token for this app get a 401."""
    verifier = _EntraVerifier(
        ssl_verify=settings.ssl_verify,
        client_id=settings.azure_client_id,
        tenant_id=settings.azure_tenant_id,
        required_scopes=settings.required_scope_list or None,
        base_authority=settings.azure_authority_host,
    )
    return RemoteAuthProvider(
        token_verifier=verifier,
        authorization_servers=[AnyHttpUrl(f"{settings.authority}/v2.0")],
        base_url=settings.base_url,
    )


class OboTokenProvider:
    """Exchanges the caller's token for a backend token (OBO), cached by MSAL per user + scope."""

    def __init__(self, settings: Settings):
        self._settings = settings
        self._app: msal.ConfidentialClientApplication | None = None
        self._lock = threading.Lock()

    def _get_app(self) -> msal.ConfidentialClientApplication:
        # Created lazily: the constructor calls Entra for authority discovery.
        with self._lock:
            if self._app is None:
                self._app = msal.ConfidentialClientApplication(
                    client_id=self._settings.azure_client_id,
                    client_credential=self._settings.azure_client_secret,
                    authority=self._settings.authority,
                    verify=self._settings.ssl_verify,
                )
            return self._app

    def _acquire(self, user_token: str, scope: str) -> dict:
        return self._get_app().acquire_token_on_behalf_of(user_assertion=user_token, scopes=[scope])

    async def get_token(self, scope: str) -> str:
        access = get_access_token()
        if access is None:
            raise ToolError("No authenticated user token on this request; cannot perform OBO exchange.")

        try:
            result = await anyio.to_thread.run_sync(self._acquire, access.token, scope)
        except Exception as exc:  # network / TLS / discovery failures
            log.exception("OBO exchange failed before reaching Entra (scope=%s)", scope)
            raise ToolError(f"OBO token exchange failed for scope '{scope}': {exc}") from exc

        if "access_token" not in result:
            codes = result.get("error_codes")
            log.error(
                "OBO exchange rejected scope=%s error=%s codes=%s correlation_id=%s description=%s",
                scope,
                result.get("error"),
                codes,
                result.get("correlation_id"),
                result.get("error_description"),
            )
            raise ToolError(
                f"OBO token exchange failed for scope '{scope}': {result.get('error')} {codes} "
                f"(Entra correlation_id={result.get('correlation_id')}): {result.get('error_description')}"
            )

        log.debug(
            "OBO token acquired scope=%s source=%s expires_in=%ss",
            scope,
            result.get("token_source", "unknown"),
            result.get("expires_in"),
        )
        return result["access_token"]


obo = OboTokenProvider(get_settings())
