"""
Task request/response contracts for KIO dispatch via NATS.
"""

from typing import Any
from pydantic import BaseModel, Field


class KIOTaskRequest(BaseModel):
    """Sent by orchestrator → KIO via NATS."""

    task_id: str
    workflow_id: str
    session_id: str | None = None
    kio_id: str
    correlation_id: str
    idempotency_key: str
    payload: dict[str, Any] = Field(default_factory=dict)
    upstream_artifact_ids: list[str] = Field(default_factory=list)
    trace_id: str | None = None
    span_id: str | None = None


class KIOTaskResult(BaseModel):
    """Sent by KIO → orchestrator via NATS reply."""

    task_id: str
    workflow_id: str
    session_id: str | None = None
    kio_id: str
    status: str  # COMPLETED | FAILED | COMPENSATING
    artifact_id: str | None = None
    artifact_type: str | None = None
    artifact_data: dict[str, Any] = Field(default_factory=dict)
    error_code: str | None = None
    error_message: str | None = None
    duration_ms: float = 0.0
    tokens_in: int = 0
    tokens_out: int = 0
