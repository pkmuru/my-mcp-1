"""Contact lookup: searches the contact index in Azure AI Search (backend `contact_search`)."""

import logging
from typing import Annotated, Any

from fastmcp.exceptions import ToolError
from pydantic import Field

from ...app import mcp
from ...auth import current_user
from ...backend import backend
from ._common import READ_ONLY, TAGS, allowed_splits, compact
from ._contact_search import SEARCH_FIELDS, build_search_request, to_contact

log = logging.getLogger(__name__)

allowed_splits(None)  # reads the user splits file now, so a missing or broken file stops startup

SEARCH_API_VERSION = "2024-07-01"
PREFIX_HINT = 'A trailing * does a prefix match, e.g. "JAM*".'


@mcp.tool(tags=TAGS, annotations=READ_ONLY)
async def lookup_contacts(
    text: Annotated[
        str | None,
        Field(description=f"Free text searched across all contact fields, e.g. a full name. {PREFIX_HINT}"),
    ] = None,
    client_id: Annotated[str | None, Field(description="Exact client id")] = None,
    first_name: Annotated[str | None, Field(description=f"First name. {PREFIX_HINT}")] = None,
    last_name: Annotated[str | None, Field(description=f"Last name. {PREFIX_HINT}")] = None,
    phone: Annotated[
        str | None, Field(description="Phone number without area code; only the digits count")
    ] = None,
    area_code: Annotated[str | None, Field(description="Phone area code")] = None,
    top: Annotated[int, Field(ge=1, le=50, description="Maximum number of contacts to return")] = 10,
    skip: Annotated[int, Field(ge=0, le=100_000, description="Contacts to skip, for paging")] = 0,
) -> dict[str, Any]:
    """Search the clients (contacts) the user is allowed to see, by free text and/or specific attributes.

    Give `text`, one or more attributes, or both; everything given must match. Each contact comes
    with its client id, name, status, emails, addresses, phones and accounts.

    `total` is the number of matches overall, `returned` the number in this result; page with `skip`.
    When more than one contact matches, show the candidates to the user and ask which one they mean
    before using a `clientId` in another tool.
    """
    given = {"client_id": client_id, "first_name": first_name, "last_name": last_name, "phone": phone, "area_code": area_code}  # fmt: skip
    attributes = {}
    for name, value in given.items():
        if value and value.strip():
            normalize = SEARCH_FIELDS[name][1]
            attributes[name] = normalize(value.strip())
            if not attributes[name]:
                raise ToolError(f"`{name}` has nothing to search for (e.g. a phone number without digits).")
    text = (text or "").strip() or None
    if not text and not attributes:
        raise ToolError(
            "Provide `text` and/or at least one of: client_id, first_name, last_name, phone, area_code."
        )

    user = current_user()
    splits = allowed_splits(user.id)
    if not splits:
        log.warning("Access denied for %s: no split access", user.id or "<no user id in token>")
        raise ToolError("The signed-in user is not provisioned for any split, so no contacts can be shown.")

    data = await backend("contact_search").post(
        "/docs/search",
        params={"api-version": SEARCH_API_VERSION},
        json=build_search_request(text, attributes, splits, top, skip),
    )

    contacts = [compact(to_contact(document, splits)) for document in data.get("value") or []]
    total = data.get("@odata.count", len(contacts))
    # Log the shape of the search, never the values (names and phone numbers are personal data).
    log.info(
        "Contact search by %s: attributes %s, text length %d -> %d of %d",
        user.id, sorted(attributes), len(text or ""), len(contacts), total,
    )  # fmt: skip
    return {"total": total, "returned": len(contacts), "skip": skip, "contacts": contacts}
