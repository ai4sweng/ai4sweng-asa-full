"""Provenance / artifact lineage API — Slide 17 of the interface spec.

Endpoints
---------
GET /workflow/{session_id}/artifacts
    List all artifacts for a session in chronological order (root first).
    Includes parent_artifact_id and created_at fields for client-side tree rendering.

GET /workflow/{session_id}/artifacts/{artifact_id}/lineage
    Walk the parent chain from artifact_id to the root and return the full chain.
    Response is ordered root → artifact_id (oldest first).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from shared.auth.dependencies import get_current_user
from shared.auth.schemas import UserInfo

import re as _re

from ..engine.provenance_manager import get_provenance_manager
from ..engine.workflow_runner import get_runner

router = APIRouter(prefix="/workflow", tags=["provenance"])

_UUID_RE = _re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


def _validate_uuid(value: str, field: str) -> str:
    if value and not _UUID_RE.match(value.lower()):
        raise HTTPException(status_code=422, detail=f"{field} must be a valid UUID v4")
    return value


def _check_session_ownership(session_id: str, current_user: UserInfo) -> None:
    """Raise 403 if the session doesn't belong to this user."""
    try:
        runner = get_runner()
        state = runner.get_state(session_id)
        if state and state.get("owner") and state["owner"] != current_user.username:
            raise HTTPException(status_code=403, detail="Access denied to this session")
    except RuntimeError:
        pass  # runner not yet initialised — skip ownership check during startup


@router.get("/{session_id}/artifacts")
async def list_session_artifacts(
    session_id: str,
    current_user: UserInfo = Depends(get_current_user),
):
    """List all artifacts for a session in chronological order.

    Each artifact includes ``parent_artifact_id`` so the client can render
    the full dependency tree.  Ordered root → leaf (oldest first).
    """
    _validate_uuid(session_id, "session_id")
    _check_session_ownership(session_id, current_user)
    pm = get_provenance_manager()
    artifacts = await pm.get_full_lineage(session_id)
    return {
        "session_id": session_id,
        "artifact_count": len(artifacts),
        "artifacts": artifacts,
    }


@router.get("/{session_id}/artifacts/{artifact_id}/lineage")
async def get_artifact_lineage(
    session_id: str,
    artifact_id: str,
    current_user: UserInfo = Depends(get_current_user),
):
    """Return the full provenance chain from root to the requested artifact.

    Response is a list ordered oldest (root) → newest (artifact_id).
    Each entry includes producer_kio, artifact_type, parent_artifact_id,
    and created_at so the caller can display the full execution trail.
    """
    _validate_uuid(session_id, "session_id")
    _validate_uuid(artifact_id, "artifact_id")
    _check_session_ownership(session_id, current_user)

    pm = get_provenance_manager()
    chain = await pm.get_lineage(session_id, artifact_id)
    if not chain:
        raise HTTPException(
            status_code=404,
            detail=f"Artifact {artifact_id} not found in session {session_id}",
        )
    return {
        "session_id": session_id,
        "artifact_id": artifact_id,
        "depth": len(chain),
        "lineage": chain,
    }
