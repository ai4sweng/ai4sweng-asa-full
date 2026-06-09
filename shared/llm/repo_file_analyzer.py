"""
Per-file LLM repository analysis — prompts, parsing, retry/repair, and fallback.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from typing import Any

from shared.llm.base import BaseLLMProvider, LLMResponse
from shared.llm.repo_analysis_schema import (
    Category,
    RepoFinding,
    Severity,
    finding_to_legacy_dict,
)
from shared.llm.repo_json_parser import parse_file_findings
from shared.tools.repo_context_builder import FileContext
from shared.tools.repo_scanner import RepoScanner

logger = logging.getLogger(__name__)

_JSON_FINDINGS_SCHEMA = """{
  "findings": [
    {
      "bug_id": "short-unique-id",
      "file_path": "relative/path.py",
      "line_hint": "line number or range as string",
      "severity": "LOW|MEDIUM|HIGH",
      "category": "BUG|SECURITY|TEST|PERFORMANCE|STYLE",
      "description": "what is wrong",
      "evidence": "quoted code snippet",
      "recommended_fix": "concrete fix"
    }
  ]
}"""

_FILE_ANALYSIS_SYSTEM = f"""You are a senior software engineer auditing a Python repository.
Analyze ONLY the single file provided. Report real issues grounded in the code excerpt.
Respond with a single JSON object — no markdown outside the JSON.

Schema:
{_JSON_FINDINGS_SCHEMA}

