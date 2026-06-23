"""Structured intent extraction from the user's prompt.

Routing off a raw sentence is guesswork; this distills the request into a few
typed signals (task type, whether code already exists, risk) that the planner
keys off and that make routing auditable.  Deterministic and LLM-free — it reads
verbs in the prompt plus facts already known (initial_context, recon signals),
so it adds no latency.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Ordered by priority: the first pattern that matches wins, so a "fix the
# security hole" reads as 'security' (the higher-stakes framing) before 'bug_fix'.
_TASK_PATTERNS: tuple[tuple[str, str], ...] = (
    ("security", r"\b(security|owasp|vulnerab\w*|exploit|cve|pentest|injection|xss|csrf)\b"),
    ("optimization", r"\b(optimi[sz]e\w*|energy|tinyml|latency|throughput|quantiz\w*)\b"),
    ("testing", r"\b(unit ?test\w*|pytest|coverage|test suite|write tests|add tests)\b"),
    ("bug_fix", r"\b(fix|patch|repair|resolve|debug|broken|failing)\b"),
    ("code_generation", r"\b(build|create|generate|write|implement|develop|scaffold)\b"),
    ("analysis", r"\b(analy[sz]e\w*|review|inspect|examine|find bugs|assess)\b"),
)


@dataclass(frozen=True)
class IntentProfile:
    """A compact, typed summary of what the user is asking for."""

    task_type: str  # security|optimization|testing|bug_fix|code_generation|analysis|unknown
    has_code: bool
    has_repo: bool
    risk_level: str  # normal|high


def extract_intent(
    description: str,
    *,
    has_code: bool,
    has_repo: bool,
    security_sensitive: bool = False,
) -> IntentProfile:
    """Derive an :class:`IntentProfile` from the prompt and known facts."""
    text = (description or "").lower()

    task_type = "unknown"
    for name, pattern in _TASK_PATTERNS:
        if re.search(pattern, text):
            task_type = name
            break

    risk_level = "high" if (task_type == "security" or security_sensitive) else "normal"

    return IntentProfile(
        task_type=task_type,
        has_code=has_code,
        has_repo=has_repo,
        risk_level=risk_level,
    )


def format_intent(intent: IntentProfile) -> str:
    """Render the intent as a compact block for the planner prompt."""
    return (
        "Task intent (parsed from the request):\n"
        f"- type: {intent.task_type}\n"
        f"- existing code available: {str(intent.has_code or intent.has_repo).lower()}\n"
        f"- risk: {intent.risk_level}"
    )
