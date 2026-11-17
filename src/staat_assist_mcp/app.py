"""The FastMCP instance. Tool modules import `mcp` from here."""

import logging
import time
import uuid

from fastmcp import FastMCP
from fastmcp.server.dependencies import get_access_token
from fastmcp.server.middleware import Middleware, MiddlewareContext
from fastmcp.server.middleware.logging import LoggingMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from .auth import build_auth
from .config import get_settings
from .logging_config import request_id_var, setup_logging

settings = get_settings()
setup_logging(settings.log_level)
log = logging.getLogger("staat_assist_mcp")
_debug = settings.log_level.upper() == "DEBUG"


class RequestContextMiddleware(Middleware):
    """Assigns a request id to each tool call and logs who called what, and how long it took."""

    async def on_call_tool(self, context: MiddlewareContext, call_next):
        token = request_id_var.set(uuid.uuid4().hex[:12])
        tool = context.message.name
        access = get_access_token()
        claims = access.claims if access else {}
        log.info(
            "tool=%s start user=%s oid=%s azp=%s scp=%s",
            tool,
            claims.get("preferred_username") or claims.get("upn"),
            claims.get("oid"),
            claims.get("azp") or claims.get("appid"),
            claims.get("scp"),
        )
        started = time.perf_counter()
        try:
            result = await call_next(context)
        except Exception as exc:
            # Full traceback at DEBUG; the error itself is returned to the MCP client unchanged.
            log.error(
                "tool=%s failed in %.0fms: %s",
                tool,
                (time.perf_counter() - started) * 1000,
                exc,
                exc_info=_debug,
            )
            raise
        else:
            log.info("tool=%s ok in %.0fms", tool, (time.perf_counter() - started) * 1000)
            return result
        finally:
            request_id_var.reset(token)


mcp = FastMCP(
    "staat-assist",
    instructions=(
        "Lookup tools (lookup_*): search the user's clients, and get a client's relationship id or "
        "insight document. ask_cio answers CIO (Chief Investment Office) questions."
    ),
    auth=build_auth(settings) if settings.auth_enabled else None,
    on_duplicate="error",  # two tools with one name is a mistake, not an override
)
mcp.add_middleware(RequestContextMiddleware())
if settings.log_payloads:
    mcp.add_middleware(LoggingMiddleware(logger=log, include_payloads=True, max_payload_length=2000))


@mcp.custom_route("/health", methods=["GET"])
async def health(_: Request) -> JSONResponse:
    return JSONResponse({"status": "ok"})
