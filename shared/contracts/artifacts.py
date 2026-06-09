"""
Artifact contracts — typed schemas for every KIO's input and output.

KIO2 → KIO3 → KIO4 ... artifact chain is defined here.
All KIOs validate their inputs/outputs against these schemas.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Union
from pydantic import BaseModel, Field


class BaseArtifact(BaseModel):
    """Common fields on every artifact."""

    artifact_id: str
    workflow_id: str
    session_id: str | None = None
    producer_kio: str
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    checksum: str = ""
    parent_artifact_id: str | None = None


# ---------------------------------------------------------------------------
# KIO2 — Requirements / Issue Analysis
# ---------------------------------------------------------------------------

class IssueArtifact(BaseArtifact):
    producer_kio: str = "kio2"
    description: str
    requirements: list[str] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# KIO3 — Repository Analysis
# ---------------------------------------------------------------------------

class Finding(BaseModel):
    file_path: str
    line_number: int | None = None
    severity: str  # HIGH | MEDIUM | LOW
    category: str
    description: str
    excerpt: str = ""


class RepoAnalysisArtifact(BaseArtifact):
    producer_kio: str = "kio3"
    repo_path: str
    findings: list[Finding] = Field(default_factory=list)
    file_count: int = 0
    analyzed_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


# ---------------------------------------------------------------------------
# KIO4 — Test Generation
# ---------------------------------------------------------------------------

class GeneratedTestsArtifact(BaseArtifact):
    producer_kio: str = "kio4"
    test_file_path: str
    test_count: int = 0
    framework: str = "pytest"
    content: str = ""


# ---------------------------------------------------------------------------
# KIO5 — Bug Detection
# ---------------------------------------------------------------------------

class Bug(BaseModel):
    bug_id: str
    file_path: str
    line_number: int | None = None
    severity: str
    kind: str
    description: str
    suggested_fix: str = ""


class BugReportArtifact(BaseArtifact):
    producer_kio: str = "kio5"
    bugs: list[Bug] = Field(default_factory=list)
    total_count: int = 0


# ---------------------------------------------------------------------------
# KIO6 — Patch Generation
# ---------------------------------------------------------------------------

class PatchArtifact(BaseArtifact):
    producer_kio: str = "kio6"
    patches: dict[str, str] = Field(default_factory=dict)  # file_path → patch content
    description: str = ""


# ---------------------------------------------------------------------------
# KIO7 — Code Generation
# ---------------------------------------------------------------------------

class CodeArtifact(BaseArtifact):
    producer_kio: str = "kio7"
    language: str = "python"
    file_path: str = ""
    content: str = ""
    description: str = ""


# ---------------------------------------------------------------------------
# KIO8 — Code Review / Static Analysis
# ---------------------------------------------------------------------------

class ReviewArtifact(BaseArtifact):
    producer_kio: str = "kio8"
    verdict: str  # APPROVED | CHANGES_REQUESTED
    comments: list[dict[str, Any]] = Field(default_factory=list)
    score: float = 0.0


# ---------------------------------------------------------------------------
# KIO9 — Reporting
# ---------------------------------------------------------------------------

class ReportArtifact(BaseArtifact):
    producer_kio: str = "kio9"
    report_type: str  # FINAL | REJECTION | SUMMARY
    content: dict[str, Any] = Field(default_factory=dict)
    markdown: str = ""


# ---------------------------------------------------------------------------
# KIO10–KIO13 — Domain-specific (UC4 battery / benchmarking)
# ---------------------------------------------------------------------------

class BenchmarkArtifact(BaseArtifact):
    producer_kio: str
    benchmark_name: str
    results: dict[str, Any] = Field(default_factory=dict)
    metrics: dict[str, float] = Field(default_factory=dict)


# Union type for type-safe dispatch — use typing.Union so isinstance() works at runtime.
AnyArtifact = Union[
    IssueArtifact,
    RepoAnalysisArtifact,
    GeneratedTestsArtifact,
    BugReportArtifact,
    PatchArtifact,
    CodeArtifact,
    ReviewArtifact,
    ReportArtifact,
    BenchmarkArtifact,
]

ARTIFACT_TYPES = (
    IssueArtifact,
    RepoAnalysisArtifact,
    GeneratedTestsArtifact,
    BugReportArtifact,
    PatchArtifact,
    CodeArtifact,
    ReviewArtifact,
    ReportArtifact,
    BenchmarkArtifact,
)
