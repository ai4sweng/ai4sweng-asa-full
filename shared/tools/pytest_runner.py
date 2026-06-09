"""
Subprocess pytest runner for external demonstration repositories.
"""

from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass
class PytestResult:
    """Captured pytest execution outcome."""

    success: bool
    return_code: int
    stdout: str
    stderr: str
    passed: int
    failed: int
    duration_sec: float


class PytestRunner:
    """Run pytest in a target repository directory."""

    def __init__(self, repo_path: str, *, timeout_sec: float = 120.0) -> None:
        self._root = Path(repo_path).resolve()
        self._timeout = timeout_sec

    def run(self, *, extra_args: list[str] | None = None) -> PytestResult:
        if not self._root.is_dir():
            raise FileNotFoundError(f"Repository not found: {self._root}")

        venv_python = self._root / ".venv" / "bin" / "python"
        python = str(venv_python) if venv_python.is_file() else sys.executable

        cmd = [
            python,
            "-m",
            "pytest",
            "tests/",
            "-q",
            "--tb=short",
        ]
        if extra_args:
            cmd.extend(extra_args)

        completed = subprocess.run(
            cmd,
            cwd=str(self._root),
            capture_output=True,
            text=True,
            timeout=self._timeout,
        )

        stdout = completed.stdout or ""
        stderr = completed.stderr or ""
        passed, failed = self._parse_counts(stdout)
        duration = self._parse_duration(stdout)

        return PytestResult(
            success=completed.returncode == 0,
            return_code=completed.returncode,
            stdout=stdout[-8000:],
            stderr=stderr[-4000:],
            passed=passed,
            failed=failed,
            duration_sec=duration,
        )

    @staticmethod
    def _parse_counts(stdout: str) -> tuple[int, int]:
        passed = failed = 0
        for line in stdout.splitlines():
            line = line.strip()
            if " in " not in line:
                continue
            parts = line.replace(",", "").split()
            for i, part in enumerate(parts):
                if part == "passed" and i > 0 and parts[i - 1].isdigit():
                    passed = int(parts[i - 1])
                if part == "failed" and i > 0 and parts[i - 1].isdigit():
                    failed = int(parts[i - 1])
        return passed, failed

    @staticmethod
    def _parse_duration(stdout: str) -> float:
        for line in stdout.splitlines():
            if " in " in line and "s" in line:
                try:
                    fragment = line.split(" in ")[-1].strip().rstrip("s")
                    return float(fragment)
                except ValueError:
                    continue
        return 0.0
