"""Logging setup. Every log line carries the request id of the tool call it belongs to."""

import logging
from contextvars import ContextVar

request_id_var: ContextVar[str] = ContextVar("request_id", default="-")

_FORMAT = "%(asctime)s %(levelname)-7s [%(request_id)s] %(name)s: %(message)s"


class _RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get()
        return True


def setup_logging(level: str) -> None:
    level = level.upper()
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter(_FORMAT))
    handler.addFilter(_RequestIdFilter())

    app_logger = logging.getLogger("staat_assist_mcp")
    app_logger.handlers = [handler]
    app_logger.setLevel(level)
    app_logger.propagate = False

    # Library loggers: chatty only when we are debugging.
    for name in ("msal", "httpx", "httpcore"):
        lib_logger = logging.getLogger(name)
        lib_logger.handlers = [handler]
        lib_logger.propagate = False
        lib_logger.setLevel(logging.DEBUG if level == "DEBUG" and name != "httpcore" else logging.WARNING)


def truncate(text: str, limit: int = 500) -> str:
    return text if len(text) <= limit else f"{text[:limit]}... <{len(text) - limit} more chars>"
