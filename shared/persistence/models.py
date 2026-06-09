"""
SQLAlchemy ORM models — System of Record.

Every workflow event, message, artifact, approval, and KIO capability
is stored here for replay, provenance, and audit.
"""

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy import (
    JSON, Boolean, DateTime, Float, ForeignKey,
    Integer, String, Text, UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _new_id() -> str:
    return str(uuid4())


class Base(DeclarativeBase):
    """Declarative base for all persistence models."""


# ---------------------------------------------------------------------------
# Workflow & Task
# ---------------------------------------------------------------------------

class WorkflowRecord(Base):
    __tablename__ = "workflows"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_id)
    project_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    state: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    correlation_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    session_id: Mapped[str | None] = mapped_column(String(36), index=True)
    owner: Mapped[str | None] = mapped_column(String(128))
    trace_id: Mapped[str | None] = mapped_column(String(64))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    metadata_: Mapped[dict[str, Any]] = mapped_column("metadata", JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    tasks: Mapped[list["TaskRecord"]] = relationship(back_populates="workflow")
    artifacts: Mapped[list["ArtifactRecord"]] = relationship(back_populates="workflow")
    messages: Mapped[list["MessageRecord"]] = relationship(back_populates="workflow")
    approvals: Mapped[list["HumanApprovalRecord"]] = relationship(back_populates="workflow")
    spans: Mapped[list["WorkflowSpanRecord"]] = relationship(back_populates="workflow")
    metrics: Mapped[list["MetricRecord"]] = relationship(back_populates="workflow")
    reports: Mapped[list["ReportRecord"]] = relationship(back_populates="workflow")


class TaskRecord(Base):
    __tablename__ = "tasks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_id)
    workflow_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("workflows.id"), nullable=False, index=True
    )
    kio_id: Mapped[str | None] = mapped_column(String(32), index=True)
    step_id: Mapped[str] = mapped_column(String(64), nullable=False)
    agent_role: Mapped[str] = mapped_column(String(64), nullable=False)
    state: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_message: Mapped[str | None] = mapped_column(Text)
    metadata_: Mapped[dict[str, Any]] = mapped_column("metadata", JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    workflow: Mapped["WorkflowRecord"] = relationship(back_populates="tasks")

    __table_args__ = (UniqueConstraint("workflow_id", "idempotency_key"),)


# ---------------------------------------------------------------------------
# Messaging
# ---------------------------------------------------------------------------

class MessageRecord(Base):
    __tablename__ = "messages"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_id)
    message_id: Mapped[str] = mapped_column(String(36), nullable=False, unique=True, index=True)
    workflow_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("workflows.id"), index=True
    )
    correlation_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    causation_id: Mapped[str | None] = mapped_column(String(36))
    subject: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    message_type: Mapped[str] = mapped_column(String(64), nullable=False)
    sender: Mapped[str] = mapped_column(String(128), nullable=False)
    recipient: Mapped[str] = mapped_column(String(128), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    envelope: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False, index=True)

    workflow: Mapped["WorkflowRecord | None"] = relationship(back_populates="messages")


# ---------------------------------------------------------------------------
# Artifacts & Provenance
# ---------------------------------------------------------------------------

class ArtifactRecord(Base):
    __tablename__ = "artifacts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_id)
    workflow_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("workflows.id"), nullable=False, index=True
    )
    task_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("tasks.id"))
    kio_id: Mapped[str | None] = mapped_column(String(32), index=True)
    artifact_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    parent_artifact_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("artifacts.id"))
    content: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    workflow: Mapped["WorkflowRecord"] = relationship(back_populates="artifacts")


# ---------------------------------------------------------------------------
# Human Approval (HITL)
# ---------------------------------------------------------------------------

