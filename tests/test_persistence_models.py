"""Tests for shared.persistence.models — ORM columns and defaults."""

import pytest

from shared.persistence.models import (
    AgentRecord,
    ArtifactRecord,
    HumanApprovalRecord,
    KIOCapabilityRecord,
    MetricRecord,
    TaskRecord,
    WorkflowRecord,
)


class TestWorkflowRecord:
    def test_required_columns_exist(self):
        cols = {c.key for c in WorkflowRecord.__table__.columns}
        assert {"id", "project_id", "name", "state", "correlation_id",
                "session_id", "owner", "created_at"} <= cols

    def test_session_id_and_owner_are_nullable(self):
        table = WorkflowRecord.__table__
        assert table.c.session_id.nullable
        assert table.c.owner.nullable


class TestTaskRecord:
    def test_kio_id_column_exists(self):
        cols = {c.key for c in TaskRecord.__table__.columns}
        assert "kio_id" in cols

    def test_kio_id_is_nullable(self):
        assert TaskRecord.__table__.c.kio_id.nullable


class TestAgentRecord:
    def test_kio_id_is_unique(self):
        col = AgentRecord.__table__.c.kio_id
        assert col.unique

    def test_available_and_port_columns(self):
        cols = {c.key for c in AgentRecord.__table__.columns}
        assert {"available", "port", "kio_id"} <= cols

    def test_port_is_nullable(self):
        assert AgentRecord.__table__.c.port.nullable


class TestArtifactRecord:
    def test_kio_id_column(self):
        cols = {c.key for c in ArtifactRecord.__table__.columns}
        assert "kio_id" in cols
        assert ArtifactRecord.__table__.c.kio_id.nullable


class TestHumanApprovalRecord:
    def test_feedback_and_kio_id_columns(self):
        cols = {c.key for c in HumanApprovalRecord.__table__.columns}
        assert {"feedback", "kio_id"} <= cols


class TestKIOCapabilityRecord:
    def test_kio_id_unique(self):
        col = KIOCapabilityRecord.__table__.c.kio_id
        assert col.unique

    def test_required_columns(self):
        cols = {c.key for c in KIOCapabilityRecord.__table__.columns}
        assert {"kio_id", "name", "description", "output_artifact_type",
                "available", "version", "tags"} <= cols


class TestMetricRecord:
    def test_kio_id_column(self):
        cols = {c.key for c in MetricRecord.__table__.columns}
        assert "kio_id" in cols
