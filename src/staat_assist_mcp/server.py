"""Entry point: `uv run staat-assist-mcp`."""

import logging

from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware

from . import tools  # noqa: F401  (registers the tools)
from .app import mcp, settings

log = logging.getLogger("staat_assist_mcp")


def main() -> None:
    if not settings.auth_enabled:
        log.warning(
            "AUTH_ENABLED=false: the server accepts UNAUTHENTICATED requests and calls backends without a token"
        )
    if not settings.ssl_verify:
        log.warning("SSL_VERIFY=false: TLS certificate verification is DISABLED for all outbound calls")
    log.info(
        "starting on %s:%s tenant=%s client_id=%s required_scopes=%s backends=%s log_level=%s",
        settings.host,
        settings.port,
        settings.azure_tenant_id,
        settings.azure_client_id,
        settings.required_scope_list,
        {name: f"{cfg.base_url} (scope={cfg.scope})" for name, cfg in settings.backends.items()},
        settings.log_level,
    )
    middleware = []
    if settings.cors_origin_list:
        log.info("CORS enabled for origins=%s", settings.cors_origin_list)
        middleware.append(
            Middleware(
                CORSMiddleware,
                allow_origins=settings.cors_origin_list,
                allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
                allow_headers=["*"],
                expose_headers=["mcp-session-id", "www-authenticate"],
            )
        )
    mcp.run(
        transport="http",
        host=settings.host,
        port=settings.port,
        log_level=settings.log_level.lower(),
        stateless_http=True,
        middleware=middleware,
    )


if __name__ == "__main__":
    main()
