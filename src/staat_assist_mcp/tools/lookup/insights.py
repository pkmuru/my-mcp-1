"""Insight document lookup: fetches a client's document from STAAT insights (backend `staat_insights`)."""

import json
from typing import Annotated

from fastmcp.exceptions import ToolError
from pydantic import Field

from ...app import mcp
from ...auth import current_user
from ...backend import BackendError, backend
from ...config import get_settings
from ._common import READ_ONLY, TAGS
from ._markdown import html_to_markdown, json_to_markdown


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


def _to_markdown(raw: str, relationship_id: str) -> str:
    """The document as markdown. STAAT insights sends HTML, either as it is or as a JSON string;
    older builds send a JSON object or list, which is rendered as headed sections."""
    if raw[:1] not in ("{", "[", '"'):
        return html_to_markdown(raw)
    try:
        payload = json.loads(raw)
    except ValueError:
        return html_to_markdown(raw)  # HTML that happens to start like JSON
    if isinstance(payload, str):
        return html_to_markdown(payload)
    if not payload:
        return ""  # {}, [] or null: no document
    first = payload[0] if isinstance(payload, list) else payload
    name = first.get("leadClientName") if isinstance(first, dict) else None
    title = name.strip() if isinstance(name, str) and name.strip() else relationship_id
    return json_to_markdown(title, payload)


@mcp.tool(tags=TAGS, annotations=READ_ONLY)
async def lookup_insight_document(
    client_id: Annotated[str, Field(min_length=1, description="Client id, as returned by lookup_contacts")],
) -> str:
    """Get one client's insight document (retention pack) from STAAT insights, as markdown.

    Needs the client id: use `lookup_contacts` first when you only have a name or phone number.
    Fails with a "not found" error when STAAT insights holds no relationship or document for the client.
    """
    gpn = current_user().gpn
    if not gpn:
        raise ToolError("The signed-in user's token has no GPN claim, which STAAT insights requires.")

    # Step 1: client id -> relationship id (older services call it the ACE id), as plain text.
    relationship_id = await _post("/getRelationshipIdByClientId", gpn, {"client-id": client_id.strip()})
    if not relationship_id:
        raise ToolError("Not found: STAAT insights holds no relationship for this client.")

    # Step 2: relationship id -> document.
    raw = await _post("/insightsByRelationshipId", gpn, {"relationship-id": relationship_id})
    markdown = _to_markdown(raw, relationship_id) if raw else ""
    if not markdown.strip():
        raise ToolError("Not found: STAAT insights holds no document for this client.")
    return markdown