If no issues, return {{"findings": []}}. Do not invent files or issues not supported by the excerpt."""

_RETRY_REPAIR_SYSTEM = (
    "You repair invalid LLM audit outputs into strict JSON. "
    "Return ONLY valid JSON. No markdown. No prose."
)


@dataclass
class FileAnalysisResult:
    """Outcome of one sequential per-file LLM call (with optional retry/fallback)."""

    file_path: str
    prompt: str
    prompt_hash: str
    raw_response: LLMResponse
    findings: list[RepoFinding] = field(default_factory=list)
    parse_error: str | None = None
    latency_ms: float = 0.0
    detection_source: str = "llm"
    parse_repaired: bool = False
    retry_used: bool = False
    regex_fallback_used: bool = False
    original_raw_content: str | None = None
    repaired_raw_content: str | None = None
    retry_prompt: str | None = None
    retry_latency_ms: float = 0.0
    retry_tokens_in: int = 0
    retry_tokens_out: int = 0

    def to_trace_dict(self) -> dict[str, Any]:
        return {
            "file_path": self.file_path,
            "prompt_hash": self.prompt_hash,
            "model": self.raw_response.model,
            "tokens_in": self.raw_response.tokens_in,
            "tokens_out": self.raw_response.tokens_out,
            "latency_ms": self.latency_ms,
            "finding_count": len(self.findings),
            "parse_error": self.parse_error,
            "detection_source": self.detection_source,
            "parse_repaired": self.parse_repaired,
            "retry_used": self.retry_used,
            "regex_fallback_used": self.regex_fallback_used,
            "raw_content_preview": self.raw_response.content[:500],
            "repaired_content_preview": (
                self.repaired_raw_content[:500] if self.repaired_raw_content else None
            ),
        }


def build_file_analysis_prompt(
    *,
    issue: str,
    file_ctx: FileContext,
) -> str:
    """Compact prompt for one file — kept small for 3B local models."""
    trunc_note = " (truncated)" if file_ctx.truncated else ""
    return (
        f"Audit issue: {issue}\n\n"
        f"File: {file_ctx.file_path}\n"
        f"Size: {file_ctx.size_bytes} bytes, lines: {file_ctx.line_count}{trunc_note}\n\n"
        f"```python\n{file_ctx.excerpt}\n```\n\n"
        "Return JSON only matching the schema."
    )


def build_retry_repair_prompt(
    *,
    issue: str,
    file_ctx: FileContext,
    previous_raw: str,
) -> str:
    """Stricter second-pass prompt when the first response is not valid JSON."""
    trunc_note = " (truncated)" if file_ctx.truncated else ""
    return (
        "Your previous response was NOT valid JSON.\n\n"
        f"Previous response:\n{previous_raw[:3000]}\n\n"
        f"Required JSON schema:\n{_JSON_FINDINGS_SCHEMA}\n\n"
        f"Audit issue: {issue}\n"
        f"File: {file_ctx.file_path}\n"
        f"Size: {file_ctx.size_bytes} bytes, lines: {file_ctx.line_count}{trunc_note}\n\n"
        f"```python\n{file_ctx.excerpt[:2000]}\n```\n\n"
        "Return ONLY valid JSON. No markdown. No prose."
    )


def prompt_hash(prompt: str) -> str:
    return hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:16]


def _normalize_findings(
    findings: list[RepoFinding],
    file_ctx: FileContext,
) -> list[RepoFinding]:
    normalized: list[RepoFinding] = []
    for item in findings:
        if not item.file_path or item.file_path == "unknown":
            item = item.model_copy(update={"file_path": file_ctx.file_path})
        elif item.file_path != file_ctx.file_path:
            item = item.model_copy(update={"file_path": file_ctx.file_path})
        normalized.append(item)
    return normalized


def _regex_findings_to_repo(
    raw_findings: list[dict[str, Any]],
    file_ctx: FileContext,
) -> list[RepoFinding]:
    converted: list[RepoFinding] = []
    for index, hit in enumerate(raw_findings):
        kind = hit.get("kind", "bug")
        category = Category.BUG
        if kind in ("sql_injection", "credential_log", "security"):
            category = Category.SECURITY
        elif kind == "performance" or kind == "n_plus_one":
            category = Category.PERFORMANCE
        elif kind == "missing_test":
            category = Category.TEST

        line = hit.get("line", 0)
        converted.append(
            RepoFinding(
                bug_id=f"REGEX-{file_ctx.file_path}-{line}-{index}",
                file_path=file_ctx.file_path,
                line_hint=str(line),
                severity=Severity.MEDIUM,
                category=category,
                description=hit.get("message", "Regex pattern match"),
                evidence=hit.get("snippet", ""),
                recommended_fix="Review and fix matched pattern",
            )
        )
    return converted


def _parse_response(
    content: str,
    file_ctx: FileContext,
) -> tuple[list[RepoFinding], str | None]:
    parsed, error = parse_file_findings(content, default_file_path=file_ctx.file_path)
    if parsed is None:
        return [], error
    return _normalize_findings(parsed.findings, file_ctx), error


async def analyze_repository_file(
    provider: BaseLLMProvider,
    *,
    issue: str,
    file_ctx: FileContext,
    repo_path: str | None = None,
) -> FileAnalysisResult:
    """
    Analyze one repository file via LLM with JSON retry/repair and per-file regex fallback.
    """
    prompt = build_file_analysis_prompt(issue=issue, file_ctx=file_ctx)
    p_hash = prompt_hash(prompt)

    response = await provider.complete(prompt, system=_FILE_ANALYSIS_SYSTEM)
    latency_ms = response.latency_ms
    findings, error = _parse_response(response.content, file_ctx)

    result = FileAnalysisResult(
        file_path=file_ctx.file_path,
        prompt=prompt,
        prompt_hash=p_hash,
        raw_response=response,
        findings=findings,
        parse_error=error,
        latency_ms=latency_ms,
        detection_source="llm",
    )

    if error is None or findings:
        return result

    # Retry when JSON extraction/validation failed entirely.
    if not findings:
        result.original_raw_content = response.content
        retry_prompt = build_retry_repair_prompt(
            issue=issue,
            file_ctx=file_ctx,
            previous_raw=response.content,
        )
        retry_response = await provider.complete(
            retry_prompt,
            system=_RETRY_REPAIR_SYSTEM,
        )
        retry_findings, retry_error = _parse_response(
            retry_response.content, file_ctx
        )

        result.retry_used = True
        result.retry_prompt = retry_prompt
        result.retry_latency_ms = retry_response.latency_ms
        result.retry_tokens_in = retry_response.tokens_in
        result.retry_tokens_out = retry_response.tokens_out
        result.repaired_raw_content = retry_response.content
        result.latency_ms += retry_response.latency_ms

        if retry_findings or retry_error is None:
            result.findings = retry_findings
            result.parse_error = retry_error
            result.detection_source = "llm_retry"
            result.parse_repaired = True
            return result

        result.parse_error = retry_error or error
        logger.warning(
            "Retry parse failed for %s: %s", file_ctx.file_path, result.parse_error
        )

    # Per-file regex mini-scanner fallback.
    if repo_path and not result.findings:
        scanner = RepoScanner(repo_path)
        regex_hits = scanner.scan_file(file_ctx.file_path)
        if regex_hits:
            result.findings = _regex_findings_to_repo(regex_hits, file_ctx)
            result.detection_source = "regex_fallback"
            result.regex_fallback_used = True
            result.parse_error = result.parse_error or "JSON parse failed; regex fallback"
        elif not result.parse_error:
            result.parse_error = "No JSON and regex fallback found nothing"

    return result


def legacy_findings_from_result(result: FileAnalysisResult) -> list[dict[str, Any]]:
    """Export findings with detection metadata for REPO_ANALYSIS artifact."""
    legacy: list[dict[str, Any]] = []
    for finding in result.findings:
        entry = finding_to_legacy_dict(
            finding,
            detection_source=result.detection_source,
            parse_repaired=result.parse_repaired,
        )
        legacy.append(entry)
    return legacy
