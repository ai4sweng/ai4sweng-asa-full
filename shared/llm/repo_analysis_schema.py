"""
Pydantic schema for per-file LLM repository analysis output.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator

from shared.llm.llm_json_coerce import coerce_to_str, normalize_finding_dict


class Severity(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class Category(str, Enum):
    BUG = "BUG"
    SECURITY = "SECURITY"
    TEST = "TEST"
    PERFORMANCE = "PERFORMANCE"
    STYLE = "STYLE"


_CATEGORY_ALIASES: dict[str, Category] = {
    "BUG": Category.BUG,
    "SECURITY": Category.SECURITY,
    "TEST": Category.TEST,
    "TEST_GAP": Category.TEST,
    "MISSING_TEST": Category.TEST,
    "PERFORMANCE": Category.PERFORMANCE,
    "STYLE": Category.STYLE,
    "SQL_INJECTION": Category.SECURITY,
    "SQLINJECTION": Category.SECURITY,
    "NULL_POINTER": Category.BUG,
    "OFF_BY_ONE": Category.BUG,
    "LOGGING": Category.SECURITY,
    "OTHER": Category.BUG,
}


class RepoFinding(BaseModel):
    """One structured finding from LLM file analysis."""

    bug_id: str
    file_path: str
    line_hint: str = ""
    severity: Severity
    category: Category
    description: str
    evidence: str
    recommended_fix: str

    @model_validator(mode="before")
    @classmethod
    def _coerce_aliases(cls, data: Any) -> Any:
        if isinstance(data, dict):
            return normalize_finding_dict(data)
        return data

    @field_validator("bug_id", "file_path", "description", "recommended_fix")
    @classmethod
    def _strip_required_strings(cls, value: str) -> str:
        return value.strip()

    @field_validator("evidence", mode="before")
    @classmethod
    def _coerce_evidence(cls, value: Any) -> str:
        return coerce_to_str(value)

    @field_validator("line_hint", mode="before")
    @classmethod
    def _coerce_line_hint(cls, value: Any) -> str:
        if value is None:
            return ""
        return str(value).strip()

    @field_validator("severity", mode="before")
    @classmethod
    def _coerce_severity(cls, value: Any) -> Severity:
        if isinstance(value, Severity):
            return value
        text = str(value).strip().upper()
        if text == "CRITICAL":
            text = "HIGH"
        try:
            return Severity(text)
        except ValueError:
            return Severity.MEDIUM

    @field_validator("category", mode="before")
    @classmethod
    def _coerce_category(cls, value: Any) -> Category:
        if isinstance(value, Category):
            return value
        token = str(value).strip().upper().replace("-", "_").replace(" ", "_")
        if token in _CATEGORY_ALIASES:
            return _CATEGORY_ALIASES[token]
        if "|" in str(value):
            first = str(value).split("|")[0].strip().upper().replace("-", "_")
            return _CATEGORY_ALIASES.get(first, Category.BUG)
        return Category.BUG


class RepoFileFindingsResponse(BaseModel):
    """Top-level JSON object expected from the model."""

    findings: list[RepoFinding] = Field(default_factory=list)


def finding_to_legacy_dict(
    finding: RepoFinding,
    *,
    detection_source: str = "llm",
    parse_repaired: bool = False,
) -> dict[str, Any]:
    """Map LLM finding to legacy scanner shape for downstream agents."""
    line_hint = finding.line_hint.strip()
    line_no = 0
    if line_hint.isdigit():
        line_no = int(line_hint)

    kind = finding.category.value.lower()
    if finding.category == Category.SECURITY:
        kind = "security"
    elif finding.category == Category.PERFORMANCE:
        kind = "performance"
    elif finding.category == Category.TEST:
        kind = "missing_test"

    return {
        "kind": kind,
        "file": finding.file_path,
        "line": line_no,
        "message": finding.description,
        "snippet": finding.evidence[:200],
        "bug_id": finding.bug_id,
        "severity": finding.severity.value,
        "recommended_fix": finding.recommended_fix,
        "detection_source": detection_source,
        "parse_repaired": parse_repaired,
    }
