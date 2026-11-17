"""Entry point: `uv run staat-assist-mcp`."""

import asyncio
import logging
import sys

from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware

from . import tools  # noqa: F401  (registers the tools)
from .app import mcp, settings

log = logging.getLogger("staat_assist_mcp")


def _describe_setup() -> list[str]:
    """What the server exposes: every backend and every tool, one line each."""
    lines = ["backends (config.yaml, BACKENDS__* overrides):"]
    for name, cfg in sorted(settings.backends.items()):
        scopes = f" scopes={cfg.scopes}" if cfg.scopes else ""
        lines.append(f"  {name:<14} {cfg.auth:<11} {cfg.base_url}{scopes}")
    listed = sorted(asyncio.run(mcp.list_tools()), key=lambda tool: (sorted(tool.tags), tool.name))
    lines.append(f"tools ({len(listed)}):")
    for tool in listed:
        lines.append(f"  {tool.name:<28} group={','.join(sorted(tool.tags)) or '-'}")
    return lines


def main() -> None:
    if missing := settings.missing():
        log.warning(
            "not set: %s. The server starts, but calls that need them fail. "
            "Secrets go in .env (see .env.example), the rest in config.yaml.",
            ", ".join(missing),
        )

    # `staat-assist-mcp --check`: print the backends and tools, and exit.
    # (An invalid value or a misspelled field in config.yaml or .env already stopped the import above.)
    if "--check" in sys.argv[1:]:
        print("\n".join(_describe_setup()))
        return

    if not settings.auth_enabled:
        log.warning(
            "AUTH_ENABLED=false: the server accepts UNAUTHENTICATED requests and calls backends without a token"
        )
    if not settings.ssl_verify:
        log.warning("SSL_VERIFY=false: TLS certificate verification is DISABLED for all outbound calls")
    log.info(
        "starting on %s:%s tenant=%s client_id=%s required_scopes=%s log_level=%s",
        settings.host,
        settings.port,
        settings.azure_tenant_id,
        settings.azure_client_id,
        settings.required_scopes,
        settings.log_level,
    )
    for line in _describe_setup():
        log.info(line)
    middleware = []
    if settings.cors_origins:
        log.info("CORS enabled for origins=%s", settings.cors_origins)
        middleware.append(
            Middleware(
                CORSMiddleware,
                allow_origins=settings.cors_origins,
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
