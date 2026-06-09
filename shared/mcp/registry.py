"""MCP tool registry — register, list, and call tools.

Each tool is a named async coroutine with a JSON-Schema ``inputSchema``.
The registry exposes them via the MCP HTTP protocol so any MCP-compatible
client can discover and invoke them.

Built-in tools (auto-registered on first ``get_registry()`` call):
  • filesystem.read_file
  • filesystem.list_directory
  • filesystem.write_file
  • shell.run_command

Custom tools can be registered at any time with ``registry.register()``.
"""
from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable

ToolFn = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]


class MCPTool:
    """A single MCP-compatible tool definition."""

    def __init__(
        self,
        name: str,
        description: str,
        input_schema: dict[str, Any],
        fn: ToolFn,
    ) -> None:
        self.name = name
        self.description = description
        self.input_schema = input_schema
        self._fn = fn

    async def call(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """Execute the tool and return ``{content: [{type, text}]}``."""
        result = await self._fn(arguments)
        if isinstance(result, str):
            text = result
        else:
            import json
            text = json.dumps(result, ensure_ascii=False, indent=2)
        return {"content": [{"type": "text", "text": text}]}

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": self.input_schema,
        }


class MCPToolRegistry:
    """Thread-safe registry for MCP tools."""

    def __init__(self) -> None:
        self._tools: dict[str, MCPTool] = {}

    def register(
        self,
        name: str,
        description: str,
        input_schema: dict[str, Any],
        fn: ToolFn,
    ) -> None:
        """Register a new tool. Overwrites any existing tool with the same name."""
        self._tools[name] = MCPTool(name, description, input_schema, fn)

    def list_tools(self) -> list[dict[str, Any]]:
        """Return MCP-compatible tools/list response body."""
        return {"tools": [t.to_dict() for t in self._tools.values()]}

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Execute a registered tool by name.

        Returns MCP tools/call response: ``{content: [{type, text}]}``.
        Raises KeyError if the tool is not registered.
        """
        tool = self._tools.get(name)
        if tool is None:
            raise KeyError(f"Tool '{name}' is not registered")
        return await tool.call(arguments)

    def __contains__(self, name: str) -> bool:
        return name in self._tools


# ------------------------------------------------------------------
# Singleton with built-in tools auto-registered
# ------------------------------------------------------------------

_registry: MCPToolRegistry | None = None


def get_registry() -> MCPToolRegistry:
    """Return the process-wide MCPToolRegistry with all built-in tools loaded."""
    global _registry
    if _registry is None:
        _registry = MCPToolRegistry()
        _register_builtins(_registry)
    return _registry


def _register_builtins(reg: MCPToolRegistry) -> None:
    from shared.mcp.tools.filesystem import read_file, list_directory, write_file
    from shared.mcp.tools.shell import run_command

    reg.register(
        name="filesystem.read_file",
        description="Read the text content of a file at the given absolute or relative path.",
        input_schema={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path to read"},
                "max_chars": {
                    "type": "integer",
                    "description": "Maximum characters to return (default 8000)",
                    "default": 8000,
                },
            },
            "required": ["path"],
        },
        fn=read_file,
    )

    reg.register(
        name="filesystem.list_directory",
        description="List files and directories at the given path (non-recursive by default).",
        input_schema={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Directory path to list"},
                "recursive": {
                    "type": "boolean",
                    "description": "Recurse into subdirectories (default false)",
                    "default": False,
                },
                "pattern": {
                    "type": "string",
                    "description": "Glob pattern filter e.g. '*.py' (optional)",
                },
            },
            "required": ["path"],
        },
        fn=list_directory,
    )

    reg.register(
        name="filesystem.write_file",
        description="Write text content to a file (creates parent directories as needed).",
        input_schema={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Destination file path"},
                "content": {"type": "string", "description": "Text to write"},
                "append": {
                    "type": "boolean",
                    "description": "Append instead of overwrite (default false)",
                    "default": False,
                },
            },
            "required": ["path", "content"],
        },
        fn=write_file,
    )

    reg.register(
        name="shell.run_command",
        description=(
            "Run a shell command in the given working directory. "
            "Captures stdout/stderr. Timeout default 30 s."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "Shell command to execute"},
                "cwd": {
                    "type": "string",
                    "description": "Working directory (default: current dir)",
                },
                "timeout": {
                    "type": "integer",
                    "description": "Seconds before timeout (max 120)",
                    "default": 30,
                },
            },
            "required": ["command"],
        },
        fn=run_command,
    )
