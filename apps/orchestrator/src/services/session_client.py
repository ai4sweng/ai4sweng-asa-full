"""HTTP client for the Session Manager service.

All mutating calls (register_artifact, update_status, create_hitl_checkpoint,
resolve_checkpoint) use exponential-backoff retry for transient 5xx / network
errors.  A single blip in Session Manager no longer kills the workflow.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

import httpx
from loguru import logger

from shared.config import get_settings

_MAX_RETRIES = 3
_RETRY_BASE_DELAY = 0.5  # seconds; doubles each attempt


async def _with_retry(coro_fn, *args, label: str = "", **kwargs) -> Any:
    """Call ``coro_fn(*args, **kwargs)`` with exponential back-off on 5xx / network errors."""
    last_exc: Exception | None = None
    for attempt in range(_MAX_RETRIES):
        try:
            return await coro_fn(*args, **kwargs)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code < 500 or attempt == _MAX_RETRIES - 1:
                raise
            last_exc = exc
        except (httpx.ConnectError, httpx.ReadTimeout, httpx.RemoteProtocolError) as exc:
            if attempt == _MAX_RETRIES - 1:
                raise
            last_exc = exc
        delay = _RETRY_BASE_DELAY * (2**attempt)
        logger.warning(
            "[session_client] {} failed (attempt {}/{}): {} — retrying in {:.1f}s",
            label,
            attempt + 1,
            _MAX_RETRIES,
            last_exc,
            delay,
        )
        await asyncio.sleep(delay)
    raise RuntimeError("unreachable")  # pragma: no cover


class SessionClient:
    """Async HTTP client wrapping the Session Manager REST API."""

    def __init__(self) -> None:
        cfg = get_settings()
        self._client = httpx.AsyncClient(
            base_url=cfg.session_manager_url,
            timeout=float(cfg.session_manager_client_timeout),
        )

    # ------------------------------------------------------------------
    # Session lifecycle
    # ------------------------------------------------------------------

    async def create_session(
        self, owner: str = "orchestrator", workflow_id: str | None = None
    ) -> dict[str, Any]:
        resp = await _with_retry(
            self._client.post,
            "/sessions/",
            json={"workflow_id": workflow_id or str(uuid.uuid4()), "owner": owner},
            label="create_session",
        )
        resp.raise_for_status()
        return resp.json()

    async def list_sessions(self) -> list[dict[str, Any]]:
        resp = await self._client.get("/sessions/")
        resp.raise_for_status()
        return resp.json()

    async def get_session(self, session_id: str) -> dict[str, Any]:
        resp = await self._client.get(f"/sessions/{session_id}")
        resp.raise_for_status()
        return resp.json()

    async def update_status(
        self, session_id: str, status: str, metadata: dict | None = None
    ) -> dict[str, Any]:
        resp = await _with_retry(
            self._client.put,
            f"/sessions/{session_id}/status",
            json={"status": status, "metadata": metadata or {}},
            label=f"update_status({session_id[:8]}, {status})",
        )
        resp.raise_for_status()
        return resp.json()

    async def update_progress(self, session_id: str, progress: dict[str, Any]) -> None:
        resp = await _with_retry(
            self._client.put,
            f"/sessions/{session_id}/status",
            json={"status": "ACTIVE", "metadata": {"progress": progress}},
            label=f"update_progress({session_id[:8]})",
        )
        resp.raise_for_status()

    # ------------------------------------------------------------------
    # Artifacts
    # ------------------------------------------------------------------

    async def register_artifact(self, session_id: str, artifact: dict[str, Any]) -> dict[str, Any]:
        # parent_artifact_id is passed through as-is if present in the artifact dict
        resp = await _with_retry(
            self._client.post,
            f"/sessions/{session_id}/artifacts",
            json=artifact,
            label=f"register_artifact({session_id[:8]})",
        )
        resp.raise_for_status()
        logger.debug("Artifact registered for session {}", session_id)
        return resp.json()

    async def get_artifacts(self, session_id: str) -> list[dict[str, Any]]:
        resp = await self._client.get(f"/sessions/{session_id}/artifacts")
        resp.raise_for_status()
        return resp.json()

    async def get_artifact(self, session_id: str, artifact_id: str) -> dict[str, Any] | None:
        try:
            resp = await self._client.get(f"/sessions/{session_id}/artifacts/{artifact_id}")
            resp.raise_for_status()
            return resp.json()
        except Exception:
            return None

    # ------------------------------------------------------------------
    # HITL
    # ------------------------------------------------------------------

    async def create_hitl_checkpoint(
        self, session_id: str, step: str, artifact_id: str | None = None
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "workflow_step": step,
            "requested_by": get_settings().project_id,
        }
        if artifact_id:
            payload["artifact_id"] = artifact_id
        resp = await _with_retry(
            self._client.post,
            f"/sessions/{session_id}/hitl",
            json=payload,
            label=f"create_hitl_checkpoint({session_id[:8]}, {step})",
        )
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
        resp = await _with_retry(
            self._client.put,
            f"/sessions/{session_id}/hitl/{checkpoint_id}",
            json={"action": action, "actor": actor, "feedback": feedback},
            label=f"resolve_checkpoint({checkpoint_id[:8]})",
        )
        resp.raise_for_status()
        return resp.json()

    async def close(self) -> None:
        await self._client.aclose()


_client: SessionClient | None = None


def get_session_client() -> SessionClient:
    """Return the process-wide SessionClient singleton."""
    global _client
    if _client is None:
        _client = SessionClient()
    return _client
