"""Orchestrator workflow API — run, status, approve, SSE stream.

All /workflow/* endpoints require a valid Bearer JWT (issued by POST /auth/login).
The SSE stream (/workflow/events) is also protected so only authenticated
clients receive live updates.
"""
from __future__ import annotations

from typing import AsyncGenerator

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse

from shared.auth.dependencies import get_current_user
from shared.auth.schemas import UserInfo

from ..engine.event_bus import get_event_bus
from ..engine.workflow_runner import get_runner
from ..services.session_client import get_session_client
from .schemas import (
    ApproveRequest,
    ApproveResponse,
    PromptWorkflowRequest,
    RunWorkflowRequest,
    WorkflowStatusResponse,
)

router = APIRouter(
    prefix="/workflow",
    tags=["workflow"],
)


@router.post("/run", status_code=202)
async def run_workflow(
    req: RunWorkflowRequest,
    current_user: UserInfo = Depends(get_current_user),
):
    """Accept a workflow run request and start async LangGraph execution.

    Returns 202 immediately; connect to GET /workflow/events for live SSE
    updates or poll GET /workflow/{session_id}/status for progress.
    The authenticated user's username is stamped as the session owner.
    """
    runner = get_runner()
    session_id = await runner.run(
        workflow_id=req.workflow_id,
        kio_sequence=req.kio_sequence,
        hitl_after=req.hitl_after,
        owner=current_user.username,
        description=req.description,
        working_directory=req.working_directory,
    )
    return {
        "session_id": session_id,
        "workflow_id": req.workflow_id,
        "status": "ACCEPTED",
        "message": "Workflow started. Connect to /workflow/events for live updates.",
    }


@router.post("/prompt", status_code=202)
async def run_prompt_workflow(
    req: PromptWorkflowRequest,
    current_user: UserInfo = Depends(get_current_user),
):
    """Start a pipeline from a natural-language prompt + optional code snippet.

    kio1 (Router) decides the KIO sequence based on the prompt.  The code (if
    any) is injected as ``initial_context`` so kio1 and downstream KIOs can
    analyse it without needing a repository on disk.

    Returns 202 immediately; poll GET /workflow/{session_id}/status for progress.
    """
    import uuid as _uuid
    runner = get_runner()
    initial_context = {
        "code": req.code or "",
        "prompt": req.prompt,
        **req.context,
    }
    session_id = await runner.run(
        workflow_id=str(_uuid.uuid4()),
        kio_sequence=["kio1"],          # kio1 will expand this dynamically
        hitl_after=None,                # kio1 sets hitl_after in its response
        owner=current_user.username,
        description=req.prompt,
        working_directory=req.working_directory,
        initial_context=initial_context,
    )
    return {
        "session_id": session_id,
        "status": "ACCEPTED",
        "message": "kio1 is routing your prompt. Poll /workflow/{session_id}/status for progress.",
    }


@router.get("/{session_id}/status", response_model=WorkflowStatusResponse)
async def get_workflow_status(
    session_id: str,
    _: UserInfo = Depends(get_current_user),
):
    """Return the current runtime state of a workflow session.

    Combines in-process LangGraph state (active KIO, progress counters) with
    artifact records fetched from Session Manager.
    """
    runner = get_runner()
    state = runner.get_state(session_id)
    if not state:
        raise HTTPException(status_code=404, detail=f"Session {session_id} not found")
    artifacts = await get_session_client().get_artifacts(session_id)
    return WorkflowStatusResponse(
        session_id=session_id,
        workflow_id=state["workflow_id"],
        status=state["status"],
        progress_current=state["progress"],
        progress_total=state["total"],
        active_kio=state["active_kio"],
        pending_checkpoint_id=state["pending_checkpoint_id"],
        artifacts=artifacts,
        log=state.get("log", [])[-30:],
    )


@router.post("/{session_id}/approve", response_model=ApproveResponse)
async def approve_hitl(session_id: str, req: ApproveRequest,
                       current_user: UserInfo = Depends(get_current_user)):
    """Resolve the pending HITL checkpoint and resume the LangGraph execution.

    Returns 409 if there is no outstanding checkpoint.  The graph is resumed
    via Command(resume=feedback) — no polling required.
    """
    runner = get_runner()
    state = runner.get_state(session_id)
    if not state:
        raise HTTPException(status_code=404, detail=f"Session {session_id} not found")
    checkpoint_id = state.get("pending_checkpoint_id")
    if not checkpoint_id:
        raise HTTPException(status_code=409, detail="No pending HITL checkpoint")
    await runner.approve(
        session_id,
        actor=current_user.username,
        feedback=req.feedback,
    )
    return ApproveResponse(
        session_id=session_id,
        checkpoint_id=checkpoint_id,
        status="APPROVED",
        message="Workflow resuming",
    )


@router.get("/events")
async def event_stream(token: str | None = None):
    """Server-Sent Events stream for real-time workflow progress.

    Accepts the JWT via ``?token=`` query param because the browser EventSource
    API cannot set Authorization headers.  Standard Bearer header also works
    (validated by decode_token which accepts any raw token string).
    Each connected client gets an isolated asyncio.Queue (max 256 events).
    """
    import jwt as _jwt
    from shared.auth.jwt_handler import decode_token
    from fastapi import HTTPException
    if not token:
        raise HTTPException(status_code=401, detail="Missing token — use ?token=<jwt>")
    try:
        decode_token(token)
    except _jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token has expired")
    except _jwt.PyJWTError:
        raise HTTPException(status_code=401, detail="Invalid token")

    bus = get_event_bus()

    async def _generate() -> AsyncGenerator[str, None]:
        yield 'data: {"event_type": "CONNECTED", "message": "Live stream active"}\n\n'
        async for event in bus.subscribe():
            yield event.to_sse()

    return StreamingResponse(
        _generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
