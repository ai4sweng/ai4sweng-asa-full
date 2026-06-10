"""Tests for the content-level hallucination validator in repo_file_analyzer.

Two checks are validated here:
  Line bound  — findings that reference a line number beyond the end of the
                file are hard-removed (unless the file is truncated).
  Evidence    — findings whose evidence tokens are entirely absent from the
                file excerpt receive a warning (kept — human HITL is the gate).

Each test targets one specific behaviour of _validate_against_source so that a
failure points directly at the broken invariant.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from shared.llm.repo_analysis_schema import Category, RepoFinding, Severity
from shared.llm.repo_file_analyzer import _validate_against_source


# ── minimal FileContext stub (only the fields the validator uses) ─────────────


@dataclass
class _FC:
    file_path: str
    excerpt: str
    line_count: int
    truncated: bool = False
    size_bytes: int = 0


def _finding(
    bug_id: str = "BUG-001",
    line_hint: str = "10",
    evidence: str = "cursor.execute(query)",
    file_path: str = "app.py",
) -> RepoFinding:
    return RepoFinding(
        bug_id=bug_id,
        file_path=file_path,
        line_hint=line_hint,
        severity=Severity.HIGH,
        category=Category.SECURITY,
        description="raw SQL query",
        evidence=evidence,
        recommended_fix="use parameterised queries",
    )


# ── Line bound: hard-remove when line > line_count ───────────────────────────


def test_finding_within_bounds_is_kept():
    fc = _FC("app.py", excerpt="cursor.execute(query)\n", line_count=50)
    f = _finding(line_hint="42")
    valid, filtered, _ = _validate_against_source([f], fc)
    assert len(valid) == 1
    assert filtered == 0


def test_finding_exactly_at_last_line_is_kept():
    fc = _FC("app.py", excerpt="cursor.execute(query)\n", line_count=50)
    f = _finding(line_hint="50")
    valid, filtered, _ = _validate_against_source([f], fc)
    assert len(valid) == 1
    assert filtered == 0


def test_finding_past_end_of_file_is_removed():
    fc = _FC("app.py", excerpt="cursor.execute(query)\n", line_count=50)
    f = _finding(line_hint="99")
    valid, filtered, _ = _validate_against_source([f], fc)
    assert len(valid) == 0
    assert filtered == 1


def test_out_of_bounds_finding_on_truncated_file_is_kept():
    """When the file is truncated we cannot know the real line count — keep it."""
    fc = _FC("app.py", excerpt="cursor.execute(query)\n", line_count=50, truncated=True)
    f = _finding(line_hint="999")
    valid, filtered, _ = _validate_against_source([f], fc)
    assert len(valid) == 1
    assert filtered == 0


def test_empty_line_hint_skips_line_check():
    fc = _FC("app.py", excerpt="cursor.execute(query)\n", line_count=10)
    f = _finding(line_hint="")
    valid, filtered, _ = _validate_against_source([f], fc)
    assert len(valid) == 1
    assert filtered == 0


def test_range_line_hint_uses_first_number():
    """'42-45' → first number 42, which is within a 50-line file."""
    fc = _FC("app.py", excerpt="cursor.execute(query)\n", line_count=50)
    f = _finding(line_hint="42-45")
    valid, filtered, _ = _validate_against_source([f], fc)
    assert len(valid) == 1
    assert filtered == 0


def test_range_line_hint_out_of_bounds_is_removed():
    """'99-101' → first number 99, beyond 50-line file."""
    fc = _FC("app.py", excerpt="cursor.execute(query)\n", line_count=50)
    f = _finding(line_hint="99-101")
    valid, filtered, _ = _validate_against_source([f], fc)
    assert len(valid) == 0
    assert filtered == 1


def test_non_numeric_line_hint_skips_line_check():
    fc = _FC("app.py", excerpt="cursor.execute(query)\n", line_count=5)
    f = _finding(line_hint="approx line 3")
    valid, filtered, _ = _validate_against_source([f], fc)
    assert len(valid) == 1
    assert filtered == 0


# ── Evidence grounding: warn when tokens absent from excerpt ─────────────────


def test_evidence_tokens_present_in_excerpt_no_warning():
    fc = _FC("app.py", excerpt="cursor.execute(query, params)\n", line_count=50)
    f = _finding(evidence="cursor.execute(query, params)")
    _, _, warnings = _validate_against_source([f], fc)
    assert warnings == []


def test_evidence_tokens_absent_from_excerpt_adds_warning():
    """Evidence mentions code that is nowhere in the excerpt — hallucination warning."""
    fc = _FC("app.py", excerpt="return render_template('index.html')\n", line_count=50)
    f = _finding(evidence="cursor.execute(query, params)")
    _, _, warnings = _validate_against_source([f], fc)
    assert len(warnings) == 1
    assert "BUG-001" in warnings[0]


def test_short_evidence_skips_grounding_check():
    """Evidence with fewer than _MIN_EVIDENCE_TOKENS significant tokens → no warning."""
    fc = _FC("app.py", excerpt="return None\n", line_count=50)
    # "x = y" has no token with len > 3 → below threshold, skip grounding
    f = _finding(evidence="x = y")
    _, _, warnings = _validate_against_source([f], fc)
    assert warnings == []


def test_empty_evidence_skips_grounding_check():
    fc = _FC("app.py", excerpt="some code here\n", line_count=50)
    f = _finding(evidence="")
    _, _, warnings = _validate_against_source([f], fc)
    assert warnings == []


# ── Mixed batch ───────────────────────────────────────────────────────────────


def test_mixed_batch_filters_only_hallucinated():
    """Three findings: one good, one out-of-bounds, one with bad evidence."""
    excerpt = "cursor.execute(query)\nreturn result\n"
    fc = _FC("app.py", excerpt=excerpt, line_count=10)

    good = _finding(bug_id="BUG-001", line_hint="2", evidence="cursor.execute(query)")
    oob = _finding(bug_id="BUG-002", line_hint="999", evidence="cursor.execute(query)")
    warn = _finding(bug_id="BUG-003", line_hint="2", evidence="subprocess.popen(shell=True)")

    valid, filtered, warnings = _validate_against_source([good, oob, warn], fc)

    assert len(valid) == 2  # good + warn (warn is kept, not removed)
    assert filtered == 1  # only oob is hard-removed
    assert len(warnings) == 1  # BUG-003 gets a grounding warning
    assert "BUG-003" in warnings[0]
    assert [f.bug_id for f in valid] == ["BUG-001", "BUG-003"]


# ── integrate with analyze_repository_file (unit, no real LLM) ───────────────


@pytest.mark.asyncio
async def test_analyze_repository_file_filters_oob_finding():
    """End-to-end: LLM returns a finding past line_count — it must be absent from result."""
    from unittest.mock import AsyncMock
    import json

    from shared.llm.repo_file_analyzer import analyze_repository_file
    from shared.tools.repo_context_builder import FileContext

    excerpt = "def hello():\n    return 'world'\n"
    fc = FileContext(
        file_path="hello.py",
        size_bytes=len(excerpt),
        excerpt=excerpt,
        truncated=False,
        line_count=2,
    )

    # LLM returns one real finding (line 1) and one hallucinated (line 999)
    llm_json = json.dumps(
        {
            "findings": [
                {
                    "bug_id": "BUG-001",
                    "file_path": "hello.py",
                    "line_hint": "1",
                    "severity": "LOW",
                    "category": "STYLE",
                    "description": "missing docstring",
                    "evidence": "def hello():",
                    "recommended_fix": "add a docstring",
                },
                {
                    "bug_id": "BUG-HAL",
                    "file_path": "hello.py",
                    "line_hint": "999",  # hallucinated — file only has 2 lines
                    "severity": "HIGH",
                    "category": "SECURITY",
                    "description": "invented issue",
                    "evidence": "cursor.execute(query)",
                    "recommended_fix": "use parameterised queries",
                },
            ]
        }
    )

    provider = AsyncMock()
    resp = AsyncMock()
    resp.content = llm_json
    resp.latency_ms = 100.0
    resp.tokens_in = 50
    resp.tokens_out = 80
    provider.complete.return_value = resp

    result = await analyze_repository_file(provider, issue="test", file_ctx=fc)

    assert len(result.findings) == 1
    assert result.findings[0].bug_id == "BUG-001"
    assert result.hallucination_filtered == 1
