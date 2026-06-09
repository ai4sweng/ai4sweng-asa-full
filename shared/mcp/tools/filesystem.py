"""MCP filesystem tools — read, list, and write files."""
from __future__ import annotations

import fnmatch
import os
from pathlib import Path
from typing import Any


async def read_file(args: dict[str, Any]) -> str:
    """Return up to *max_chars* characters of a file's text content."""
    path = Path(args["path"])
    max_chars: int = int(args.get("max_chars", 8000))

    if not path.exists():
        return f"ERROR: File not found: {path}"
    if not path.is_file():
        return f"ERROR: Path is not a file: {path}"

    try:
        text = path.read_text(encoding="utf-8", errors="replace")
        if len(text) > max_chars:
            text = text[:max_chars] + f"\n\n… [truncated at {max_chars} chars]"
        return text
    except OSError as exc:
        return f"ERROR reading {path}: {exc}"


async def list_directory(args: dict[str, Any]) -> list[dict[str, Any]]:
    """Return a list of entries under *path*, optionally filtered by glob *pattern*."""
    root = Path(args["path"])
    recursive: bool = bool(args.get("recursive", False))
    pattern: str | None = args.get("pattern")

    if not root.exists():
        return [{"error": f"Path not found: {root}"}]
    if not root.is_dir():
        return [{"error": f"Not a directory: {root}"}]

    entries: list[dict[str, Any]] = []
    walker = root.rglob("*") if recursive else root.iterdir()

    for entry in sorted(walker):
        name = entry.name
        if pattern and not fnmatch.fnmatch(name, pattern):
            continue
        rel = entry.relative_to(root)
        entries.append({
            "path": str(rel),
            "type": "dir" if entry.is_dir() else "file",
            "size": entry.stat().st_size if entry.is_file() else None,
        })
        if len(entries) >= 500:
            entries.append({"note": "Listing truncated at 500 entries"})
            break

    return entries


async def write_file(args: dict[str, Any]) -> str:
    """Write *content* to *path*; creates parent directories as needed."""
    path = Path(args["path"])
    content: str = args["content"]
    append: bool = bool(args.get("append", False))

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        mode = "a" if append else "w"
        path.write_text(content, encoding="utf-8") if not append else \
            path.open("a", encoding="utf-8").write(content)
        return f"OK: wrote {len(content)} chars to {path}"
    except OSError as exc:
        return f"ERROR writing {path}: {exc}"
