"""PostgreSQL-backed session service — replaces the in-memory dict from EnisAi4sweng."""
from __future__ import annotations

from typing import Any

from loguru import logger
from sqlalchemy.exc import IntegrityError

from shared.constants import TaskState, WorkflowState
from shared.persistence.session_provider import SessionProvider


class SessionService:
    """All state lives in PostgreSQL via shared.persistence — no in-memory dict."""

    def __init__(self, session_provider: SessionProvider) -> None:
        self._sp = session_provider

    # ------------------------------------------------------------------
    # Session lifecycle
    # ------------------------------------------------------------------

    async def create_session(
        self,
        *,
        workflow_id: str,
        owner: str = "demo_user",
        scope: str = "workflow",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Create a new WorkflowRecord and return it as a session dict."""
        async with self._sp.session_scope() as repo:
            workflow = await repo.create_workflow(
                name=scope,
                project_id=workflow_id,
                correlation_id=workflow_id,
                session_id=workflow_id,
                owner=owner,
                metadata={"scope": scope, **(metadata or {})},
            )
        logger.info("Session created: {}", workflow.id)
        return self._to_session_dict(workflow)

    async def get_session(self, session_id: str) -> dict[str, Any] | None:
        """Fetch a session by ID; returns None if not found."""
        async with self._sp.read_scope() as repo:
            workflow = await repo.get_workflow(session_id)
        if not workflow:
            return None
        return self._to_session_dict(workflow)

    async def list_sessions(self, owner: str | None = None) -> list[dict[str, Any]]:
        """Return all sessions, optionally filtered by owner."""
        async with self._sp.read_scope() as repo:
            workflows = await repo.list_workflows(owner=owner)
        return [self._to_session_dict(w) for w in workflows]

    async def update_status(
        self,
        session_id: str,
        status: str,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """Transition the session to a new status."""
        state_map = {
            "ACTIVE": WorkflowState.RUNNING,
            "COMPLETED": WorkflowState.COMPLETED,
            "FAILED": WorkflowState.FAILED,
            "TIMEOUT": WorkflowState.TIMEOUT,
            "CANCELLED": WorkflowState.CANCELLED,
            "PENDING_REVIEW": WorkflowState.WAITING_FOR_HUMAN_APPROVAL,
            "BLOCKED": WorkflowState.BLOCKED,
        }
        wf_state = state_map.get(status, WorkflowState.RUNNING)
        async with self._sp.session_scope() as repo:
            workflow = await repo.update_workflow_state(
                session_id,
                wf_state,
                started=wf_state == WorkflowState.RUNNING,
                completed=wf_state in (WorkflowState.COMPLETED, WorkflowState.FAILED),
            )
        if not workflow:
            return None
        logger.info("Session {} → {}", session_id, status)
        return self._to_session_dict(workflow)

    async def update_progress(self, session_id: str, progress: dict[str, Any]) -> None:
        """Persist workflow progress into metadata_ — committed by session_scope on exit."""
        async with self._sp.session_scope() as repo:
            workflow = await repo.get_workflow(session_id)
            if workflow:
                meta = dict(workflow.metadata_ or {})
                meta["progress"] = progress
                workflow.metadata_ = meta
                # No explicit flush needed — session_scope commits on clean exit.
        logger.debug("Progress updated for session {}", session_id)

    # ------------------------------------------------------------------
    # Artifacts
    # ------------------------------------------------------------------

    async def register_artifact(
        self,
        session_id: str,
        *,
        artifact_id: str,
        producer_kio: str,
        artifact_type: str,
        artifact_data: dict[str, Any],
        workflow_stage: str = "",
        state: str = "CREATED",
        parent_artifact_id: str | None = None,
    ) -> dict[str, Any]:
        """Persist a KIO-produced artifact and return its descriptor dict.

        parent_artifact_id is stored in the content JSON rather than the ORM FK
        column because the FK references the DB auto-generated PK, which is not
        aligned with the KIO-generated artifact_id yet.  Lineage is queryable via
        content['parent_artifact_id'] until IDs are unified.
        """
        content = {
            "data": artifact_data,
            "stage": workflow_stage,
            "state": state,
        }
        async with self._sp.session_scope() as repo:
            # 4.4: pass KIO artifact_id as the DB PK so FK lineage works
            artifact = await repo.create_artifact(
                workflow_id=session_id,
                artifact_type=artifact_type,
                content=content,
                artifact_id=artifact_id,
                kio_id=producer_kio,
                parent_artifact_id=parent_artifact_id,
            )
        return {
            "artifact_id": artifact.id,
            "session_id": session_id,
            "producer_kio": producer_kio,
            "artifact_type": artifact_type,
            "artifact_data": artifact_data,
            "state": state,
            "parent_artifact_id": parent_artifact_id,
        }

    async def get_artifacts(self, session_id: str) -> list[dict[str, Any]]:
        """Return all artifact descriptors registered for a session."""
        async with self._sp.read_scope() as repo:
            artifacts = await repo.list_artifacts_for_workflow(session_id)
        return [
            {
                "artifact_id": a.id,
                "session_id": session_id,
                "producer_kio": a.kio_id or "",
                "artifact_type": a.artifact_type,
                "artifact_data": (a.content or {}).get("data", {}),
                "state": (a.content or {}).get("state", "CREATED"),
                # 4.4: parent_artifact_id now stored in ORM FK column
                "parent_artifact_id": a.parent_artifact_id,
                "created_at": a.created_at.isoformat() if a.created_at else None,
            }
            for a in artifacts
        ]

    async def get_artifact(self, artifact_id: str) -> dict[str, Any] | None:
        """Fetch a single artifact by its ID (DB PK == KIO artifact_id after 4.4)."""
        async with self._sp.read_scope() as repo:
            a = await repo.get_artifact(artifact_id)
        if not a:
            return None
        return {
            "artifact_id": a.id,
            "session_id": a.workflow_id,
            "producer_kio": a.kio_id or "",
            "artifact_type": a.artifact_type,
            "artifact_data": (a.content or {}).get("data", {}),
            "state": (a.content or {}).get("state", "CREATED"),
            "parent_artifact_id": a.parent_artifact_id,
            "created_at": a.created_at.isoformat() if a.created_at else None,
        }

    # ------------------------------------------------------------------
    # HITL checkpoints
    # ------------------------------------------------------------------

    async def create_hitl_checkpoint(
        self,
        session_id: str,
        *,
        workflow_step: str,
        artifact_id: str | None = None,
        requested_by: str = "kio1",
    ) -> dict[str, Any]:
        """Create a HITL checkpoint and transition the session to PENDING_REVIEW.

        Idempotent: if a checkpoint already exists for this step (e.g. LangGraph
        re-enters hitl_node after resume), the existing record is returned.
        """
        idempotency_key = f"{session_id}:hitl:{workflow_step}"
        try:
            async with self._sp.session_scope() as repo:
                task = await repo.create_task(
                    workflow_id=session_id,
                    step_id=f"hitl_{workflow_step}",
                    agent_role="human_approval",
                    idempotency_key=idempotency_key,
                )
                prompt = f"Review required at step '{workflow_step}'."
                approval = await repo.create_approval_request(
                    workflow_id=session_id,
                    task_id=task.id,
                    prompt=prompt,
                    metadata={
                        "workflow_step": workflow_step,
                        "artifact_id": artifact_id,
                        "requested_by": requested_by,
                    },
                )
                await repo.update_workflow_state(session_id, WorkflowState.WAITING_FOR_HUMAN_APPROVAL)
            logger.warning("HITL checkpoint {} at step '{}'", approval.id, workflow_step)
            return {
                "checkpoint_id": approval.id,
                "session_id": session_id,
                "workflow_step": workflow_step,
                "state": "PENDING",
                "artifact_id": artifact_id,
            }
        except IntegrityError:
            # Duplicate idempotency key — hitl_node re-entered after LangGraph resume.
            # Fetch and return the existing checkpoint instead of failing.
            async with self._sp.read_scope() as repo:
                tasks = await repo.list_tasks_for_workflow(session_id)
                task = next((t for t in tasks if t.idempotency_key == idempotency_key), None)
                if task is None:
                    raise
                approvals = await repo.list_approvals_for_workflow(session_id)
                approval = next((a for a in approvals if a.task_id == task.id), None)
                if approval is None:
                    raise
            logger.info("Reusing existing HITL checkpoint {} for step '{}'", approval.id, workflow_step)
            return {
                "checkpoint_id": approval.id,
                "session_id": session_id,
                "workflow_step": workflow_step,
                "state": "PENDING",
                "artifact_id": artifact_id,
            }

    async def resolve_checkpoint(
        self,
        session_id: str,
        checkpoint_id: str,
        *,
        action: str = "APPROVED",
        actor: str = "human_operator",
        feedback: str = "",
    ) -> dict[str, Any] | None:
        """Resolve a HITL checkpoint; APPROVED resumes the workflow, REJECTED marks it FAILED."""
        async with self._sp.session_scope() as repo:
            approval = await repo.resolve_approval(
                checkpoint_id,
                decision=action,
                feedback=feedback or None,
                decided_by=actor,
            )
            if not approval:
                return None
            new_state = (
                WorkflowState.RUNNING if action == "APPROVED" else WorkflowState.FAILED
            )
            await repo.update_workflow_state(session_id, new_state)
        logger.info("Checkpoint {} resolved → {} by {}", checkpoint_id, action, actor)
        return {
            "checkpoint_id": checkpoint_id,
            "session_id": session_id,
            "state": action,
            "decided_by": actor,
            "feedback": feedback,
        }

    async def get_checkpoint(self, checkpoint_id: str) -> dict[str, Any] | None:
        """Look up a HITL checkpoint by its approval ID (primary key lookup)."""
        async with self._sp.read_scope() as repo:
            approval = await repo.get_approval(checkpoint_id)
        if not approval:
            return None
        return {
            "checkpoint_id": approval.id,
            "state": approval.decision or "PENDING",
            "decided_by": approval.decided_by,
            "feedback": approval.feedback,
        }

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _to_session_dict(workflow) -> dict[str, Any]:
        state_map = {
            "RUNNING": "ACTIVE",
            "WAITING_FOR_HUMAN": "PENDING_REVIEW",
            "WAITING_FOR_HUMAN_APPROVAL": "PENDING_REVIEW",
            "COMPLETED": "COMPLETED",
            "FAILED": "FAILED",
            "TIMEOUT": "TIMEOUT",
            "CANCELLED": "CANCELLED",
            "BLOCKED": "BLOCKED",
            "CREATED": "ACTIVE",
        }
        return {
            "session_id": workflow.id,
            "workflow_id": workflow.id,
            "status": state_map.get(workflow.state, workflow.state),
            "owner": workflow.owner,
            "metadata": workflow.metadata_ or {},
        }


_service: SessionService | None = None


def get_session_service() -> SessionService:
    global _service
    if _service is None:
        _service = SessionService(SessionProvider())
    return _service