class HumanApprovalRecord(Base):
    __tablename__ = "human_approvals"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_id)
    workflow_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("workflows.id"), nullable=False, index=True
    )
    task_id: Mapped[str] = mapped_column(String(36), ForeignKey("tasks.id"), nullable=False)
    kio_id: Mapped[str | None] = mapped_column(String(32))
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    decision: Mapped[str | None] = mapped_column(String(16))   # APPROVED | REJECTED
    feedback: Mapped[str | None] = mapped_column(Text)
    decided_by: Mapped[str] = mapped_column(String(128), default="human")
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    metadata_: Mapped[dict[str, Any]] = mapped_column("metadata", JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    workflow: Mapped["WorkflowRecord"] = relationship(back_populates="approvals")


# ---------------------------------------------------------------------------
# Observability
# ---------------------------------------------------------------------------

class MetricRecord(Base):
    __tablename__ = "metrics"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_id)
    workflow_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("workflows.id"), nullable=False, index=True
    )
    task_id: Mapped[str | None] = mapped_column(String(36))
    kio_id: Mapped[str | None] = mapped_column(String(32))
    metric_name: Mapped[str] = mapped_column(String(64), nullable=False)
    metric_value: Mapped[float] = mapped_column(Float, nullable=False)
    unit: Mapped[str] = mapped_column(String(32), default="ms")
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    workflow: Mapped["WorkflowRecord"] = relationship(back_populates="metrics")


class WorkflowSpanRecord(Base):
    __tablename__ = "workflow_spans"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_id)
    workflow_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("workflows.id"), nullable=False, index=True
    )
    span_name: Mapped[str] = mapped_column(String(128), nullable=False)
    trace_id: Mapped[str] = mapped_column(String(64), nullable=False)
    span_id: Mapped[str] = mapped_column(String(64), nullable=False)
    parent_span_id: Mapped[str | None] = mapped_column(String(64))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    metadata_: Mapped[dict[str, Any]] = mapped_column("metadata", JSON, default=dict)

    workflow: Mapped["WorkflowRecord"] = relationship(back_populates="spans")


# ---------------------------------------------------------------------------
# Reports
# ---------------------------------------------------------------------------

class ReportRecord(Base):
    __tablename__ = "reports"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_id)
    workflow_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("workflows.id"), nullable=False, index=True
    )
    report_type: Mapped[str] = mapped_column(String(64), nullable=False)
    content: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    workflow: Mapped["WorkflowRecord"] = relationship(back_populates="reports")


# ---------------------------------------------------------------------------
# Users (authentication)
# ---------------------------------------------------------------------------

class UserRecord(Base):
    """Platform user — credentials stored with bcrypt hash, no plaintext passwords."""

    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_id)
    username: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    email: Mapped[str | None] = mapped_column(String(200), unique=True)
    hashed_password: Mapped[str] = mapped_column(Text, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


# ---------------------------------------------------------------------------
# Agent / KIO Registry (replaces AgentRecord from ai4sweng-kio1)
# ---------------------------------------------------------------------------

class AgentRecord(Base):
    """Registered KIO agent metadata and heartbeat tracking."""

    __tablename__ = "agents"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_id)
    kio_id: Mapped[str] = mapped_column(String(32), unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    role: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    capabilities: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(32), default="ACTIVE")
    available: Mapped[bool] = mapped_column(Boolean, default=True)
    port: Mapped[int | None] = mapped_column(Integer)
    last_heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


# ---------------------------------------------------------------------------
# KIO Capability Registry
# ---------------------------------------------------------------------------

class KIOCapabilityRecord(Base):
    """Persisted KIO capability — queried by LLM planner for dynamic KIO selection."""

    __tablename__ = "kio_capabilities"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_id)
    kio_id: Mapped[str] = mapped_column(String(32), unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    input_artifact_type: Mapped[str | None] = mapped_column(String(64))
    output_artifact_type: Mapped[str] = mapped_column(String(64), nullable=False)
    version: Mapped[str] = mapped_column(String(32), default="1.0.0")
    tags: Mapped[list[str]] = mapped_column(JSON, default=list)
    available: Mapped[bool] = mapped_column(Boolean, default=True)
    port: Mapped[int | None] = mapped_column(Integer)
    registered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
