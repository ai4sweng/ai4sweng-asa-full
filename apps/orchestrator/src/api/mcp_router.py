"""MCP (Model Context Protocol) endpoint.

Exposes the platform's tool registry over the standard MCP HTTP interface so
external MCP clients (Claude Desktop, Cursor, Continue.dev, etc.) can connect
to this orchestrator and invoke tools.

Endpoints
---------
  GET  /mcp/tools           — list all registered tools
  POST /mcp/tools/call      — execute a tool by name

Authentication
--------------
All endpoints require a valid Bearer JWT (same as /workflow/* endpoints).

MCP client configuration (Claude Desktop example)::

    {
      "mcpServers": {
        "kio1": {
          "url": "http://localhost:8000/mcp",
          "headers": { "Authorization": "Bearer <your-jwt>" }
        }
      }
    }
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from shared.auth.dependencies import get_current_user
from shared.auth.schemas import UserInfo
from shared.mcp.registry import get_registry

router = APIRouter(
    prefix="/mcp",
    tags=["mcp"],
    dependencies=[Depends(get_current_user)],
)


class ToolCallRequest(BaseModel):
    name: str
    arguments: dict[str, Any] = {}


@router.get("/tools")
async def list_tools(_: UserInfo = Depends(get_current_user)):
    """Return all registered MCP tools (MCP tools/list response format)."""
    return get_registry().list_tools()


@router.post("/tools/call")
async def call_tool(req: ToolCallRequest, _: UserInfo = Depends(get_current_user)):
    """Execute a registered tool by name (MCP tools/call response format).

    Returns ``{content: [{type: "text", text: "..."}]}`` on success.
    Returns 404 if the tool name is not registered.
    """
    try:
        return await get_registry().call_tool(req.name, req.arguments)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Tool '{req.name}' not found")
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Tool execution failed: {exc}")
