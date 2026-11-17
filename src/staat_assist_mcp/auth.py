"""Entra ID authentication.

Inbound:  validate the user's bearer token (issued by MSAL for this server's app registration).
Outbound: exchange that token for a backend-scoped token via the On-Behalf-Of (OBO) flow.
"""

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

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
        required_scopes=settings.required_scopes or None,
        base_authority=settings.azure_authority_host,
    )
    return RemoteAuthProvider(
        token_verifier=verifier,
        authorization_servers=[AnyHttpUrl(f"{settings.authority}/v2.0")],
        base_url=settings.base_url,
    )


class OboTokenProvider:
    """Gets backend tokens from Entra ID, cached by MSAL.

    get_token:      for the signed-in user (On-Behalf-Of), cached per user + scope.
    get_app_token:  for this server itself (client credentials), no user involved.
    """

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

    async def get_token(self, scopes: str | list[str]) -> str:
        access = get_access_token()
        if access is None:
            raise ToolError("No authenticated user token on this request; cannot perform OBO exchange.")
        return await self._acquire(
            "OBO",
            scopes,
            lambda app, scope: app.acquire_token_on_behalf_of(user_assertion=access.token, scopes=scope),
        )

    async def get_app_token(self, scopes: str | list[str]) -> str:
        return await self._acquire(
            "App", scopes, lambda app, scope: app.acquire_token_for_client(scopes=scope)
        )

    async def _acquire(
        self, kind: str, scopes: str | list[str], call: Callable[[Any, list[str]], dict]
    ) -> str:
        """Run one MSAL call (blocking, so in a thread) and turn a refusal into a readable ToolError."""
        scope = scopes.split() if isinstance(scopes, str) else scopes
        try:
            result = await anyio.to_thread.run_sync(lambda: call(self._get_app(), scope))
        except Exception as exc:  # network / TLS / discovery failures
            log.exception("%s token request failed before reaching Entra (scope=%s)", kind, scope)
            raise ToolError(f"{kind} token request failed for scope {scope}: {exc}") from exc

        if "access_token" not in result:
            codes = result.get("error_codes")
            log.error(
                "%s token request rejected scope=%s error=%s codes=%s correlation_id=%s description=%s",
                kind,
                scope,
                result.get("error"),
                codes,
                result.get("correlation_id"),
                result.get("error_description"),
            )
            raise ToolError(
                f"{kind} token request failed for scope {scope}: {result.get('error')} {codes} "
                f"(Entra correlation_id={result.get('correlation_id')}): {result.get('error_description')}"
            )

        log.debug(
            "%s token acquired scope=%s source=%s expires_in=%ss",
            kind,
            scope,
            result.get("token_source", "unknown"),
            result.get("expires_in"),
        )
        return result["access_token"]


@dataclass(frozen=True)
class User:
    """The signed-in user, from the claims of the validated token."""

    id: str | None  # preferred_username, upn or email
    gpn: str | None  # the financial advisor's GPN


def current_user() -> User:
    settings = get_settings()
    access = get_access_token()
    if access is None:  # only with AUTH_ENABLED=false: a stand-in user for local development
        return User(settings.dev_user_id or None, settings.dev_gpn or None)
    claims = access.claims
    user_id = next(
        (claims[name] for name in ("preferred_username", "upn", "email") if claims.get(name)), None
    )
    return User(user_id, str(claims.get(settings.gpn_claim) or "").strip() or None)


def user_token() -> str:
    """The caller's own bearer token, for backends with auth "passthrough"."""
    access = get_access_token()
    if access is None:
        raise ToolError("No authenticated user token on this request; cannot pass it through to the backend.")
    return access.token


obo = OboTokenProvider(get_settings())
