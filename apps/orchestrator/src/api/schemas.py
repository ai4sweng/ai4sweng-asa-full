"""Orchestrator API schemas."""
from __future__ import annotations

import re
import uuid
from typing import Any

from pydantic import BaseModel, Field, field_validator

_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)


class RunWorkflowRequest(BaseModel):
    workflow_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    kio_sequence: list[str] = Field(
        default_factory=list,
        description="KIO IDs to execute in order. Empty = auto-plan via LM Engine.",
    )
    hitl_after: list[str] | None = Field(
        default=None,
        description="Insert HITL checkpoint after these KIOs (overrides KIO-driven HITL).",
    )
    description: str = ""
    working_directory: str = ""
    timeout_seconds: int | None = Field(
        default=None,
        description="Per-workflow deadline in seconds (Slide 8). None = use global timeout.",
        gt=0,
    )

    @field_validator("workflow_id", mode="after")
    @classmethod
    def _validate_workflow_id(cls, v: str) -> str:
        if not _UUID_RE.match(v.lower()):
            raise ValueError("workflow_id must be a valid UUID v4")
        return v


class PromptWorkflowRequest(BaseModel):
    prompt: str = Field(description="Natural language instruction (e.g. 'bu kodda bug bul')")
    code: str | None = Field(default=None, description="Optional code snippet to analyse")
    context: dict[str, Any] = Field(default_factory=dict, description="Extra key/value context")
    working_directory: str = ""


class WorkflowStatusResponse(BaseModel):
    session_id: str
    workflow_id: str
    status: str
    progress_current: int
    progress_total: int
    active_kio: str | None
    pending_checkpoint_id: str | None
    artifacts: list[dict[str, Any]] = Field(default_factory=list)
    log: list[str] = Field(default_factory=list)


class ApproveRequest(BaseModel):
    actor: str = "human_operator"
    feedback: str = ""


class ApproveResponse(BaseModel):
    session_id: str
    checkpoint_id: str
    status: str
    message: str
