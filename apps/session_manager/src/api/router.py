"""Session Manager API routes."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from ..service.session_service import get_session_service
from .schemas import (
    ApproveCheckpointRequest,
    ArtifactContentResponse,
    ArtifactResponse,
    BatchArtifactRequest,
    CheckpointResponse,
    CreateCheckpointRequest,
    CreateSessionRequest,
    RegisterArtifactRequest,
    SessionResponse,
    UpdateStatusRequest,
)

router = APIRouter(prefix="/sessions", tags=["sessions"])


@router.post("/", response_model=SessionResponse, status_code=201)
async def create_session(req: CreateSessionRequest):
    svc = get_session_service()
    result = await svc.create_session(
        workflow_id=req.workflow_id,
        owner=req.owner,
        scope=req.scope,
        metadata=req.metadata,
    )
    return result


@router.get("/", response_model=list[SessionResponse])
async def list_sessions(owner: str | None = None):
    svc = get_session_service()
    return await svc.list_sessions(owner=owner)


@router.get("/{session_id}", response_model=SessionResponse)
async def get_session(session_id: str):
    svc = get_session_service()
    result = await svc.get_session(session_id)
    if not result:
        raise HTTPException(status_code=404, detail=f"Session {session_id} not found")
    return result


@router.put("/{session_id}/status", response_model=SessionResponse)
async def update_status(session_id: str, req: UpdateStatusRequest):
    svc = get_session_service()
    result = await svc.update_status(session_id, req.status, req.metadata)
    if not result:
        raise HTTPException(status_code=404, detail=f"Session {session_id} not found")
    return result


@router.post("/{session_id}/artifacts", response_model=ArtifactResponse, status_code=201)
async def register_artifact(session_id: str, req: RegisterArtifactRequest):
    svc = get_session_service()
    return await svc.register_artifact(
        session_id,
        artifact_id=req.artifact_id,
        producer_kio=req.producer_kio,
        artifact_type=req.artifact_type,
        artifact_data=req.artifact_data,
        workflow_stage=req.workflow_stage,
        state=req.state,
        parent_artifact_id=req.parent_artifact_id,
    )


@router.get("/{session_id}/artifacts", response_model=list[ArtifactResponse])
async def get_artifacts(session_id: str):
    svc = get_session_service()
    return await svc.get_artifacts(session_id)


@router.get("/{session_id}/artifacts/{artifact_id}", response_model=ArtifactResponse)
async def get_artifact(session_id: str, artifact_id: str):
    svc = get_session_service()
    result = await svc.get_artifact(artifact_id)
    if not result or result.get("session_id") != session_id:
        raise HTTPException(status_code=404, detail=f"Artifact {artifact_id} not found")
    return result


@router.get(
    "/{session_id}/artifacts/{artifact_id}/content",
    response_model=ArtifactContentResponse,
    summary="Fetch artifact content (for KIO-to-KIO reads)",
)
async def get_artifact_content(session_id: str, artifact_id: str):
    """Return only the artifact_data payload — resolves from S3 when offloaded.

    KIO handlers use this to pull upstream artifacts by ID without needing
    DB credentials. Resolves S3-offloaded payloads transparently.
    """
    svc = get_session_service()
    result = await svc.get_artifact_content(session_id, artifact_id)
    if not result:
        raise HTTPException(status_code=404, detail=f"Artifact {artifact_id} not found")
    return result


@router.post(
    "/{session_id}/artifacts/batch",
    response_model=list[ArtifactContentResponse],
    summary="Batch-fetch artifact content (for KIO-to-KIO reads)",
)
async def get_artifacts_batch(session_id: str, req: BatchArtifactRequest):
    """Fetch multiple artifact payloads in one request.

    KIO handlers pass ``input_artifacts[].artifact_id`` from the envelope payload
    to retrieve all upstream outputs without N sequential HTTP calls.
    """
    svc = get_session_service()
    return await svc.get_artifacts_batch(session_id, req.artifact_ids)


@router.post("/{session_id}/hitl", response_model=CheckpointResponse, status_code=201)
async def create_hitl_checkpoint(session_id: str, req: CreateCheckpointRequest):
    svc = get_session_service()
    return await svc.create_hitl_checkpoint(
        session_id,
        workflow_step=req.workflow_step,
        artifact_id=req.artifact_id,
        requested_by=req.requested_by,
    )


@router.put("/{session_id}/hitl/{checkpoint_id}", response_model=CheckpointResponse)
async def resolve_checkpoint(session_id: str, checkpoint_id: str, req: ApproveCheckpointRequest):
    svc = get_session_service()
    result = await svc.resolve_checkpoint(
        session_id,
        checkpoint_id,
        action=req.action,
        actor=req.actor,
        feedback=req.feedback,
    )
    if not result:
        raise HTTPException(status_code=404, detail=f"Checkpoint {checkpoint_id} not found")
    return result
