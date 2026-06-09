"""HTTP client for the Session Manager service."""
from __future__ import annotations

import uuid
from typing import Any

import httpx
from loguru import logger

from shared.config import get_settings


class SessionClient:
    """Async HTTP client wrapping the Session Manager REST API."""

    def __init__(self) -> None:
        cfg = get_settings()
        self._client = httpx.AsyncClient(
            base_url=cfg.session_manager_url,
            timeout=float(cfg.session_manager_client_timeout),
        )

    async def create_session(
        self, owner: str = "orchestrator", workflow_id: str | None = None
    ) -> dict[str, Any]:
        """Create a new session in the Session Manager."""
        resp = await self._client.post(
            "/sessions/",
            json={"workflow_id": workflow_id or str(uuid.uuid4()), "owner": owner},
        )
        resp.raise_for_status()
        return resp.json()

    async def list_sessions(self) -> list[dict[str, Any]]:
        """Return all sessions known to Session Manager."""
        resp = await self._client.get("/sessions/")
        resp.raise_for_status()
        return resp.json()

    async def get_session(self, session_id: str) -> dict[str, Any]:
        """Fetch session state (status, owner, metadata)."""
        resp = await self._client.get(f"/sessions/{session_id}")
        resp.raise_for_status()
        return resp.json()

    async def update_status(
        self, session_id: str, status: str, metadata: dict | None = None
    ) -> dict[str, Any]:
        """Update the session state (ACTIVE, COMPLETED, FAILED, PENDING_REVIEW)."""
        resp = await self._client.put(
            f"/sessions/{session_id}/status",
            json={"status": status, "metadata": metadata or {}},
        )
        resp.raise_for_status()
        return resp.json()

    async def update_progress(self, session_id: str, progress: dict[str, Any]) -> None:
        """Persist KIO step progress into session metadata."""
        resp = await self._client.put(
            f"/sessions/{session_id}/status",
            json={"status": "ACTIVE", "metadata": {"progress": progress}},
        )
        resp.raise_for_status()

    async def register_artifact(
        self, session_id: str, artifact: dict[str, Any]
    ) -> dict[str, Any]:
        """Register an artifact produced by a KIO step."""
        resp = await self._client.post(f"/sessions/{session_id}/artifacts", json=artifact)
        resp.raise_for_status()
        logger.debug("Artifact registered for session {}", session_id)
        return resp.json()

    async def get_artifacts(self, session_id: str) -> list[dict[str, Any]]:
        """Return all artifacts registered for the session."""
        resp = await self._client.get(f"/sessions/{session_id}/artifacts")
        resp.raise_for_status()
        return resp.json()

    async def create_hitl_checkpoint(
        self, session_id: str, step: str, artifact_id: str | None = None
    ) -> dict[str, Any]:
        """Create a HITL checkpoint — pauses the workflow until approved."""
        payload: dict[str, Any] = {
            "workflow_step": step,
            "requested_by": get_settings().project_id,
        }
        if artifact_id:
            payload["artifact_id"] = artifact_id
        resp = await self._client.post(f"/sessions/{session_id}/hitl", json=payload)
        resp.raise_for_status()
        logger.warning("HITL checkpoint created for session {} at '{}'", session_id, step)
        return resp.json()

    async def resolve_checkpoint(
        self,
        session_id: str,
        checkpoint_id: str,
        action: str = "APPROVED",
        actor: str = "human_operator",
        feedback: str = "",
    ) -> dict[str, Any]:
        """Resolve a HITL checkpoint (APPROVED or REJECTED)."""
        resp = await self._client.put(
            f"/sessions/{session_id}/hitl/{checkpoint_id}",
            json={"action": action, "actor": actor, "feedback": feedback},
        )
        resp.raise_for_status()
        return resp.json()

    async def close(self) -> None:
        """Close the underlying httpx client."""
        await self._client.aclose()


_client: SessionClient | None = None


def get_session_client() -> SessionClient:
    """Return the process-wide SessionClient singleton."""
    global _client
    if _client is None:
        _client = SessionClient()
    return _client
