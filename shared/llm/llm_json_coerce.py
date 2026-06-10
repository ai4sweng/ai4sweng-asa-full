"""
Shared coercion helpers for lenient LLM JSON parsing.
"""

from __future__ import annotations

import json
import re
from typing import Any

_TRAILING_COMMA_RE = re.compile(r",(\s*[}\]])")
# Python literals that small LLMs often emit instead of JSON tokens.
_PYTHON_LITERAL_RE = re.compile(r"(?<=[:,\[\s])(None|True|False)(?=\s*[,}\]\s]|$)")


def _replace_python_literals(text: str) -> str:
    """Map Python None/True/False to JSON null/true/false outside quoted strings."""

    def _repl(match: re.Match[str]) -> str:
        token = match.group(1)
        return {"None": "null", "True": "true", "False": "false"}[token]

    return _PYTHON_LITERAL_RE.sub(_repl, text)


def _close_unclosed_json(text: str) -> str:
    """Append missing } / ] when the model truncates mid-object."""
    stack: list[str] = []
    in_string = False
    escape = False

    for char in text:
        if escape:
            escape = False
            continue
        if char == "\\" and in_string:
            escape = True
            continue
        if char == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if char == "{":
            stack.append("}")
        elif char == "[":
            stack.append("]")
        elif char in "}]" and stack and stack[-1] == char:
            stack.pop()

    if stack:
        text = text + "".join(reversed(stack))
    return text


def repair_json_text(text: str) -> str:
    """Fix common LLM JSON mistakes before json.loads."""
    repaired = text.strip()
    repaired = _TRAILING_COMMA_RE.sub(r"\1", repaired)
    repaired = _replace_python_literals(repaired)
    repaired = _close_unclosed_json(repaired)
    return repaired


def coerce_to_str(value: Any, *, max_len: int = 2000) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()[:max_len]
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            if isinstance(item, dict):
                code = item.get("code") or item.get("snippet") or item.get("text")
                line = item.get("line")
                if code is not None:
                    parts.append(f"L{line}: {code}" if line is not None else str(code))
                else:
                    parts.append(str(item))
            else:
                parts.append(str(item))
        return " | ".join(parts)[:max_len]
    if isinstance(value, dict):
        for key in ("code", "snippet", "text", "message"):
            if key in value:
                return str(value[key])[:max_len]
        return str(value)[:max_len]
    return str(value).strip()[:max_len]


def coerce_to_int(value: Any, *, default: int = 0) -> int:
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    text = str(value).strip()
    if text.isdigit():
        return int(text)
    match = re.search(r"\d+", text)
    return int(match.group()) if match else default


def normalize_bug_dict(data: dict[str, Any]) -> dict[str, Any]:
    """Map common LLM field aliases onto BugEntry shape."""
    out = dict(data)

    if not out.get("id"):
        out["id"] = out.get("bug_id") or out.get("bugId") or out.get("finding_id") or "BUG-UNKNOWN"

    if not out.get("file"):
        out["file"] = out.get("file_path") or out.get("filepath") or out.get("path") or "unknown"

    if out.get("line") is None and out.get("line_hint") is not None:
        out["line"] = out["line_hint"]
    out["line"] = coerce_to_int(out.get("line"), default=0)

    if not out.get("description"):
        out["description"] = (
            out.get("message") or out.get("summary") or out.get("title") or "No description"
        )

    if not out.get("evidence"):
        out["evidence"] = out.get("snippet") or out.get("proof") or ""
    out["evidence"] = coerce_to_str(out.get("evidence"))

    if not out.get("recommended_fix"):
        out["recommended_fix"] = (
            out.get("fix") or out.get("suggested_fix") or out.get("recommendation") or ""
        )
    out["recommended_fix"] = coerce_to_str(out.get("recommended_fix"))

    if not out.get("kind"):
        out["kind"] = out.get("category") or out.get("type") or "OTHER"

    sev_raw = out.get("severity")
    if sev_raw is None:
        out["severity"] = "medium"
    else:
        sev = str(sev_raw).strip().lower()
        if sev == "critical":
            sev = "high"
        out["severity"] = sev if sev in ("low", "medium", "high") else "medium"

    return out


def normalize_finding_dict(
    data: dict[str, Any],
    *,
    default_file_path: str | None = None,
) -> dict[str, Any]:
    """Map common LLM field aliases onto RepoFinding shape."""
    out = dict(data)

    if not out.get("bug_id"):
        out["bug_id"] = out.get("id") or out.get("finding_id") or "FINDING-UNKNOWN"

    if not out.get("file_path"):
        out["file_path"] = (
            out.get("file")
            or out.get("filepath")
            or out.get("path")
            or default_file_path
            or "unknown"
        )

    if out.get("line_hint") in (None, ""):
        hint = out.get("line") or out.get("line_number")
        if hint is not None:
            out["line_hint"] = str(hint)

    if not out.get("description"):
        out["description"] = out.get("message") or out.get("summary") or "No description"

    out["evidence"] = coerce_to_str(out.get("evidence") or out.get("snippet") or "")

    if not out.get("recommended_fix"):
        out["recommended_fix"] = (
            out.get("fix") or out.get("suggested_fix") or out.get("recommendation") or ""
        )
    out["recommended_fix"] = coerce_to_str(out.get("recommended_fix"))

    if not out.get("category"):
        out["category"] = out.get("kind") or out.get("type") or "BUG"

    sev_raw = out.get("severity")
    if sev_raw is None:
        out["severity"] = "MEDIUM"
    else:
        sev = str(sev_raw).strip().upper()
        if sev == "CRITICAL":
            sev = "HIGH"
        if sev not in ("LOW", "MEDIUM", "HIGH"):
            sev = "MEDIUM"
        out["severity"] = sev

    return out


def extract_json_object(text: str) -> dict[str, Any] | None:
    """Best-effort extraction of a JSON object from free-form model text."""
    stripped = text.strip()
    if not stripped:
        return None

    candidates: list[str] = [stripped, repair_json_text(stripped)]

    block_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", stripped, re.DOTALL | re.IGNORECASE)
    if block_match:
        candidates.insert(0, repair_json_text(block_match.group(1)))

    start = stripped.find("{")
    end = stripped.rfind("}")
    if start != -1 and end > start:
        candidates.append(repair_json_text(stripped[start : end + 1]))

    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            continue

    return None
