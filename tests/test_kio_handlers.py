"""Direct handler tests for kio2, kio5, kio8 — Phase 9.3.3.

Handlers are imported directly (no HTTP, no NATS) by adding kio_shells/
to sys.path so `from kio_base import ...` resolves correctly.
The LLM provider is mocked in every test — no real LLM calls are made.
"""
from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Make kio_base importable as a top-level module (kio handlers use bare imports)
sys.path.insert(0, str(Path(__file__).parent.parent / "apps" / "kio_shells"))

from kio_base import MessageEnvelope  # noqa: E402


# ── Envelope factory ──────────────────────────────────────────────────────────

def _envelope(kio_id: str, payload: dict | None = None) -> MessageEnvelope:
    return MessageEnvelope(
        message_id=str(uuid.uuid4()),
        session_id=str(uuid.uuid4()),
        workflow_id="wf-test",
        source="orchestrator",
        target=kio_id,
        timestamp="2026-06-10T00:00:00+00:00",
        message_type="JOB_REQUEST",
        payload=payload or {},
    )


def _mock_provider(response_content: str) -> AsyncMock:
    """Return an async mock LLM provider whose .complete() returns the given text."""
    provider = AsyncMock()
    response = MagicMock()
    response.content = response_content
    provider.complete.return_value = response
    return provider


# ── kio2 — Planning Agent ─────────────────────────────────────────────────────

class TestKio2Handler:
    @pytest.fixture(autouse=True)
    def reset_provider(self):
        """Reset kio2's module-level _provider singleton between tests."""
        import kio2.main as m
        orig = m._provider
        m._provider = None
        yield
        m._provider = None

    async def test_returns_done_status(self):
        from kio2.main import handler

        llm_plan = json.dumps({
            "pipeline": ["kio2", "kio3", "kio8"],
            "hitl_after": [],
            "stages": [],
            "reasoning": "Small task — repo analysis then report.",
            "confidence": 0.9,
        })

        with patch("kio2.main._get_provider", return_value=_mock_provider(llm_plan)):
            result = await handler(_envelope("kio2", {"description": "Fix bugs", "working_directory": "/tmp"}))

        assert result["status"] == "DONE"

    async def test_returns_kio_sequence(self):
        from kio2.main import handler

        llm_plan = json.dumps({
            "pipeline": ["kio2", "kio3", "kio8"],
            "hitl_after": [],
            "stages": [],
            "reasoning": "Analysis only.",
            "confidence": 0.95,
        })

        with patch("kio2.main._get_provider", return_value=_mock_provider(llm_plan)):
            result = await handler(_envelope("kio2", {"description": "Analyse repo"}))

        assert isinstance(result["kio_sequence"], list)
        assert len(result["kio_sequence"]) >= 2

    async def test_pipeline_invariants_kio2_first_kio8_last(self):
        """kio2 handler enforces: pipeline[0]==kio2, pipeline[-1]==kio8."""
        from kio2.main import handler

        # LLM forgets to include kio2 and kio8 — handler should fix it
        llm_plan = json.dumps({
            "pipeline": ["kio3", "kio5"],
            "hitl_after": ["kio5"],
            "stages": [],
            "reasoning": "Minimal.",
            "confidence": 0.7,
        })

        with patch("kio2.main._get_provider", return_value=_mock_provider(llm_plan)):
            result = await handler(_envelope("kio2", {"description": "Analyse"}))

        seq = result["kio_sequence"]
        assert seq[0] == "kio2"
        assert seq[-1] == "kio8"

    async def test_hitl_after_returned(self):
        from kio2.main import handler

        llm_plan = json.dumps({
            "pipeline": ["kio2", "kio3", "kio5", "kio8"],
            "hitl_after": ["kio5"],
            "stages": [],
            "reasoning": "Bug detection requires approval.",
            "confidence": 0.85,
        })

        with patch("kio2.main._get_provider", return_value=_mock_provider(llm_plan)):
            result = await handler(_envelope("kio2", {"description": "Detect bugs"}))

        assert isinstance(result["hitl_after"], list)

    async def test_artifact_data_contains_pipeline_key(self):
        from kio2.main import handler

        llm_plan = json.dumps({
            "pipeline": ["kio2", "kio8"],
            "hitl_after": [],
            "stages": [],
            "reasoning": "Minimal.",
            "confidence": 1.0,
        })

        with patch("kio2.main._get_provider", return_value=_mock_provider(llm_plan)):
            result = await handler(_envelope("kio2", {"description": "Quick scan"}))

        assert "pipeline" in result["artifact_data"]
        assert "hitl_after" in result["artifact_data"]
        assert "confidence" in result["artifact_data"]

    async def test_fallback_pipeline_on_non_json_response(self):
        """When LLM returns non-JSON, handler falls back to FULL_PIPELINE — still DONE."""
        from kio2.main import handler

        with patch("kio2.main._get_provider", return_value=_mock_provider("not json at all")):
            result = await handler(_envelope("kio2", {"description": "Something"}))

        assert result["status"] == "DONE"
        assert len(result["kio_sequence"]) > 2  # full pipeline

    async def test_fallback_pipeline_on_provider_exception(self):
        """When the LLM provider raises, handler returns fallback — still DONE."""
        from kio2.main import handler

        broken_provider = AsyncMock()
        broken_provider.complete.side_effect = RuntimeError("LLM timeout")

        with patch("kio2.main._get_provider", return_value=broken_provider):
            result = await handler(_envelope("kio2", {"description": "Emergency scan"}))

        assert result["status"] == "DONE"
        assert result["artifact_data"]["confidence"] == 0.0  # fallback confidence


