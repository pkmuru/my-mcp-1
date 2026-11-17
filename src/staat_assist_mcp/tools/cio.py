"""CIO (Chief Investment Office) tools."""

from typing import Annotated

from fastmcp import Context
from pydantic import Field

from ..app import mcp
from ..backend import backend


@mcp.tool(tags={"cio"}, annotations={"readOnlyHint": True})
async def ask_cio(
    question: Annotated[str, Field(description="The CIO-related question, in natural language")],
    ctx: Context,
) -> str:
    """Answer any CIO (Chief Investment Office) specific question: house view, market outlook,
    asset allocation, investment themes and CIO research. Returns the CIO assistant's answer.
    """
    # The backend streams the answer over SSE. Each chunk is relayed to the agent as a progress
    # notification; the full text is the tool result.
    # ASSUMPTION: the path and the {"question": ...} body; adjust to the real contract.
    answer = await backend("cio").stream("POST", "/cio/chat/streaming", json={"question": question}, ctx=ctx)
    return answer.text or "The CIO service returned an empty answer."
