"""Orchestrator API schemas."""
from __future__ import annotations

from typing import Any
from pydantic import BaseModel, Field
import uuid


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
    owner: str = "demo_user"
    description: str = ""
    working_directory: str = ""


class PromptWorkflowRequest(BaseModel):
    prompt: str = Field(description="Natural language instruction (e.g. 'bu kodda bug bul')")
    code: str | None = Field(default=None, description="Optional code snippet to analyse")
    context: dict[str, Any] = Field(default_factory=dict, description="Extra key/value context")
    owner: str = "demo_user"
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
