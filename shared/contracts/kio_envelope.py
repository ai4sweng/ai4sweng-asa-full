"""
Unified KIO Message Envelope — the common language for all inter-service communication.

Every NATS message uses this structure for routing, auditing, and replay.
Moved here from messaging/envelope.py so contracts are independent of transport.
"""

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field

from shared.config import get_settings
from shared.constants import PROTOCOL_VERSION, MessageType


class KIOEnvelope(BaseModel):
    """Required fields for all inter-KIO and orchestrator messages."""

    message_id: str
    correlation_id: str
    causation_id: str | None = None
    timestamp: str
    protocol_version: str
    project_id: str
    workflow_id: str | None = None
    session_id: str | None = None
    kio_id: str | None = None
    task_id: str | None = None
    sender: str
    recipient: str
    message_type: str
    subject: str
    priority: int = 5
    idempotency_key: str
    trace_id: str | None = None
    span_id: str | None = None
    parent_span_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump()


# Keep MessageEnvelope as alias for backward compatibility during migration
MessageEnvelope = KIOEnvelope


def build_envelope(
    *,
    subject: str,
    message_type: MessageType | str,
    sender: str,
    recipient: str,
    correlation_id: str,
    idempotency_key: str | None = None,
    workflow_id: str | None = None,
    session_id: str | None = None,
    kio_id: str | None = None,
    task_id: str | None = None,
    causation_id: str | None = None,
    trace_id: str | None = None,
    span_id: str | None = None,
    parent_span_id: str | None = None,
    priority: int = 5,
    payload: dict[str, Any] | None = None,
) -> KIOEnvelope:
    """
    Factory for KIO envelopes with consistent idempotency and tracing fields.

    Idempotency keys default to message_id for at-least-once delivery safety.
    """
    message_id = str(uuid4())
    settings = get_settings()
    msg_type = message_type.value if isinstance(message_type, MessageType) else message_type

    return KIOEnvelope(
        message_id=message_id,
        correlation_id=correlation_id,
        causation_id=causation_id,
        timestamp=datetime.now(timezone.utc).isoformat(),
        protocol_version=PROTOCOL_VERSION,
        project_id=settings.project_id,
        workflow_id=workflow_id,
        session_id=session_id,
        kio_id=kio_id,
        task_id=task_id,
        sender=sender,
        recipient=recipient,
        message_type=msg_type,
        subject=subject,
        priority=priority,
        idempotency_key=idempotency_key or message_id,
        trace_id=trace_id,
        span_id=span_id,
        parent_span_id=parent_span_id,
        payload=payload or {},
    )
