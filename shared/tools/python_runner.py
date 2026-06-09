"""
Python code runner for DebuggerAgent.

Executes generated source code in an isolated subprocess and captures
errors for debug report generation.
"""

import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path


@dataclass
class RunResult:
    """Result of executing Python source code."""

    success: bool
    stdout: str
    stderr: str
    error_type: str | None = None
    failing_line: int | None = None
    traceback: str | None = None


class PythonRunner:
    """Execute Python source and extract failure diagnostics."""

    def run(self, source_code: str) -> RunResult:
        """Run source in subprocess and parse any failure."""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".py", delete=False, encoding="utf-8"
        ) as f:
            f.write(source_code)
            f.write("\n\n# Auto-invoke for demo\n")
            f.write("if __name__ == '__main__':\n")
            f.write("    result = fibonacci_up_to(100)\n")
            f.write("    print(result)\n")
            path = Path(f.name)

        try:
            proc = subprocess.run(
                [sys.executable, str(path)],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if proc.returncode == 0:
                return RunResult(success=True, stdout=proc.stdout, stderr=proc.stderr)

            return self._parse_failure(proc.stderr, proc.stdout)
        except subprocess.TimeoutExpired:
            return RunResult(
                success=False,
                stdout="",
                stderr="Execution timed out",
                error_type="TimeoutError",
            )
        finally:
            path.unlink(missing_ok=True)

    def _parse_failure(self, stderr: str, stdout: str) -> RunResult:
        """
        Extract error type and failing line from stderr traceback text.

        Heuristic line scanning — not a full traceback parser; sufficient for
        the demo's deterministic IndexError output from subprocess execution.
        """
        error_type = None
        failing_line = None
        for line in stderr.splitlines():
            if ": " in line and "Error" in line:
                parts = line.split(":", 1)
                error_type = parts[0].strip().split()[-1] if parts else "Error"
            if 'File "' in line and ", line " in line:
                try:
                    failing_line = int(line.split(", line ")[1].split(",")[0])
                except (IndexError, ValueError):
                    pass

        return RunResult(
            success=False,
            stdout=stdout,
            stderr=stderr,
            error_type=error_type or "ExecutionError",
            failing_line=failing_line,
            traceback=stderr,
        )
