"""
Static repository scanner for the repo-audit workflow.

Walks a target codebase and collects structure, risky patterns, and file metrics.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class RepoScanResult:
    """Aggregated static analysis of a repository."""

    repo_path: str
    python_files: list[str] = field(default_factory=list)
    modules: list[str] = field(default_factory=list)
    test_files: list[str] = field(default_factory=list)
    findings: list[dict[str, Any]] = field(default_factory=list)
    metrics: dict[str, int] = field(default_factory=dict)


# Heuristic patterns aligned with ai4sweng-fastapi-demo intentional bugs.
_RISK_PATTERNS: list[tuple[str, str, str]] = [
    ("sql_injection", r'f"SELECT.*\{', "Possible SQL string interpolation"),
    ("null_access", r"\.profile\.", "Direct profile attribute access"),
    ("off_by_one", r"range\(1,\s*len\(", "Loop starts at index 1 (off-by-one risk)"),
    ("credential_log", r"password=%s", "Password may be logged"),
    ("n_plus_one", r"for product in products", "Per-item query loop (N+1 risk)"),
]


class RepoScanner:
    """Filesystem scanner for demonstration repositories."""

    def __init__(self, repo_path: str) -> None:
        self._root = Path(repo_path).resolve()

    def scan(self) -> RepoScanResult:
        if not self._root.is_dir():
            raise FileNotFoundError(f"Repository not found: {self._root}")

        result = RepoScanResult(repo_path=str(self._root))
        self._root / "app"
        tests_dir = self._root / "tests"

        for path in self._root.rglob("*.py"):
            rel = str(path.relative_to(self._root))
            if rel.startswith(".venv") or "__pycache__" in rel:
                continue
            result.python_files.append(rel)
            if rel.startswith("tests/"):
                result.test_files.append(rel)
            if rel.startswith("app/") and "/__init__.py" not in rel:
                parts = Path(rel).parts
                if len(parts) >= 2:
                    result.modules.append(parts[1])

        result.modules = sorted(set(result.modules))
        result.metrics = {
            "python_file_count": len(result.python_files),
            "test_file_count": len(result.test_files),
            "module_count": len(result.modules),
        }

        for path in self._root.rglob("*.py"):
            rel = str(path.relative_to(self._root))
            if ".venv" in rel or "__pycache__" in rel:
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except OSError:
                continue
            for kind, pattern, message in _RISK_PATTERNS:
                for match in re.finditer(pattern, text):
                    line_no = text[: match.start()].count("\n") + 1
                    result.findings.append(
                        {
                            "kind": kind,
                            "file": rel,
                            "line": line_no,
                            "message": message,
                            "snippet": match.group(0)[:120],
                        }
                    )

        # Known missing test gaps in the demo repo.
        auth_test_path = tests_dir / "test_auth.py"
        if auth_test_path.is_file():
            auth_tests = auth_test_path.read_text(encoding="utf-8")
            if "reset_password" not in auth_tests and "reset-password" not in auth_tests:
                result.findings.append(
                    {
                        "kind": "missing_test",
                        "file": "tests/test_auth.py",
                        "line": 0,
                        "message": "No unit test for reset-password endpoint",
                        "snippet": "POST /api/v1/auth/reset-password",
                    }
                )

        return result

    def scan_file(self, relative_path: str) -> list[dict[str, Any]]:
        """Lightweight regex scan for a single file (per-file parse fallback)."""
        path = self._root / relative_path
        if not path.is_file():
            return []

        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            return []

        rel = str(path.relative_to(self._root))
        findings: list[dict[str, Any]] = []
        for kind, pattern, message in _RISK_PATTERNS:
            for match in re.finditer(pattern, text):
                line_no = text[: match.start()].count("\n") + 1
                findings.append(
                    {
                        "kind": kind,
                        "file": rel,
                        "line": line_no,
                        "message": message,
                        "snippet": match.group(0)[:120],
                        "detection_source": "regex_fallback",
                        "parse_repaired": False,
                    }
                )
        return findings

    def to_analysis_dict(self, scan: RepoScanResult) -> dict[str, Any]:
        """Serialize scan for REPO_ANALYSIS artifact."""
        return {
            "repo_path": scan.repo_path,
            "modules": scan.modules,
            "python_files": scan.python_files[:30],
            "test_files": scan.test_files,
            "metrics": scan.metrics,
            "findings": scan.findings,
            "summary": (
                f"Scanned {scan.metrics.get('python_file_count', 0)} Python files "
                f"across {scan.metrics.get('module_count', 0)} modules; "
                f"{len(scan.findings)} static findings."
            ),
        }
