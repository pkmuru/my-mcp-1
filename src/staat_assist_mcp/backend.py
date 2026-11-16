"""Backend HTTP access for tools.

    data = await backend("someservice").get("/find-contact", params={"email": email})
    text = await backend("someservice").stream_sse("POST", "/cio/chat/streaming", json={...}, ctx=ctx)

Each call gets an OBO token for the backend's scope, is logged with timing, and maps
failures to ToolError so the agent (and the developer) sees what went wrong.
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

log = logging.getLogger(__name__)

_http: httpx.AsyncClient | None = None


def _client() -> httpx.AsyncClient:
    global _http
    if _http is None:
        _http = httpx.AsyncClient(verify=get_settings().ssl_verify)
    return _http


def default_sse_extract(event: ServerSentEvent) -> str | None:
    """Turn one SSE event into a text chunk (None = skip). Pass your own `extract` if a backend differs."""
    data = event.data
    if not data or data.strip() == "[DONE]":
        return None
    try:
        payload = jsonlib.loads(data)
    except ValueError:
        return data
    if isinstance(payload, dict):
        for key in ("content", "text", "delta", "answer", "message"):
            if isinstance(payload.get(key), str):
                return payload[key]
        return None
    return str(payload)


class BackendClient:
    def __init__(self, name: str, cfg: BackendSettings):
        self.name = name
        self.cfg = cfg

    def _url(self, path: str) -> str:
        return f"{self.cfg.base_url.rstrip('/')}/{path.lstrip('/')}"

    async def _headers(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        headers = {"x-request-id": request_id_var.get(), **(extra or {})}
        if self.cfg.scope:
            headers["Authorization"] = f"Bearer {await auth.obo.get_token(self.cfg.scope)}"
        else:
            log.debug("backend=%s has no scope configured; calling without Authorization", self.name)
        return headers

    def _fail(self, method: str, url: str, status: int, body: str) -> ToolError:
        log.error("backend=%s %s %s -> %s body=%s", self.name, method, url, status, truncate(body))
        return ToolError(
            f"Backend '{self.name}' returned HTTP {status} for {method} {url}: {truncate(body, 300)}"
        )

    async def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: Any = None,
        headers: dict[str, str] | None = None,
    ) -> Any:
        """Call a JSON API. Returns the parsed JSON body (or text if the response is not JSON)."""
        url = self._url(path)
        request_headers = await self._headers(headers)
        log.info("backend=%s -> %s %s params=%s", self.name, method, url, params)
        if get_settings().log_payloads and json is not None:
            log.debug("backend=%s request body=%s", self.name, truncate(jsonlib.dumps(json, default=str)))

        started = time.perf_counter()
        try:
            response = await _client().request(
                method, url, params=params, json=json, headers=request_headers, timeout=self.cfg.timeout
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

        try:
            return response.json()
        except ValueError:
            return response.text

    async def get(self, path: str, **kwargs: Any) -> Any:
        return await self.request("GET", path, **kwargs)

    async def post(self, path: str, **kwargs: Any) -> Any:
        return await self.request("POST", path, **kwargs)

    async def stream_sse(
        self,
        method: str,
        path: str,
        *,
        ctx: Context | None = None,
        params: dict[str, Any] | None = None,
        json: Any = None,
        headers: dict[str, str] | None = None,
        extract: Callable[[ServerSentEvent], str | None] = default_sse_extract,
    ) -> str:
        """Consume an SSE endpoint.

        Each chunk is forwarded to the MCP client as a progress notification (live updates in
        LangGraph); the concatenated text is returned as the tool result for the LLM.
        """
        url = self._url(path)
        request_headers = await self._headers(headers)
        log.info("backend=%s -> %s %s (SSE)", self.name, method, url)
        timeout = httpx.Timeout(self.cfg.timeout, read=self.cfg.sse_read_timeout)

        started = time.perf_counter()
        chunks: list[str] = []
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
                    chunk = extract(event)
                    if chunk:
                        chunks.append(chunk)
                        if ctx is not None:
                            await ctx.report_progress(progress=len(chunks), message=chunk)
        except ToolError:
            raise
        except Exception as exc:  # httpx errors, non-SSE content type, broken stream
            log.exception("backend=%s SSE stream failed after %d events", self.name, events)
            raise ToolError(
                f"Backend '{self.name}' SSE stream {method} {url} failed after {events} events: {exc!r}"
            ) from exc

        text = "".join(chunks)
        log.info(
            "backend=%s <- SSE %s %s done: %d events, %d chars in %.0fms",
            self.name, method, url, events, len(text), (time.perf_counter() - started) * 1000,
        )  # fmt: skip
        return text


def backend(name: str) -> BackendClient:
    """Client for a backend configured via BACKENDS__<NAME>__BASE_URL / __SCOPE."""
    backends = get_settings().backends
    cfg = backends.get(name.lower())
    if cfg is None:
        raise ToolError(
            f"Backend '{name}' is not configured. Set BACKENDS__{name.upper()}__BASE_URL "
            f"(configured: {sorted(backends) or 'none'})."
        )
    return BackendClient(name.lower(), cfg)
