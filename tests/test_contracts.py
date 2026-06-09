"""Tests for shared.contracts — envelope, artifacts, capabilities, tasks, errors."""

import typing

import pytest
from pydantic import ValidationError

from shared.contracts.artifacts import (
    ARTIFACT_TYPES,
    AnyArtifact,
    BugReportArtifact,
    IssueArtifact,
    RepoAnalysisArtifact,
)
from shared.contracts.capabilities import KIOCapability
from shared.contracts.errors import KIOError, KIOErrorCode
from shared.contracts.kio_envelope import KIOEnvelope, MessageEnvelope, build_envelope
from shared.contracts.tasks import KIOTaskRequest, KIOTaskResult


# ---------------------------------------------------------------------------
# KIOEnvelope / build_envelope
# ---------------------------------------------------------------------------

class TestKIOEnvelope:
    def test_build_envelope_sets_required_fields(self):
        env = build_envelope(
            subject="kio.test.request",
            message_type="REQUEST",
            sender="kio1",
            recipient="kio2",
            correlation_id="corr-123",
            payload={"key": "value"},
            workflow_id="wf-123",
            session_id="sess-1",
        )
        assert env.subject == "kio.test.request"
        assert env.sender == "kio1"
        assert env.workflow_id == "wf-123"
        assert env.session_id == "sess-1"
        assert env.correlation_id == "corr-123"
        assert env.message_id  # auto-generated UUID
        assert env.idempotency_key == env.message_id  # default to message_id

    def test_build_envelope_custom_idempotency_key(self):
        env = build_envelope(
            subject="s",
            message_type="T",
            sender="a",
            recipient="b",
            correlation_id="c",
            idempotency_key="my-key",
        )
        assert env.idempotency_key == "my-key"

    def test_envelope_serializes_to_dict(self):
        env = build_envelope(
            subject="s", message_type="T", sender="a", recipient="b", correlation_id="c"
        )
        d = env.to_dict()
        assert "message_id" in d
        assert "timestamp" in d
        assert "protocol_version" in d

    def test_message_envelope_alias(self):
        assert MessageEnvelope is KIOEnvelope

    def test_envelope_requires_sender(self):
        with pytest.raises((ValidationError, TypeError)):
            KIOEnvelope(
                message_id="m",
                correlation_id="c",
                timestamp="t",
                protocol_version="1",
                project_id="p",
                sender=None,  # type: ignore
                recipient="r",
                message_type="T",
                subject="s",
                idempotency_key="k",
            )


# ---------------------------------------------------------------------------
# Artifacts
# ---------------------------------------------------------------------------

class TestArtifacts:
    def _base(self, kio: str) -> dict:
        return {"artifact_id": "art-1", "workflow_id": "wf-1", "producer_kio": kio}

    def test_issue_artifact_defaults(self):
        a = IssueArtifact(**self._base("kio2"), description="fix login bug")
        assert a.producer_kio == "kio2"
        assert a.requirements == []
        assert a.constraints == []

    def test_repo_analysis_artifact_defaults(self):
        a = RepoAnalysisArtifact(**self._base("kio3"), repo_path="/tmp/repo")
        assert a.file_count == 0
        assert a.findings == []

    def test_bug_report_artifact_defaults(self):
        a = BugReportArtifact(**self._base("kio5"))
        assert a.bugs == []
        assert a.total_count == 0

    def test_artifact_types_tuple_usable_with_isinstance(self):
        a = IssueArtifact(
            artifact_id="x", workflow_id="w", producer_kio="kio2", description="d"
        )
        assert isinstance(a, ARTIFACT_TYPES)

    def test_any_artifact_is_union_with_nine_members(self):
        args = typing.get_args(AnyArtifact)
        assert len(args) == 9

    def test_created_at_auto_populated(self):
        a = IssueArtifact(**self._base("kio2"), description="d")
        assert a.created_at  # ISO datetime string


# ---------------------------------------------------------------------------
# KIOCapability
# ---------------------------------------------------------------------------

class TestKIOCapability:
    def test_capability_defaults(self):
        cap = KIOCapability(
            kio_id="kio3",
            name="Repo Analyser",
            description="Scans repositories",
            input_artifact_type=None,
            output_artifact_type="repo_analysis",
        )
        assert cap.version == "1.0.0"
        assert cap.available is True
        assert cap.tags == []
        assert cap.port is None

    def test_capability_requires_kio_id(self):
        with pytest.raises(ValidationError):
            KIOCapability(
                name="X",
                description="Y",
                input_artifact_type=None,
                output_artifact_type="z",
            )

    def test_capability_with_port_and_tags(self):
        cap = KIOCapability(
            kio_id="kio5",
            name="Bug Detector",
            description="Detects bugs",
            input_artifact_type="repo_analysis",
            output_artifact_type="bug_report",
            port=8005,
            tags=["bugs", "analysis"],
        )
        assert cap.port == 8005
        assert "bugs" in cap.tags


# ---------------------------------------------------------------------------
# KIOTaskRequest / KIOTaskResult
# ---------------------------------------------------------------------------

class TestTasks:
    def test_task_request_required_fields(self):
        req = KIOTaskRequest(
            task_id="t1",
            workflow_id="wf1",
            kio_id="kio3",
            correlation_id="corr-1",
            idempotency_key="idem-1",
        )
        assert req.payload == {}
        assert req.upstream_artifact_ids == []
        assert req.session_id is None

    def test_task_result_required_fields(self):
        res = KIOTaskResult(
            task_id="t1",
            workflow_id="wf1",
            kio_id="kio3",
            status="COMPLETED",
        )
        assert res.tokens_in == 0
        assert res.tokens_out == 0
        assert res.duration_ms == 0.0
        assert res.artifact_id is None

    def test_task_request_missing_correlation_id_fails(self):
        with pytest.raises(ValidationError):
            KIOTaskRequest(
                task_id="t1",
                workflow_id="wf1",
                kio_id="kio3",
                idempotency_key="k",
            )


# ---------------------------------------------------------------------------
# KIOError / KIOErrorCode
# ---------------------------------------------------------------------------

class TestErrors:
    def test_kio_error_attributes(self):
        err = KIOError(
            kio_id="kio3",
            error_code=KIOErrorCode.KIO_TIMEOUT,
            message="LLM timed out",
        )
        assert err.kio_id == "kio3"
        assert err.error_code == "KIO_TIMEOUT"
        assert err.retryable is False

    def test_compensation_error_inherits(self):
        from shared.contracts.errors import CompensationError
        err = CompensationError(
            kio_id="kio5",
            error_code=KIOErrorCode.COMPENSATION_FAILED,
            message="rollback failed",
            compensated_task_id="t-old",
            compensation_action="delete_patch",
        )
        assert err.compensated_task_id == "t-old"

    def test_error_code_constants_are_strings(self):
        assert isinstance(KIOErrorCode.KIO_UNAVAILABLE, str)
        assert isinstance(KIOErrorCode.LLM_PARSE_FAILURE, str)
        assert isinstance(KIOErrorCode.AUTH_REQUIRED, str)