# ── kio5 — Bug Detector Agent ─────────────────────────────────────────────────

class TestKio5Handler:
    @pytest.fixture(autouse=True)
    def reset_provider(self):
        import kio5.main as m
        m._provider = None
        yield
        m._provider = None

    async def test_always_returns_review_required(self):
        """kio5 always returns REVIEW_REQUIRED — human approval is mandatory."""
        from kio5.main import handler

        llm_resp = json.dumps({
            "summary": "Two SQL injection vulnerabilities confirmed.",
            "confirmed_count": 2,
            "bugs": [
                {"bug_id": "BUG-001", "file": "app.py", "line": 42,
                 "severity": "CRITICAL", "kind": "sql_injection",
                 "description": "Unparameterized query", "cwe": "CWE-89",
                 "suggested_fix": "Use parameterized queries.", "confirmed": True},
            ],
        })

        with patch("kio5.main._get_provider", return_value=_mock_provider(llm_resp)):
            result = await handler(_envelope("kio5", {
                "description": "Validate findings",
                "last_artifact": {"findings": [{"file": "app.py", "line": 42, "kind": "sql_injection"}]},
            }))

        assert result["status"] == "REVIEW_REQUIRED"

    async def test_artifact_data_has_bug_count(self):
        from kio5.main import handler

        llm_resp = json.dumps({
            "summary": "One bug confirmed.",
            "confirmed_count": 1,
            "bugs": [
                {"bug_id": "BUG-001", "file": "main.py", "line": 10,
                 "severity": "HIGH", "kind": "xss",
                 "description": "Unsanitised input", "cwe": "CWE-79",
                 "suggested_fix": "Escape HTML.", "confirmed": True},
            ],
        })

        with patch("kio5.main._get_provider", return_value=_mock_provider(llm_resp)):
            result = await handler(_envelope("kio5", {
                "description": "Scan",
                "last_artifact": {},
            }))

        assert "bug_count" in result["artifact_data"]
        assert isinstance(result["artifact_data"]["bug_count"], int)

    async def test_artifact_data_contains_bugs_list(self):
        from kio5.main import handler

        llm_resp = json.dumps({
            "summary": "No bugs.",
            "confirmed_count": 0,
            "bugs": [],
        })

        with patch("kio5.main._get_provider", return_value=_mock_provider(llm_resp)):
            result = await handler(_envelope("kio5", {"description": "Clean repo"}))

        assert "bugs" in result["artifact_data"]
        assert isinstance(result["artifact_data"]["bugs"], list)

    async def test_hitl_question_included(self):
        from kio5.main import handler

        llm_resp = json.dumps({"summary": "Clean.", "confirmed_count": 0, "bugs": []})

        with patch("kio5.main._get_provider", return_value=_mock_provider(llm_resp)):
            result = await handler(_envelope("kio5", {"description": "Check"}))

        assert "hitl_question" in result
        assert isinstance(result["hitl_question"], str)
        assert len(result["hitl_question"]) > 0

    async def test_returns_review_required_even_when_llm_fails(self):
        """kio5 must return REVIEW_REQUIRED even if LLM raises."""
        from kio5.main import handler

        broken = AsyncMock()
        broken.complete.side_effect = RuntimeError("Provider down")

        with patch("kio5.main._get_provider", return_value=broken):
            result = await handler(_envelope("kio5", {
                "description": "Test",
                "last_artifact": {},
            }))

        assert result["status"] == "REVIEW_REQUIRED"
        assert result["artifact_data"]["bug_count"] == 0  # no bugs on error


