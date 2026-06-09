"""
LLM bug detection — chunk prompts, JSON parsing, retry/repair, provider extension.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from typing import Any

from pydantic import ValidationError

from shared.llm.base import BaseLLMProvider, LLMResponse
from shared.llm.bug_detection_schema import BugCatalogResponse, BugEntry
from shared.llm.llm_json_coerce import extract_json_object, normalize_bug_dict

logger = logging.getLogger(__name__)

_BUG_JSON_SCHEMA = """{
  "bugs": [
    {
      "id": "unique-bug-id",
      "kind": "ONE of: OFF_BY_ONE, NULL_POINTER, SQL_INJECTION, PERFORMANCE, TEST_GAP, SECURITY, STYLE, OTHER",
      "file": "relative/path.py",
      "line": 0,
      "severity": "critical|high|medium|low",
      "description": "clear bug description",
      "evidence": "code snippet or reason",
      "recommended_fix": "concrete fix"
    }
  ]
}"""

_BUG_DETECTION_SYSTEM = f"""You are a bug detection agent validating repository audit findings.
Review the provided findings chunk and pytest context. Confirm real bugs only.
Respond with a single JSON object — no markdown outside the JSON.

Schema:
{_BUG_JSON_SCHEMA}

Drop false positives. Merge duplicates. Use severity critical only for exploitable security flaws.
If nothing is valid in this chunk, return {{"bugs": []}}."""

_RETRY_REPAIR_SYSTEM = (
    "You repair invalid LLM bug-detection outputs into strict JSON. "
    "Return ONLY valid JSON. No markdown. No prose."
)


@dataclass
class BugDetectionChunkResult:
    """Outcome of one sequential chunk LLM call (with optional retry/repair)."""

    chunk_index: int
    prompt: str
    prompt_hash: str
    raw_response: LLMResponse
    bugs: list[BugEntry] = field(default_factory=list)
    parse_error: str | None = None
    latency_ms: float = 0.0
    detection_source: str = "llm"
    parse_repaired: bool = False
    retry_used: bool = False
    original_raw_content: str | None = None
    repaired_raw_content: str | None = None
    retry_prompt: str | None = None
    retry_latency_ms: float = 0.0
    retry_tokens_in: int = 0
    retry_tokens_out: int = 0

    def to_trace_dict(self) -> dict[str, Any]:
        return {
            "chunk_index": self.chunk_index,
            "prompt_hash": self.prompt_hash,
            "model": self.raw_response.model,
            "tokens_in": self.raw_response.tokens_in,
            "tokens_out": self.raw_response.tokens_out,
            "latency_ms": self.latency_ms,
            "bug_count": len(self.bugs),
            "parse_error": self.parse_error,
            "detection_source": self.detection_source,
            "parse_repaired": self.parse_repaired,
            "retry_used": self.retry_used,
            "raw_content_preview": self.raw_response.content[:500],
            "repaired_content_preview": (
                self.repaired_raw_content[:500] if self.repaired_raw_content else None
            ),
        }


def prompt_hash(prompt: str) -> str:
    return hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:16]


def _salvage_bugs(data: dict[str, Any]) -> tuple[list[BugEntry], list[str]]:
    raw_items = data.get("bugs") or data.get("findings") or []
    if not isinstance(raw_items, list):
        return [], ["bugs field is not a list"]

    salvaged: list[BugEntry] = []
    errors: list[str] = []
    for index, item in enumerate(raw_items):
        if not isinstance(item, dict):
            errors.append(f"bugs[{index}]: expected object")
            continue
        try:
            salvaged.append(BugEntry.model_validate(normalize_bug_dict(item)))
        except ValidationError as exc:
            errors.append(f"bugs[{index}]: {exc}")
    return salvaged, errors


def parse_bug_catalog(raw_text: str) -> tuple[BugCatalogResponse | None, str | None]:
    data = extract_json_object(raw_text)
    if data is None:
        return None, "No JSON object found in model response"
    try:
        return BugCatalogResponse.model_validate(data), None
    except ValidationError:
        salvaged, item_errors = _salvage_bugs(data)
        if salvaged:
            note = f"Salvaged {len(salvaged)} bugs; dropped {len(item_errors)}"
            if item_errors:
                note += f" ({'; '.join(item_errors[:3])})"
            return BugCatalogResponse(bugs=salvaged), note
        return None, "; ".join(item_errors) or "Validation failed"


def _parse_response(content: str) -> tuple[list[BugEntry], str | None]:
    parsed, error = parse_bug_catalog(content)
    if parsed is None:
        return [], error
    return list(parsed.bugs), error


def build_chunk_prompt(
    *,
    issue: str,
    findings_chunk: list[dict[str, Any]],
    pytest_summary: dict[str, Any],
    chunk_index: int,
    chunk_total: int,
) -> str:
    compact_findings = [
        {
            "bug_id": f.get("bug_id"),
            "file_path": f.get("file_path") or f.get("file"),
            "line_hint": f.get("line_hint") or f.get("line"),
            "severity": f.get("severity"),
            "category": f.get("category") or f.get("kind"),
            "description": f.get("description") or f.get("message"),
            "evidence": f.get("evidence") or f.get("snippet"),
            "recommended_fix": f.get("recommended_fix", ""),
        }
        for f in findings_chunk
    ]
    return (
        f"Audit issue: {issue}\n"
        f"Chunk {chunk_index + 1}/{chunk_total}\n\n"
        f"Pytest: passed={pytest_summary.get('passed', 0)} "
        f"failed={pytest_summary.get('failed', 0)} "
        f"success={pytest_summary.get('success', False)}\n"
        f"Pytest tail:\n{pytest_summary.get('stdout_tail', '')[-600:]}\n\n"
        f"Findings to validate:\n{json.dumps(compact_findings, indent=2)}\n\n"
        "Return JSON only matching the schema."
    )


def build_chunk_retry_prompt(
    *,
    issue: str,
    findings_chunk: list[dict[str, Any]],
    pytest_summary: dict[str, Any],
    chunk_index: int,
    chunk_total: int,
    previous_raw: str,
) -> str:
    """Stricter second-pass prompt when the first chunk response is not valid JSON."""
    compact_findings = [
        {
            "bug_id": f.get("bug_id"),
            "file_path": f.get("file_path") or f.get("file"),
            "line_hint": f.get("line_hint") or f.get("line"),
            "severity": f.get("severity"),
            "category": f.get("category") or f.get("kind"),
            "description": f.get("description") or f.get("message"),
            "evidence": f.get("evidence") or f.get("snippet"),
            "recommended_fix": f.get("recommended_fix", ""),
        }
        for f in findings_chunk
    ]
    return (
        "Your previous response was NOT valid JSON.\n\n"
        f"Previous response:\n{previous_raw[:3000]}\n\n"
        f"Required JSON schema:\n{_BUG_JSON_SCHEMA}\n\n"
        f"Audit issue: {issue}\n"
        f"Chunk {chunk_index + 1}/{chunk_total}\n\n"
        f"Pytest: passed={pytest_summary.get('passed', 0)} "
        f"failed={pytest_summary.get('failed', 0)} "
        f"success={pytest_summary.get('success', False)}\n\n"
        f"Findings to validate:\n{json.dumps(compact_findings, indent=2)}\n\n"
        "Return ONLY valid JSON. No markdown. No prose."
    )


def chunk_list(items: list[Any], size: int) -> list[list[Any]]:
    if size <= 0:
        return [items]
    return [items[i : i + size] for i in range(0, len(items), size)]


async def _detect_chunk(
    provider: BaseLLMProvider,
    *,
    issue: str,
    findings_chunk: list[dict[str, Any]],
    pytest_summary: dict[str, Any],
    chunk_index: int,
    chunk_total: int,
) -> BugDetectionChunkResult:
    prompt = build_chunk_prompt(
        issue=issue,
        findings_chunk=findings_chunk,
        pytest_summary=pytest_summary,
        chunk_index=chunk_index,
        chunk_total=chunk_total,
    )
    p_hash = prompt_hash(prompt)
    response = await provider.complete(prompt, system=_BUG_DETECTION_SYSTEM)
    bugs, error = _parse_response(response.content)

    result = BugDetectionChunkResult(
        chunk_index=chunk_index,
        prompt=prompt,
        prompt_hash=p_hash,
        raw_response=response,
        bugs=bugs,
        parse_error=error,
        latency_ms=response.latency_ms,
        detection_source="llm",
    )

    if error is None or bugs:
        return result

    result.original_raw_content = response.content
    retry_prompt = build_chunk_retry_prompt(
        issue=issue,
        findings_chunk=findings_chunk,
        pytest_summary=pytest_summary,
        chunk_index=chunk_index,
        chunk_total=chunk_total,
        previous_raw=response.content,
    )
    retry_response = await provider.complete(
        retry_prompt,
        system=_RETRY_REPAIR_SYSTEM,
    )
    retry_bugs, retry_error = _parse_response(retry_response.content)

    result.retry_used = True
    result.retry_prompt = retry_prompt
    result.retry_latency_ms = retry_response.latency_ms
    result.retry_tokens_in = retry_response.tokens_in
    result.retry_tokens_out = retry_response.tokens_out
    result.repaired_raw_content = retry_response.content
    result.latency_ms += retry_response.latency_ms

    if retry_bugs or retry_error is None:
        result.bugs = retry_bugs
        result.parse_error = retry_error
        result.detection_source = "llm_retry"
        result.parse_repaired = True
        return result

    result.parse_error = retry_error or error
    logger.warning(
        "Bug detection retry parse failed chunk %s: %s",
        chunk_index,
        result.parse_error,
    )
    return result


async def detect_bugs_from_findings(
    provider: BaseLLMProvider,
    *,
    issue: str,
    findings: list[dict[str, Any]],
    pytest_summary: dict[str, Any],
    chunk_size: int = 4,
) -> list[BugDetectionChunkResult]:
    """
    Validate findings in sequential chunks via LLM (one chunk at a time).
    Each chunk retries once with a strict JSON repair prompt on parse failure.
    """
    if not findings:
        return []

    chunks = chunk_list(findings, chunk_size)
    results: list[BugDetectionChunkResult] = []

    for idx, chunk in enumerate(chunks):
        result = await _detect_chunk(
            provider,
            issue=issue,
            findings_chunk=chunk,
            pytest_summary=pytest_summary,
            chunk_index=idx,
            chunk_total=len(chunks),
        )
        if result.parse_error and not result.bugs:
            logger.warning(
                "Bug detection parse failed chunk %s: %s",
                idx,
                result.parse_error,
            )
        results.append(result)

    return results


def merge_bug_entries(chunk_results: list[BugDetectionChunkResult]) -> list[dict[str, Any]]:
    """Deduplicate bugs by id, then by (file, description)."""
    merged: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    seen_keys: set[tuple[str, str]] = set()

    for result in chunk_results:
        for bug in result.bugs:
            key = (bug.file, bug.description[:80])
            if bug.id in seen_ids or key in seen_keys:
                continue
            seen_ids.add(bug.id)
            seen_keys.add(key)
            entry = bug.model_dump(mode="json")
            entry["detection_source"] = result.detection_source
            entry["parse_repaired"] = result.parse_repaired
            merged.append(entry)

    return merged
