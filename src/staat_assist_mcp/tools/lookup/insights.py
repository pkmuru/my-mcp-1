"""Insight lookups against STAAT insights (backend `staat_insights`): a client's relationship id, and the
relationship's insight document as markdown, plain text or HTML."""

import json
import logging
import time
from collections.abc import Callable
from typing import Annotated, Any, Literal

from fastmcp.exceptions import ToolError
from pydantic import Field

from ...app import mcp
from ...auth import current_user
from ...backend import BackendError, backend
from ...config import get_settings
from ._common import READ_ONLY, TAGS
from ._markdown import html_to_markdown, json_to_markdown
from ._text import html_to_text, json_to_text

log = logging.getLogger(__name__)

Format = Literal["markdown", "text", "html"]

ClientId = Annotated[str, Field(min_length=1, description="Client id, as returned by lookup_contacts")]
Gpn = Annotated[
    str | None,
    Field(
        description="GPN of the financial advisor. Leave empty to use the signed-in user's GPN; "
        "set it only when the user gives another advisor's GPN."
    ),
]

# How each format renders HTML (the usual payload) and JSON (older builds). html keeps JSON as sent.
_FROM_HTML: dict[str, Callable[[str], str]] = {
    "markdown": html_to_markdown,
    "text": html_to_text,
    "html": lambda html: html,
}
_FROM_JSON: dict[str, Callable[[str, Any], str]] = {"markdown": json_to_markdown, "text": json_to_text}


def _resolve_gpn(gpn: str | None) -> tuple[str, str]:
    """The financial advisor's GPN and where it came from: the tool argument, or else the user's token."""
    if gpn and gpn.strip():
        return gpn.strip(), "argument"
    token_gpn = current_user().gpn
    if not token_gpn:
        raise ToolError(
            "The signed-in user's token has no GPN claim, which STAAT insights requires. "
            "Pass the financial advisor's GPN as `gpn`."
        )
    return token_gpn, "token"


async def _post(path: str, gpn: str, body: dict[str, str]) -> str:
    """POST to STAAT insights and return the response text. "" when it answers 404 or with nothing,
    its two ways of saying "not found"."""
    product = get_settings().insights_product
    try:
        text = await backend("staat_insights").post(
            path,
            json={"fa-gpn": gpn, **body, "productType": product},
            # STAAT insights authorizes on the financial advisor's GPN in this header.
            headers={"X_USER_GPN": gpn, "product": product},
            raw=True,
        )
    except BackendError as error:
        if error.status == 404:
            return ""
        raise
    return text.strip()


def _elapsed_ms(started: float) -> float:
    return (time.perf_counter() - started) * 1000


async def _relationship_id(gpn: str, gpn_source: str, client_id: str) -> str:
    """Client id -> relationship id (older services call it the ACE id), sent as plain text."""
    started = time.perf_counter()
    relationship_id = await _post("/getRelationshipIdByClientId", gpn, {"client-id": client_id})
    log.info(
        "Relationship id lookup by %s for client %s (GPN from %s): %s in %.0f ms",
        current_user().id, client_id, gpn_source, "found" if relationship_id else "not found",
        _elapsed_ms(started),
    )  # fmt: skip
    if not relationship_id:
        raise ToolError("Not found: STAAT insights holds no relationship for this client.")
    return relationship_id


def _render(raw: str, relationship_id: str, fmt: Format) -> str:
    """The document in `fmt`; "" when there is none. STAAT insights sends HTML, either as it is or as a
    JSON string; older builds send a JSON object or list, which html returns as sent."""
    if raw[:1] not in ("{", "[", '"'):
        return _FROM_HTML[fmt](raw)
    try:
        # Numbers stay as written: "1000.50", not 1000.5.
        payload = json.loads(raw, parse_float=str)
    except ValueError:
        return _FROM_HTML[fmt](raw)  # HTML that happens to start like JSON
    if isinstance(payload, str):
        return _FROM_HTML[fmt](payload)
    if not payload:
        return ""  # {}, [] or null: no document
    if fmt == "html":
        return raw
    first = payload[0] if isinstance(payload, list) else payload
    name = first.get("leadClientName") if isinstance(first, dict) else None
    title = name.strip() if isinstance(name, str) and name.strip() else relationship_id
    return _FROM_JSON[fmt](title, payload)


async def _document(gpn: str, relationship_id: str, fmt: Format) -> str:
    """Relationship id -> document in `fmt`."""
    started = time.perf_counter()
    raw = await _post("/insightsByRelationshipId", gpn, {"relationship-id": relationship_id})
    document = _render(raw, relationship_id, fmt) if raw else ""
    found = bool(document.strip())
    log.info(
        "Insight document lookup by %s for relationship %s as %s: %s, %d chars in %.0f ms",
        current_user().id, relationship_id, fmt, "found" if found else "not found",
        len(document) if found else 0, _elapsed_ms(started),
    )  # fmt: skip
    if not found:
        raise ToolError("Not found: STAAT insights holds no document for this client.")
    return document


@mcp.tool(tags=TAGS, annotations=READ_ONLY)
async def lookup_relationship_id(client_id: ClientId, gpn: Gpn = None) -> str:
    """Get the relationship id STAAT insights holds for one client (older services call it the ACE id).

    Needs the client id: use `lookup_contacts` first when you only have a name or phone number.
    `lookup_insight_document` finds the relationship id itself; use this tool only when you need the id.
    Fails with a "not found" error when STAAT insights holds no relationship for the client.
    """
    advisor_gpn, gpn_source = _resolve_gpn(gpn)
    return await _relationship_id(advisor_gpn, gpn_source, client_id.strip())


@mcp.tool(tags=TAGS, annotations=READ_ONLY)
async def lookup_insight_document(
    client_id: ClientId,
    format: Annotated[
        Format,
        Field(
            description="markdown (default): headings, lists and tables, best for reading and reasoning; "
            "text: plain text, the smallest; html: the original document"
        ),
    ] = "markdown",
    gpn: Gpn = None,
) -> str:
    """Get one client's insight document (retention pack) from STAAT insights, as markdown by default.

    Needs the client id: use `lookup_contacts` first when you only have a name or phone number.
    Fails with a "not found" error when STAAT insights holds no relationship or document for the client.
    """
    advisor_gpn, gpn_source = _resolve_gpn(gpn)
    relationship_id = await _relationship_id(advisor_gpn, gpn_source, client_id.strip())
    return await _document(advisor_gpn, relationship_id, format)
