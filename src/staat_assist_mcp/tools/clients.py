"""Client lookup tools."""

from typing import Annotated, Any

from pydantic import Field

from ..app import mcp
from ..backend import backend


@mcp.tool
async def lookup_client(
    email: Annotated[str, Field(description="Email address of the client to look up")],
) -> dict[str, Any]:
    """Resolve a client's email address into client info (name, ids, ...).

    One email can match several clients. When `match_count` is greater than 1, show the
    candidates to the user and ask which one they mean before using a client in other tools.
    """
    # ASSUMPTION: GET /find-contact?email=... returning one object, a list, or a wrapper object
    # with the list under "contacts"/"clients"/"results"/"items". Adjust to the real contract.
    data = await backend("someservice").get("/find-contact", params={"email": email})

    if isinstance(data, dict):
        for key in ("contacts", "clients", "results", "items"):
            if isinstance(data.get(key), list):
                data = data[key]
                break
    clients = data if isinstance(data, list) else [data] if data else []

    return {"email": email, "match_count": len(clients), "clients": clients}
