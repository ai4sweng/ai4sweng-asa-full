"""
Build compact per-file context for LLM-driven repository analysis.

Lists Python files, skips vendor/cache dirs, and truncates large sources so
prompts stay within limits for local models (e.g. qwen2.5-coder:3b on 16 GB RAM).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

IGNORED_DIR_NAMES: frozenset[str] = frozenset(
    {
        ".venv",
        "__pycache__",
        ".git",
        "node_modules",
    }
)


@dataclass(frozen=True)
class FileContext:
    """Compact excerpt of one Python file for a single LLM call."""

    file_path: str
    size_bytes: int
    excerpt: str
    truncated: bool
    line_count: int


@dataclass(frozen=True)
class RepoInventory:
    """Repository file listing and module summary."""

    repo_path: str
    python_files: list[str]
    test_files: list[str]
    modules: list[str]
    metrics: dict[str, int]


class RepoContextBuilder:
    """Discover and excerpt Python files from a target repository."""

    def __init__(
        self,
        repo_path: str,
        *,
        max_excerpt_chars: int = 3500,
    ) -> None:
        self._root = Path(repo_path).resolve()
        self._max_excerpt_chars = max_excerpt_chars

    @property
    def repo_path(self) -> str:
        return str(self._root)

    def build_inventory(self) -> RepoInventory:
        """List all Python files and derive module/test metrics."""
        if not self._root.is_dir():
            raise FileNotFoundError(f"Repository not found: {self._root}")

        python_files: list[str] = []
        test_files: list[str] = []
        modules: set[str] = set()

        for path in self._iter_python_files():
            rel = str(path.relative_to(self._root))
            python_files.append(rel)
            if rel.startswith("tests/"):
                test_files.append(rel)
            if rel.startswith("app/") and "/__init__.py" not in rel:
                parts = Path(rel).parts
                if len(parts) >= 2:
                    modules.add(parts[1])

        return RepoInventory(
            repo_path=str(self._root),
            python_files=sorted(python_files),
            test_files=sorted(test_files),
            modules=sorted(modules),
            metrics={
                "python_file_count": len(python_files),
                "test_file_count": len(test_files),
                "module_count": len(modules),
            },
        )

    def select_analysis_files(
        self,
        inventory: RepoInventory,
        *,
        max_files: int = 25,
    ) -> list[str]:
        """
        Pick files for sequential LLM analysis.

        Priority: app/ service modules → other app/ → tests/ → remainder.
        Skips empty __init__.py stubs.
        """
        candidates = list(inventory.python_files)

        def sort_key(rel: str) -> tuple[int, str]:
            if rel.startswith("app/") and rel.endswith("service.py"):
                return (0, rel)
            if rel.startswith("app/") and not rel.endswith("__init__.py"):
                return (1, rel)
            if rel.startswith("tests/"):
                return (2, rel)
            return (3, rel)

        ordered = sorted(candidates, key=sort_key)
        selected: list[str] = []
        for rel in ordered:
            if len(selected) >= max_files:
                break
            if rel.endswith("__init__.py"):
                full = self._root / rel
                try:
                    if full.stat().st_size < 40:
                        continue
                except OSError:
                    continue
            selected.append(rel)
        return selected

    def read_file_context(self, relative_path: str) -> FileContext:
        """Read one file and return a safely truncated excerpt."""
        path = self._root / relative_path
        text = path.read_text(encoding="utf-8")
        size = len(text.encode("utf-8"))
        line_count = text.count("\n") + (1 if text else 0)
        excerpt, truncated = self._truncate(text)
        return FileContext(
            file_path=relative_path,
            size_bytes=size,
            excerpt=excerpt,
            truncated=truncated,
            line_count=line_count,
        )

    def _iter_python_files(self) -> list[Path]:
        files: list[Path] = []
        for path in self._root.rglob("*.py"):
            rel = path.relative_to(self._root)
            if any(part in IGNORED_DIR_NAMES for part in rel.parts):
                continue
            files.append(path)
        return files

    def _truncate(self, text: str) -> tuple[str, bool]:
        limit = self._max_excerpt_chars
        if len(text) <= limit:
            return text, False

        head_len = int(limit * 0.7)
        tail_len = int(limit * 0.2)
        marker = f"\n\n# ... [{len(text) - head_len - tail_len} chars truncated] ...\n\n"
        return text[:head_len] + marker + text[-tail_len:], True
