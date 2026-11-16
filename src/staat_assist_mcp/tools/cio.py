"""CIO (Chief Investment Office) tools."""

from typing import Annotated

from fastmcp import Context
from pydantic import Field

from ..app import mcp
from ..backend import backend


@mcp.tool
async def ask_cio(
    question: Annotated[str, Field(description="The CIO-related question, in natural language")],
    ctx: Context,
) -> str:
    """Answer any CIO (Chief Investment Office) specific question: house view, market outlook,
    asset allocation, investment themes and CIO research. Returns the CIO assistant's answer.
    """
    # The backend streams the answer over SSE. Chunks are relayed to the MCP client as progress
    # notifications while the full text is collected and returned as the tool result.
    # ASSUMPTION: POST with {"question": ...}; adjust the body (and pass `extract=` if the
    # event payload is not plain text / {"content": ...}) to the real contract.
    answer = await backend("someservice").stream_sse(
        "POST", "/cio/chat/streaming", json={"question": question}, ctx=ctx
    )
    return answer or "The CIO service returned an empty answer."
