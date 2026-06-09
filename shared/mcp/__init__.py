"""shared.mcp — Model Context Protocol tool registry and server.

Exposes filesystem, shell and extensible custom tools over a standard MCP
HTTP interface.  External MCP clients (Claude Desktop, Cursor, Continue.dev)
can connect to the orchestrator's ``/mcp`` endpoint.

Endpoint summary
----------------
  GET  /mcp/tools           — list all registered tools (MCP tools/list)
  POST /mcp/tools/call      — execute a tool  (MCP tools/call)
"""
from shared.mcp.registry import MCPToolRegistry, get_registry

__all__ = ["MCPToolRegistry", "get_registry"]
