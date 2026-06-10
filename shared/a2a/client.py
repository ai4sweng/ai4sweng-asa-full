"""A2AClient — direct KIO-to-KIO invocation.

A KIO shell imports this client to call a peer KIO directly (A2A = Agent to
Agent) without routing through the orchestrator's LangGraph workflow loop.

Transport priority:
  1. NATS JetStream (durable, at-least-once) when ``use_nats=True``
  2. HTTP POST /execute (fallback for local dev without NATS)

Design note
-----------
A2A calls share the same session_id and workflow_id as the parent task, so
all artifacts they produce are visible to the orchestrator and Session Manager
without any special plumbing.  The calling KIO is responsible for passing the
result back to the orchestrator as part of its own artifact_data.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

import httpx
from loguru import logger

from shared.config import get_settings


class A2AClient:
    """Invoke peer KIO agents directly, bypassing the orchestrator workflow loop."""

    def __init__(self) -> None:
        self._http: dict[str, httpx.AsyncClient] = {}

    async def invoke(
        self,
        target_kio: str,
        *,
        session_id: str,
        workflow_id: str,
        caller_kio: str,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Dispatch a JOB_REQUEST to *target_kio* and return the JOB_RESULT envelope.

        Parameters
        ----------
        target_kio:  Destination KIO id (e.g. ``"kio5"``).
        session_id:  Parent session — artifacts will be attributed to it.
        workflow_id: Parent workflow.
        caller_kio:  The calling KIO id — used as the ``source`` field.
        payload:     Arbitrary dict forwarded to the target KIO's handler.
        """
        cfg = get_settings()
        envelope = {
            "message_id": str(uuid.uuid4()),
            "session_id": session_id,
            "workflow_id": workflow_id,
            "source": caller_kio,
            "target": target_kio,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "message_type": "JOB_REQUEST",
            "payload": payload or {},
        }

        if cfg.use_nats:
            try:
                return await self._invoke_nats(target_kio, envelope, cfg)
            except Exception as exc:
                logger.warning(
                    "[a2a] NATS invoke {} failed ({}); falling back to HTTP", target_kio, exc
                )

        return await self._invoke_http(target_kio, envelope, cfg)

    # ------------------------------------------------------------------
    # Transports
    # ------------------------------------------------------------------

    async def _invoke_nats(self, target_kio: str, envelope: dict, cfg) -> dict[str, Any]:
        from shared.messaging.jetstream import get_jetstream

        js = await get_jetstream()
        logger.info(
            "[a2a] → JetStream {}.request session={}", target_kio, envelope["session_id"][:8]
        )
        result = await js.request_reply(
            target_kio, envelope, timeout=float(cfg.nats_request_timeout)
        )
        logger.info(
            "[a2a] ← {} replied: status={}", target_kio, result.get("payload", {}).get("status")
        )
        return result

    async def _invoke_http(self, target_kio: str, envelope: dict, cfg) -> dict[str, Any]:
        client = self._get_http_client(target_kio, cfg)
        logger.info("[a2a] → HTTP /execute {}", target_kio)
        resp = await client.post("/execute", json=envelope)
        resp.raise_for_status()
        return resp.json()

    def _get_http_client(self, kio_id: str, cfg) -> httpx.AsyncClient:
        if kio_id not in self._http:
            port = cfg.kio_port_map.get(kio_id, 8000)
            # Empty KIO_BASE_HOST → use kio_id as Docker DNS hostname (e.g. "kio12:8022").
            # This mirrors how the orchestrator's KioClient resolves peer KIOs.
            host = cfg.kio_base_host or kio_id
            self._http[kio_id] = httpx.AsyncClient(
                base_url=f"http://{host}:{port}",
                timeout=float(cfg.kio_client_timeout),
            )
        return self._http[kio_id]

    async def close(self) -> None:
        for c in self._http.values():
            await c.aclose()


# ------------------------------------------------------------------
# Singleton
# ------------------------------------------------------------------

_client: A2AClient | None = None


def get_a2a_client() -> A2AClient:
    """Return the process-wide A2AClient singleton."""
    global _client
    if _client is None:
        _client = A2AClient()
    return _client
