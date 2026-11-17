"""Backend HTTP access for tools.

    data = await backend("lookup").post("/contact-lookup/search", json={"text": "james"})
    answer = await backend("cio").stream("POST", "/cio/chat/streaming", json={...}, ctx=ctx)

Each call is authorized the way the backend's `auth` says (backends.yaml), is logged with timing,
and maps failures to ToolError so the agent (and the developer) sees what went wrong.
"""

import json as jsonlib
import logging
import time
from collections.abc import Callable
from typing import Any

import httpx
from fastmcp import Context
from fastmcp.exceptions import ToolError
from httpx_sse import ServerSentEvent, aconnect_sse

from . import auth
from .config import BackendSettings, get_settings
from .logging_config import request_id_var, truncate
from .sse import SseItem, StreamResult, parse_event

log = logging.getLogger(__name__)

_http: httpx.AsyncClient | None = None


def _client() -> httpx.AsyncClient:
    global _http
    if _http is None:
        _http = httpx.AsyncClient(verify=get_settings().ssl_verify)
    return _http


class BackendError(ToolError):
    """A backend answered with an error status. Tools can check `.status`, e.g. to treat 404 as "not found"."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


def problem_text(body: str) -> str | None:
    """Readable message from a problem+json error body (title, detail, validation errors); None if not one."""
    try:
        problem = jsonlib.loads(body)
    except ValueError:
        return None
    if not isinstance(problem, dict):
        return None
    parts = [str(problem[key]) for key in ("title", "detail") if problem.get(key)]
    errors = problem.get("errors")
    if isinstance(errors, dict):
        for field, messages in errors.items():
            messages = messages if isinstance(messages, list) else [messages]
            parts.append(f"{field}: {' '.join(str(m) for m in messages)}")
    return " | ".join(parts) or None


class BackendClient:
    def __init__(self, name: str, cfg: BackendSettings):
        self.name = name
        self.cfg = cfg

    def _url(self, path: str) -> str:
        return f"{self.cfg.base_url.rstrip('/')}/{path.lstrip('/')}"

    async def _headers(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        cfg = self.cfg
        headers = {"x-request-id": request_id_var.get(), **(extra or {}), **cfg.headers}
        if cfg.auth == "api":
            headers[cfg.api_key_header] = cfg.api_key
        elif cfg.auth in ("obo", "app", "passthrough") and not get_settings().auth_enabled:
            log.debug("AUTH_ENABLED=false; calling backend=%s without Authorization", self.name)
        elif cfg.auth == "obo":
            headers["Authorization"] = f"Bearer {await auth.obo.get_token(cfg.scopes)}"
        elif cfg.auth == "app":
            headers["Authorization"] = f"Bearer {await auth.obo.get_app_token(cfg.scopes)}"
        elif cfg.auth == "passthrough":
            headers["Authorization"] = f"Bearer {auth.user_token()}"
        return headers

    def _fail(self, method: str, url: str, status: int, body: str) -> "BackendError":
        log.error("backend=%s %s %s -> %s body=%s", self.name, method, url, status, truncate(body))
        # Problem details are passed on whole so the agent can read what to correct.
        reason = problem_text(body) or truncate(body, 300)
        return BackendError(
            status, f"Backend '{self.name}' returned HTTP {status} for {method} {url}: {reason}"
        )

    async def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: Any = None,
        headers: dict[str, str] | None = None,
        raw: bool = False,
    ) -> Any:
        """Call an API. Returns the parsed JSON body, or the text if the response is not JSON.

        `raw=True` always returns the text exactly as sent, for backends that answer with plain text
        or HTML (parsing would turn an id like "12345" into a number).
        """
        url = self._url(path)
        request_headers = await self._headers(headers)
        log.info("backend=%s -> %s %s params=%s", self.name, method, url, params)
        if get_settings().log_payloads and json is not None:
            log.debug("backend=%s request body=%s", self.name, truncate(jsonlib.dumps(json, default=str)))

        started = time.perf_counter()
        try:
            response = await _client().request(
                method,
                url,
                params=params,
                json=json,
                headers=request_headers,
                timeout=self.cfg.timeout_seconds,
            )
        except httpx.HTTPError as exc:
            log.exception("backend=%s %s %s failed", self.name, method, url)
            raise ToolError(f"Backend '{self.name}' unreachable for {method} {url}: {exc!r}") from exc
        elapsed_ms = (time.perf_counter() - started) * 1000

        if response.is_error:
            raise self._fail(method, url, response.status_code, response.text)
        log.info(
            "backend=%s <- %s %s %s in %.0fms (%d bytes)",
            self.name, response.status_code, method, url, elapsed_ms, len(response.content),
        )  # fmt: skip
        if get_settings().log_payloads:
            log.debug("backend=%s response body=%s", self.name, truncate(response.text))

        if raw:
            return response.text
        try:
            return response.json()
        except ValueError:
            return response.text

    async def get(self, path: str, **kwargs: Any) -> Any:
        return await self.request("GET", path, **kwargs)

    async def post(self, path: str, **kwargs: Any) -> Any:
        return await self.request("POST", path, **kwargs)

    async def stream(
        self,
        method: str,
        path: str,
        *,
        ctx: Context | None = None,
        params: dict[str, Any] | None = None,
        json: Any = None,
        headers: dict[str, str] | None = None,
        extract: Callable[[ServerSentEvent], str | None] | None = None,
    ) -> StreamResult:
        """Consume an SSE endpoint.

        Each text chunk is forwarded to the MCP client as a progress notification (live updates in
        the agent); the concatenated text, plus any follow-up suggestions, is returned for the tool
        result. Events are read the way the backend's `sse` setting says; pass `extract` to override.
        """
        url = self._url(path)
        request_headers = await self._headers(headers)
        log.info("backend=%s -> %s %s (SSE)", self.name, method, url)
        timeout = httpx.Timeout(self.cfg.timeout_seconds, read=self.cfg.sse_read_timeout_seconds)

        started = time.perf_counter()
        chunks: list[str] = []
        result = StreamResult()
        events = 0
        try:
            async with aconnect_sse(
                _client(), method, url, params=params, json=json, headers=request_headers, timeout=timeout
            ) as source:
                response = source.response
                if response.is_error:
                    raise self._fail(
                        method, url, response.status_code, (await response.aread()).decode(errors="replace")
                    )
                log.debug("backend=%s SSE connected status=%s", self.name, response.status_code)

                async for event in source.aiter_sse():
                    events += 1
                    if get_settings().log_payloads:
                        log.debug(
                            "backend=%s SSE event=%s data=%s",
                            self.name,
                            event.event,
                            truncate(event.data, 200),
                        )
                    if extract is not None:
                        chunk = extract(event)
                        items = [SseItem("chunk", chunk)] if chunk else []
                    else:
                        items = parse_event(event, self.cfg)
                    for item in items:
                        if item.kind == "followup":
                            result.followups.append(item.value)
                            continue
                        chunks.append(item.value)
                        if ctx is not None:
                            await ctx.report_progress(progress=len(chunks), message=item.value)
        except ToolError:
            raise
        except Exception as exc:  # httpx errors, non-SSE content type, broken stream
            log.exception("backend=%s SSE stream failed after %d events", self.name, events)
            raise ToolError(
                f"Backend '{self.name}' SSE stream {method} {url} failed after {events} events: {exc!r}"
            ) from exc

        result.text = "".join(chunks)
        log.info(
            "backend=%s <- SSE %s %s done: %d events, %d chars, %d followups in %.0fms",
            self.name, method, url, events, len(result.text), len(result.followups),
            (time.perf_counter() - started) * 1000,
        )  # fmt: skip
        return result


def backend(name: str) -> BackendClient:
    """Client for a backend from backends.yaml."""
    backends = get_settings().backends
    cfg = backends.get(name.lower())
    if cfg is None:
        raise ToolError(
            f"Backend '{name}' is not configured. Add it to backends.yaml "
            f"(configured: {sorted(backends) or 'none'})."
        )
    return BackendClient(name.lower(), cfg)
