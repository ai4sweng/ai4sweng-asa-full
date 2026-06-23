"""Fast, read-only repository recon for evidence-driven agent routing.

The planner (``lm.plan_workflow``) chooses which KIO agents to run.  Choosing
from the prompt text alone is guesswork; this module gives the planner a cheap
*fact sheet* about the target repository so selection is grounded in what the
code actually is.

Design goals:
  • Cheap & deterministic — derives signals from the file tree and file names
    only (no content reads, no LLM call), so it adds negligible latency.
  • Multi-language — not Python-specific (cf. RepoContextBuilder).
  • Fail-open — the caller treats any error as "no signals" and plans anyway.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

# Directories we never descend into (vendor, build output, caches, VCS).
_IGNORED_DIRS: frozenset[str] = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        ".venv",
        "venv",
        "env",
        "node_modules",
        "__pycache__",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        "dist",
        "build",
        "target",
        "vendor",
        ".next",
        ".nuxt",
        "coverage",
        ".idea",
        ".vscode",
    }
)

# File extension → language label.
_LANG_BY_EXT: dict[str, str] = {
    ".py": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".go": "go",
    ".rs": "rust",
    ".java": "java",
    ".kt": "kotlin",
    ".scala": "scala",
    ".rb": "ruby",
    ".php": "php",
    ".cs": "csharp",
    ".c": "c",
    ".h": "c",
    ".cc": "cpp",
    ".cpp": "cpp",
    ".hpp": "cpp",
    ".swift": "swift",
    ".m": "objc",
    ".sh": "shell",
    ".sql": "sql",
}

# Presence of any of these (by basename) marks a dependency/build manifest.
_DEPENDENCY_FILES: frozenset[str] = frozenset(
    {
        "requirements.txt",
        "pyproject.toml",
        "setup.py",
        "setup.cfg",
        "pipfile",
        "package.json",
        "go.mod",
        "cargo.toml",
        "pom.xml",
        "build.gradle",
        "gemfile",
        "composer.json",
    }
)

# Substrings in a relative path that hint at a security-sensitive surface.
_SECURITY_HINTS: tuple[str, ...] = (
    "auth",
    "login",
    "password",
    "passwd",
    "secret",
    "credential",
    "token",
    "jwt",
    "oauth",
    "session",
    "crypto",
    "cipher",
    "ssl",
    "tls",
    ".env",
)

# Test markers: a path part, filename prefix/infix.
_TEST_DIR_PARTS: frozenset[str] = frozenset({"test", "tests", "__tests__", "spec"})

_MAX_SECURITY_SAMPLE = 8


@dataclass(frozen=True)
class RepoSignals:
    """A compact, routing-relevant fact sheet about a repository."""

    exists: bool
    file_count: int = 0
    languages: list[tuple[str, int]] = field(default_factory=list)  # sorted, top-N
    has_tests: bool = False
    dependency_files: list[str] = field(default_factory=list)
    security_surfaces: list[str] = field(default_factory=list)  # sample paths
    security_sensitive: bool = False


def _is_test_path(rel_lower: str, name_lower: str) -> bool:
    parts = rel_lower.split("/")
    if any(p in _TEST_DIR_PARTS for p in parts[:-1]):
        return True
    return (
        name_lower.startswith("test_")
        or name_lower.endswith("_test.py")
        or "_test." in name_lower
        or ".test." in name_lower
        or ".spec." in name_lower
    )


def scan_repo(repo_path: str, *, max_files: int = 5000) -> RepoSignals:
    """Walk ``repo_path`` and derive routing signals from names alone.

    Bounded by ``max_files`` so a huge tree can't stall planning.  Never reads
    file contents.  Returns ``RepoSignals(exists=False)`` when the path is not a
    directory.
    """
    if not repo_path or not os.path.isdir(repo_path):
        return RepoSignals(exists=False)

    root = os.path.abspath(repo_path)
    lang_counts: dict[str, int] = {}
    dependency: set[str] = set()
    security: list[str] = []
    has_tests = False
    file_count = 0

    for dirpath, dirnames, filenames in os.walk(root):
        # Prune ignored dirs in place so os.walk never descends into them.
        dirnames[:] = [d for d in dirnames if d not in _IGNORED_DIRS and not d.startswith(".git")]
        for name in filenames:
            if file_count >= max_files:
                break
            file_count += 1
            name_lower = name.lower()
            rel = os.path.relpath(os.path.join(dirpath, name), root).replace(os.sep, "/")
            rel_lower = rel.lower()

            _, ext = os.path.splitext(name_lower)
            lang = _LANG_BY_EXT.get(ext)
            if lang:
                lang_counts[lang] = lang_counts.get(lang, 0) + 1

            if name_lower in _DEPENDENCY_FILES:
                dependency.add(name)

            if not has_tests and _is_test_path(rel_lower, name_lower):
                has_tests = True

            if len(security) < _MAX_SECURITY_SAMPLE and any(h in rel_lower for h in _SECURITY_HINTS):
                security.append(rel)
        if file_count >= max_files:
            break

    languages = sorted(lang_counts.items(), key=lambda kv: (-kv[1], kv[0]))

    return RepoSignals(
        exists=True,
        file_count=file_count,
        languages=languages,
        has_tests=has_tests,
        dependency_files=sorted(dependency),
        security_surfaces=security,
        security_sensitive=bool(security),
    )


def format_signals(signals: RepoSignals) -> str:
    """Render signals as a compact block for the planner prompt.

    Returns "" when there is nothing useful to say (no repo / empty), so callers
    can simply skip the block.
    """
    if not signals.exists or signals.file_count == 0:
        return ""

    langs = ", ".join(f"{name}×{count}" for name, count in signals.languages[:5]) or "unknown"
    lines = [
        "Repository signals (read-only recon — use these to choose agents):",
        f"- files scanned: {signals.file_count}",
        f"- languages: {langs}",
        f"- has tests: {str(signals.has_tests).lower()}",
        f"- dependency manifests: {', '.join(signals.dependency_files) or 'none'}",
        f"- security-sensitive surfaces: {str(signals.security_sensitive).lower()}",
    ]
    if signals.security_surfaces:
        lines.append(f"  e.g. {', '.join(signals.security_surfaces[:5])}")
    return "\n".join(lines)
