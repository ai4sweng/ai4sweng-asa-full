"""KIO client — JetStream primary transport with HTTP fallback.

Transport selection (per ``shared.config.Settings.use_nats``):

  NATS (default):
    Publishes JOB_REQUEST to JetStream stream ``KIO_JOBS`` on subject
    ``kio.{kio_id}.request``.  The KIO's durable consumer processes the
    message and replies on the ephemeral ``_kio_reply.{corr_id}`` subject.
    At-least-once delivery: the request is re-queued if the KIO restarts
    before acking.

  HTTP fallback (USE_NATS=false):
    POST /execute — same as Phase 2 behaviour.  Used for local dev without
    a running NATS server.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

import httpx
from loguru import logger

from shared.config import get_settings


class KioClient:
    """Async dispatcher for KIO shell execute endpoints."""

    def __init__(self) -> None:
        self._http_clients: dict[str, httpx.AsyncClient] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def execute(
        self,
        kio_id: str,
        session_id: str,
        workflow_id: str,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Dispatch a JOB_REQUEST to a KIO shell and return the JOB_RESULT envelope.

        Uses JetStream when ``use_nats=True`` (default), HTTP otherwise.
        """
        cfg = get_settings()
        envelope = self._build_envelope(kio_id, session_id, workflow_id, payload)

        if cfg.use_nats:
            try:
                return await self._execute_nats(kio_id, envelope, cfg)
            except Exception as exc:
                logger.warning(
                    "[{}] NATS execute failed ({}); falling back to HTTP", kio_id, exc
                )

        return await self._execute_http(kio_id, envelope, cfg)

    async def health(self, kio_id: str) -> dict[str, Any]:
        """Check the HTTP health endpoint of a KIO shell."""
        client = self._get_http_client(kio_id)
        resp = await client.get("/health/")
        resp.raise_for_status()
        return resp.json()

    async def close(self) -> None:
        """Close all underlying HTTP clients and the NATS connection."""
        for c in self._http_clients.values():
            await c.aclose()
        cfg = get_settings()
        if cfg.use_nats:
            try:
                from shared.messaging.jetstream import get_jetstream
                js = await get_jetstream()
                await js.close()
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Transport implementations
    # ------------------------------------------------------------------

    async def _execute_nats(
        self, kio_id: str, envelope: dict[str, Any], cfg
    ) -> dict[str, Any]:
        """Publish via JetStream and wait for direct reply."""
        from shared.messaging.jetstream import get_jetstream
        js = await get_jetstream()
        logger.info("→ JetStream kio.{}.request session={}", kio_id, envelope["session_id"][:8])
        result = await js.request_reply(
            kio_id, envelope, timeout=float(cfg.nats_request_timeout)
        )
        job_status = result.get("payload", {}).get("status", "DONE")
        logger.info("← [{}] JetStream reply: status={}", kio_id, job_status)
        return result

    async def _execute_http(
        self, kio_id: str, envelope: dict[str, Any], cfg
    ) -> dict[str, Any]:
        """POST /execute — HTTP fallback."""
        client = self._get_http_client(kio_id)
        logger.info("→ HTTP POST /execute kio={} session={}", kio_id, envelope["session_id"][:8])
        resp = await client.post("/execute", json=envelope)
        resp.raise_for_status()
        result = resp.json()
        job_status = result.get("payload", {}).get("status", "DONE")
        logger.info("← [{}] HTTP reply: status={}", kio_id, job_status)
        return result

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _build_envelope(
        kio_id: str,
        session_id: str,
        workflow_id: str,
        payload: dict[str, Any] | None,
    ) -> dict[str, Any]:
        cfg = get_settings()
        return {
            "message_id": str(uuid.uuid4()),
            "session_id": session_id,
            "workflow_id": workflow_id,
            "source": cfg.project_id,
            "target": kio_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "message_type": "JOB_REQUEST",
            "payload": payload or {},
        }

    def _get_http_client(self, kio_id: str) -> httpx.AsyncClient:
        if kio_id not in self._http_clients:
            cfg = get_settings()
            port = cfg.kio_port_map.get(kio_id, 8000)
            host = cfg.kio_base_host or kio_id  # empty string → use kio_id as Docker DNS name
            self._http_clients[kio_id] = httpx.AsyncClient(
                base_url=f"http://{host}:{port}",
                timeout=float(cfg.kio_client_timeout),
            )
        return self._http_clients[kio_id]


# ------------------------------------------------------------------
# Singleton
# ------------------------------------------------------------------

_client: KioClient | None = None


def get_kio_client() -> KioClient:
    """Return the process-wide KioClient singleton."""
    global _client
    if _client is None:
        _client = KioClient()
    return _client
