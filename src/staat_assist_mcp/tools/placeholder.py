"""Template for a new tool. Copy this file, rename it, and add the module to tools/__init__.py.

The function name is the tool name, the docstring is what the LLM reads to decide when to call
it, and the type hints become the input schema.
"""

from typing import Annotated, Any

from pydantic import Field

from ..app import mcp


@mcp.tool
async def placeholder_tool(
    text: Annotated[str, Field(description="Any text")],
) -> dict[str, Any]:
    """Placeholder tool that is not implemented yet. Do not call it."""
    # Typical body:
    #   return await backend("someservice").get("/some/path", params={"q": text})
    #   return await backend("someservice").post("/some/path", json={"q": text})
    #   return await backend("someservice").stream_sse("POST", "/some/stream", json={...}, ctx=ctx)
    return {"status": "not_implemented", "echo": text}