# ── kio8 — Evidence Report Agent ─────────────────────────────────────────────

class TestKio8Handler:
    @pytest.fixture(autouse=True)
    def reset_provider(self):
        import kio8.main as m
        m._provider = None
        yield
        m._provider = None

    async def test_returns_done_status(self):
        from kio8.main import handler

        llm_resp = json.dumps({
            "executive_summary": "All tests passed, no critical bugs.",
            "findings": ["XSS in login form — patched"],
            "remediation": "Parameterized queries added.",
            "test_evidence": "47/47 tests passed, 82% coverage.",
            "verdict": "PASS",
            "verdict_reason": "All tests green, no open vulnerabilities.",
        })

        with patch("kio8.main._get_provider", return_value=_mock_provider(llm_resp)):
            result = await handler(_envelope("kio8", {
                "description": "Final report",
                "last_artifact": {"passed": 47, "failed": 0, "total": 47, "coverage_pct": 82},
            }))

        assert result["status"] == "DONE"

    async def test_artifact_data_has_report_key(self):
        from kio8.main import handler

        llm_resp = json.dumps({
            "executive_summary": "Tests passed.",
            "findings": [],
            "remediation": "No patches needed.",
            "test_evidence": "10/10 tests passed.",
            "verdict": "PASS",
            "verdict_reason": "All tests green.",
        })

        with patch("kio8.main._get_provider", return_value=_mock_provider(llm_resp)):
            result = await handler(_envelope("kio8", {"description": "Report"}))

        assert "report" in result["artifact_data"]
        report = result["artifact_data"]["report"]
        assert "executive_summary" in report
        assert "verdict" in report

    async def test_artifact_data_has_produced_at(self):
        from kio8.main import handler

        llm_resp = json.dumps({
            "executive_summary": "Done.",
            "findings": [],
            "remediation": "",
            "test_evidence": "",
            "verdict": "PASS",
            "verdict_reason": "",
        })

        with patch("kio8.main._get_provider", return_value=_mock_provider(llm_resp)):
            result = await handler(_envelope("kio8", {"description": "Wrap up"}))

        assert "produced_at" in result["artifact_data"]

    async def test_message_contains_verdict(self):
        from kio8.main import handler

        llm_resp = json.dumps({
            "executive_summary": "Failed.",
            "findings": ["SQL injection"],
            "remediation": "Patch pending.",
            "test_evidence": "5/10 tests passed.",
            "verdict": "FAIL",
            "verdict_reason": "Tests failing.",
        })

        with patch("kio8.main._get_provider", return_value=_mock_provider(llm_resp)):
            result = await handler(_envelope("kio8", {
                "description": "Post-patch",
                "last_artifact": {"passed": 5, "failed": 5, "total": 10},
            }))

        assert "FAIL" in result["message"] or "PASS" in result["message"]

    async def test_fallback_report_on_llm_error(self):
        """kio8 generates a fallback report if the LLM raises — still returns DONE."""
        from kio8.main import handler

        broken = AsyncMock()
        broken.complete.side_effect = RuntimeError("LLM down")

        with patch("kio8.main._get_provider", return_value=broken):
            result = await handler(_envelope("kio8", {
                "description": "Emergency report",
                "last_artifact": {"passed": 10, "failed": 2, "total": 12},
            }))

        assert result["status"] == "DONE"
        assert "report" in result["artifact_data"]
        # Fallback verdict: FAIL because failed > 0
        assert result["artifact_data"]["report"]["verdict"] == "FAIL"

    async def test_fallback_report_all_pass_verdict(self):
        """Fallback verdict is PASS when failed == 0."""
        from kio8.main import handler

        broken = AsyncMock()
        broken.complete.side_effect = RuntimeError("LLM down")

        with patch("kio8.main._get_provider", return_value=broken):
            result = await handler(_envelope("kio8", {
                "description": "Pass report",
                "last_artifact": {"passed": 20, "failed": 0, "total": 20},
            }))

        assert result["artifact_data"]["report"]["verdict"] == "PASS"
