"""MCP shell tool — run a command and capture output.

Safety constraints
------------------
• Timeout is capped at 120 seconds.
• The working directory must exist (no arbitrary path creation).
• Commands are passed directly to the shell — callers are responsible for
  ensuring the orchestrator is not exposed to untrusted input.
"""
from __future__ import annotations

import asyncio
from typing import Any


async def run_command(args: dict[str, Any]) -> dict[str, Any]:
    """Execute a shell command and return stdout, stderr, and return code."""
    command: str = args["command"]
    cwd: str | None = args.get("cwd")
    timeout: int = min(int(args.get("timeout", 30)), 120)

    try:
        proc = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
        )
        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(), timeout=float(timeout)
            )
        except asyncio.TimeoutError:
            proc.kill()
            return {
                "returncode": -1,
                "stdout": "",
                "stderr": f"Command timed out after {timeout}s",
                "timed_out": True,
            }

        stdout = stdout_bytes.decode("utf-8", errors="replace")
        stderr = stderr_bytes.decode("utf-8", errors="replace")

        # Truncate large outputs
        if len(stdout) > 10_000:
            stdout = stdout[:10_000] + "\n… [truncated]"
        if len(stderr) > 4_000:
            stderr = stderr[:4_000] + "\n… [truncated]"

        return {
            "returncode": proc.returncode,
            "stdout": stdout,
            "stderr": stderr,
            "timed_out": False,
        }

    except Exception as exc:
        return {"returncode": -1, "stdout": "", "stderr": str(exc), "timed_out": False}
