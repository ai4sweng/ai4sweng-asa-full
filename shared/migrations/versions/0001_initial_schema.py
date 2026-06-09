"""Initial schema — all 9 tables from ai4sweng-kio1 + KIOCapabilityRecord.

Revision ID: 0001
Create Date: 2026-06-07
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    # agents
    op.create_table(
        "agents",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("kio_id", sa.String(32), nullable=False, unique=True),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("role", sa.String(64), nullable=False),
        sa.Column("capabilities", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("status", sa.String(32), nullable=False, server_default="ACTIVE"),
        sa.Column("available", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("port", sa.Integer(), nullable=True),
        sa.Column("last_heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_agents_role", "agents", ["role"])

    # workflows
    op.create_table(
        "workflows",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("project_id", sa.String(64), nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("state", sa.String(64), nullable=False),
        sa.Column("correlation_id", sa.String(36), nullable=False),
        sa.Column("session_id", sa.String(36), nullable=True),
        sa.Column("owner", sa.String(128), nullable=True),
        sa.Column("trace_id", sa.String(64), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("metadata", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_workflows_project_id", "workflows", ["project_id"])
    op.create_index("ix_workflows_state", "workflows", ["state"])
    op.create_index("ix_workflows_correlation_id", "workflows", ["correlation_id"])
    op.create_index("ix_workflows_session_id", "workflows", ["session_id"])

    # tasks
    op.create_table(
        "tasks",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("workflow_id", sa.String(36), sa.ForeignKey("workflows.id"), nullable=False),
        sa.Column("kio_id", sa.String(32), nullable=True),
        sa.Column("step_id", sa.String(64), nullable=False),
        sa.Column("agent_role", sa.String(64), nullable=False),
        sa.Column("state", sa.String(64), nullable=False),
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_code", sa.String(64), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("metadata", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("workflow_id", "idempotency_key"),
    )
    op.create_index("ix_tasks_workflow_id", "tasks", ["workflow_id"])
    op.create_index("ix_tasks_kio_id", "tasks", ["kio_id"])
    op.create_index("ix_tasks_state", "tasks", ["state"])

    # messages
    op.create_table(
        "messages",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("message_id", sa.String(36), nullable=False, unique=True),
        sa.Column("workflow_id", sa.String(36), sa.ForeignKey("workflows.id"), nullable=True),
        sa.Column("correlation_id", sa.String(36), nullable=False),
        sa.Column("causation_id", sa.String(36), nullable=True),
        sa.Column("subject", sa.String(128), nullable=False),
        sa.Column("message_type", sa.String(64), nullable=False),
        sa.Column("sender", sa.String(128), nullable=False),
        sa.Column("recipient", sa.String(128), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("envelope", sa.JSON(), nullable=False),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
    )
    op.create_index("ix_messages_workflow_id", "messages", ["workflow_id"])
    op.create_index("ix_messages_correlation_id", "messages", ["correlation_id"])
    op.create_index("ix_messages_idempotency_key", "messages", ["idempotency_key"])
    op.create_index("ix_messages_subject", "messages", ["subject"])

    # artifacts
    op.create_table(
        "artifacts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("workflow_id", sa.String(36), sa.ForeignKey("workflows.id"), nullable=False),
        sa.Column("task_id", sa.String(36), sa.ForeignKey("tasks.id"), nullable=True),
        sa.Column("kio_id", sa.String(32), nullable=True),
        sa.Column("artifact_type", sa.String(64), nullable=False),
        sa.Column("parent_artifact_id", sa.String(36), sa.ForeignKey("artifacts.id"), nullable=True),
        sa.Column("content", sa.JSON(), nullable=False),
        sa.Column("checksum", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_artifacts_workflow_id", "artifacts", ["workflow_id"])
    op.create_index("ix_artifacts_artifact_type", "artifacts", ["artifact_type"])
    op.create_index("ix_artifacts_kio_id", "artifacts", ["kio_id"])

    # human_approvals
    op.create_table(
        "human_approvals",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("workflow_id", sa.String(36), sa.ForeignKey("workflows.id"), nullable=False),
        sa.Column("task_id", sa.String(36), sa.ForeignKey("tasks.id"), nullable=False),
        sa.Column("kio_id", sa.String(32), nullable=True),
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column("decision", sa.String(16), nullable=True),
        sa.Column("feedback", sa.Text(), nullable=True),
        sa.Column("decided_by", sa.String(128), nullable=False, server_default="human"),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("metadata", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_human_approvals_workflow_id", "human_approvals", ["workflow_id"])

    # metrics
    op.create_table(
        "metrics",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("workflow_id", sa.String(36), sa.ForeignKey("workflows.id"), nullable=False),
        sa.Column("task_id", sa.String(36), nullable=True),
        sa.Column("kio_id", sa.String(32), nullable=True),
        sa.Column("metric_name", sa.String(64), nullable=False),
        sa.Column("metric_value", sa.Float(), nullable=False),
        sa.Column("unit", sa.String(32), nullable=False, server_default="ms"),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_metrics_workflow_id", "metrics", ["workflow_id"])

    # workflow_spans
    op.create_table(
        "workflow_spans",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("workflow_id", sa.String(36), sa.ForeignKey("workflows.id"), nullable=False),
        sa.Column("span_name", sa.String(128), nullable=False),
        sa.Column("trace_id", sa.String(64), nullable=False),
        sa.Column("span_id", sa.String(64), nullable=False),
        sa.Column("parent_span_id", sa.String(64), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("metadata", sa.JSON(), nullable=False, server_default="{}"),
    )
    op.create_index("ix_workflow_spans_workflow_id", "workflow_spans", ["workflow_id"])

    # reports
    op.create_table(
        "reports",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("workflow_id", sa.String(36), sa.ForeignKey("workflows.id"), nullable=False),
        sa.Column("report_type", sa.String(64), nullable=False),
        sa.Column("content", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_reports_workflow_id", "reports", ["workflow_id"])

    # kio_capabilities
    op.create_table(
        "kio_capabilities",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("kio_id", sa.String(32), nullable=False, unique=True),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("input_artifact_type", sa.String(64), nullable=True),
        sa.Column("output_artifact_type", sa.String(64), nullable=False),
        sa.Column("version", sa.String(32), nullable=False, server_default="1.0.0"),
        sa.Column("tags", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("available", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("port", sa.Integer(), nullable=True),
        sa.Column("registered_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_kio_capabilities_available", "kio_capabilities", ["available"])


def downgrade() -> None:
    op.drop_table("kio_capabilities")
    op.drop_table("reports")
    op.drop_table("workflow_spans")
    op.drop_table("metrics")
    op.drop_table("human_approvals")
    op.drop_table("artifacts")
    op.drop_table("messages")
    op.drop_table("tasks")
    op.drop_table("workflows")
    op.drop_table("agents")
