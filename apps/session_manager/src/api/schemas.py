"""Request/response schemas for the Session Manager API."""
from __future__ import annotations

from typing import Any
from pydantic import BaseModel, Field
import uuid


class CreateSessionRequest(BaseModel):
    workflow_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    owner: str = "demo_user"
    scope: str = "workflow"
    metadata: dict[str, Any] = Field(default_factory=dict)


class SessionResponse(BaseModel):
    session_id: str
    workflow_id: str
    status: str
    owner: str | None
    metadata: dict[str, Any] = Field(default_factory=dict)


class UpdateStatusRequest(BaseModel):
    status: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class RegisterArtifactRequest(BaseModel):
    artifact_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    producer_kio: str
    consumer_kio: str | None = None
    workflow_stage: str = ""
    artifact_type: str = "json"
    artifact_data: dict[str, Any] = Field(default_factory=dict)
    state: str = "CREATED"


class ArtifactResponse(BaseModel):
    artifact_id: str
    session_id: str
    producer_kio: str
    artifact_type: str
    artifact_data: dict[str, Any]
    state: str


class CreateCheckpointRequest(BaseModel):
    workflow_step: str
    artifact_id: str | None = None
    requested_by: str = "kio1"
    review_schema: dict[str, Any] = Field(default_factory=dict)


class CheckpointResponse(BaseModel):
    checkpoint_id: str
    session_id: str
    workflow_step: str = ""
    state: str
    decided_by: str | None = None
    feedback: str | None = None
    artifact_id: str | None = None


class ApproveCheckpointRequest(BaseModel):
    action: str = "APPROVED"
    actor: str = "human_operator"
    feedback: str = ""
