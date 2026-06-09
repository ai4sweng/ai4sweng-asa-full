"""
Extract and validate JSON findings from LLM responses.
"""

from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from shared.llm.llm_json_coerce import extract_json_object, normalize_finding_dict
from shared.llm.repo_analysis_schema import RepoFileFindingsResponse, RepoFinding


def _salvage_findings(
    data: dict[str, Any],
    *,
    default_file_path: str | None = None,
) -> tuple[list[RepoFinding], list[str]]:
    raw_items = data.get("findings") or data.get("bugs") or []
    if not isinstance(raw_items, list):
        return [], ["findings field is not a list"]

    salvaged: list[RepoFinding] = []
    errors: list[str] = []
    for index, item in enumerate(raw_items):
        if not isinstance(item, dict):
            errors.append(f"findings[{index}]: expected object")
            continue
        try:
            normalized = normalize_finding_dict(
                item, default_file_path=default_file_path
            )
            salvaged.append(RepoFinding.model_validate(normalized))
        except ValidationError as exc:
            errors.append(f"findings[{index}]: {exc}")
    return salvaged, errors


def parse_file_findings(
    raw_text: str,
    *,
    default_file_path: str | None = None,
) -> tuple[RepoFileFindingsResponse | None, str | None]:
    """
    Parse and validate LLM output.

    Returns (parsed_response, error_message). Salvages valid items when possible.
    """
    data = extract_json_object(raw_text)
    if data is None:
        return None, "No JSON object found in model response"

    try:
        return RepoFileFindingsResponse.model_validate(data), None
    except ValidationError:
        salvaged, item_errors = _salvage_findings(
            data, default_file_path=default_file_path
        )
        if salvaged:
            note = f"Salvaged {len(salvaged)} findings; dropped {len(item_errors)}"
            if item_errors:
                note += f" ({'; '.join(item_errors[:3])})"
            return RepoFileFindingsResponse(findings=salvaged), note
        return None, "; ".join(item_errors) or "Validation failed"
