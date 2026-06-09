"""
Pydantic schema for LLM bug catalog output (BugDetectorAgent).
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator

from shared.llm.bug_kind import BugKind, enrich_bug_dict, normalize_kind
from shared.llm.llm_json_coerce import coerce_to_int, coerce_to_str, normalize_bug_dict


class BugEntry(BaseModel):
    """One confirmed bug in the BUG_REPORT artifact."""

    id: str
    kind: BugKind
    file: str
    line: int = 0
    severity: str
    description: str
    evidence: str = ""
    recommended_fix: str = ""
    tags: list[str] = Field(default_factory=list)
    normalized_kind: bool = False

    @field_validator("id", "file", "severity", "description")
    @classmethod
    def _strip_required(cls, value: str) -> str:
        return value.strip()

    @field_validator("severity")
    @classmethod
    def _normalize_severity(cls, value: str) -> str:
        return value.strip().lower()

    @field_validator("evidence", "recommended_fix", mode="before")
    @classmethod
    def _coerce_text_fields(cls, value: Any) -> str:
        return coerce_to_str(value)

    @field_validator("line", mode="before")
    @classmethod
    def _coerce_line(cls, value: Any) -> int:
        return coerce_to_int(value, default=0)

    @model_validator(mode="before")
    @classmethod
    def _coerce_aliases_and_kind(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        data = normalize_bug_dict(data)
        raw_kind = data.get("kind")
        if raw_kind is None:
            raw_kind = data.get("category", "OTHER")
        primary, tags, normalized = normalize_kind(
            raw_kind.value if isinstance(raw_kind, BugKind) else str(raw_kind)
        )
        data["kind"] = primary
        if not data.get("tags"):
            data["tags"] = tags
        if "normalized_kind" not in data:
            data["normalized_kind"] = normalized
        return data


class BugCatalogResponse(BaseModel):
    """Top-level JSON object expected from bug detection LLM calls."""

    bugs: list[BugEntry] = Field(default_factory=list)


def repo_finding_to_bug_entry(finding: dict[str, Any], *, index: int) -> dict[str, Any]:
    """Map REPO_ANALYSIS finding dict to BUG_REPORT bug shape (fallback)."""
    severity = str(finding.get("severity", "medium")).lower()
    if severity in ("low", "medium", "high"):
        pass
    elif severity == "critical":
        severity = "high"
    else:
        severity = "medium"

    line = finding.get("line", 0)
    if isinstance(line, str) and line.isdigit():
        line = int(line)

    bug_id = finding.get("bug_id") or finding.get("id")
    if not bug_id:
        file_part = finding.get("file", "unknown")
        line_part = finding.get("line", 0)
        bug_id = f"FALLBACK-{file_part}-{line_part}-{index}"

    raw_kind = (
        finding.get("kind")
        or finding.get("category")
        or "OTHER"
    )

    evidence = finding.get("evidence") or finding.get("snippet", "")
    if isinstance(evidence, list):
        evidence = " | ".join(str(x) for x in evidence)

    return enrich_bug_dict(
        {
            "id": str(bug_id),
            "kind": str(raw_kind),
            "file": finding.get("file") or finding.get("file_path", "unknown"),
            "line": line or 0,
            "severity": severity,
            "description": finding.get("description") or finding.get("message", ""),
            "evidence": evidence,
            "recommended_fix": finding.get("recommended_fix", ""),
        }
    )


# Last-resort demo bugs when analysis and LLM both yield nothing.
HARDCODED_FALLBACK_BUGS: list[dict[str, Any]] = [
    enrich_bug_dict(
        {
            "id": "BUG-001",
            "kind": "off_by_one",
            "file": "app/inventory/service.py",
            "line": 126,
            "severity": "medium",
            "description": "Pagination skips first product on page 1",
            "evidence": "range(1, len(",
            "recommended_fix": "Use 0-based slice offset for pagination",
        }
    ),
    enrich_bug_dict(
        {
            "id": "BUG-002",
            "kind": "null_pointer",
            "file": "app/users/service.py",
            "line": 78,
            "severity": "high",
            "description": "get_user_profile accesses profile without None guard",
            "evidence": ".profile.",
            "recommended_fix": "Return defaults when profile is None",
        }
    ),
    enrich_bug_dict(
        {
            "id": "BUG-003",
            "kind": "sql_injection",
            "file": "app/users/service.py",
            "line": 59,
            "severity": "critical",
            "description": "search_users_by_name interpolates query into SQL",
            "evidence": "f\"SELECT",
            "recommended_fix": "Use parameterized query",
        }
    ),
    enrich_bug_dict(
        {
            "id": "BUG-004",
            "kind": "logging",
            "file": "app/auth/service.py",
            "line": 21,
            "severity": "high",
            "description": "Passwords and JWT tokens logged at INFO",
            "evidence": "password=%s",
            "recommended_fix": "Remove credential values from logs",
        }
    ),
    enrich_bug_dict(
        {
            "id": "BUG-005",
            "kind": "performance",
            "file": "app/inventory/service.py",
            "line": 100,
            "severity": "medium",
            "description": "N+1 category queries in enrich_products_with_categories",
            "evidence": "for product in products",
            "recommended_fix": "Batch-fetch categories",
        }
    ),
]
