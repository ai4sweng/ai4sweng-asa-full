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
        # Tracks (host, port) used for each cached client so we can bust it when the registry updates
        self._endpoint_keys: dict[str, tuple[str, int]] = {}

        # Register a cache-bust callback with the AgentRegistry
        try:
            from ..engine.agent_registry import get_agent_registry
            get_agent_registry().on_endpoint_change(self._invalidate_client)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def execute(
        self,
        kio_id: str,
        session_id: str,
        workflow_id: str,
        payload: dict[str, Any] | None = None,
        *,
        correlation_id: str = "",
        step_id: str = "",
    ) -> dict[str, Any]:
        """Dispatch a JOB_REQUEST to a KIO shell and return the JOB_RESULT envelope.

        Uses JetStream when ``use_nats=True`` (default), HTTP otherwise.
        """
        cfg = get_settings()
        envelope = self._build_envelope(
            kio_id, session_id, workflow_id, payload,
            correlation_id=correlation_id, step_id=step_id,
        )

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
        *,
        correlation_id: str = "",
        step_id: str = "",
    ) -> dict[str, Any]:
        cfg = get_settings()
        return {
            "message_id": str(uuid.uuid4()),
            "correlation_id": correlation_id,
            "step_id": step_id,
            "protocol_version": "1.0.0",
            "project_id": cfg.project_id,   # Slide 7: explicit project_id field
            "session_id": session_id,
            "workflow_id": workflow_id,
            "source": cfg.project_id,
            "target": kio_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "message_type": "JOB_REQUEST",
            "payload": payload or {},
        }

    def _invalidate_client(self, kio_id: str) -> None:
        """Called by AgentRegistry when a KIO's endpoint changes."""
        old = self._http_clients.pop(kio_id, None)
        self._endpoint_keys.pop(kio_id, None)
        if old:
            import asyncio
            asyncio.create_task(old.aclose())
            logger.info("[kio_client] Invalidated cached client for {} (endpoint changed)", kio_id)

    def _get_http_client(self, kio_id: str) -> httpx.AsyncClient:
        cfg = get_settings()

        # 1. Dynamic registry — populated by CAPABILITY_ANNOUNCEMENT at KIO startup
        registry_endpoint: tuple[str, int] | None = None
        try:
            from ..engine.agent_registry import get_agent_registry
            registry_endpoint = get_agent_registry().get_endpoint(kio_id)
        except Exception:
            pass

        if registry_endpoint:
            host, port = registry_endpoint
        else:
            # 2. Static fallback — kio_port_map in shared/config.py
            port = cfg.kio_port_map.get(kio_id, 8000)
            host = cfg.kio_base_host or kio_id

        current_key = (host, port)
        if self._endpoint_keys.get(kio_id) != current_key and kio_id in self._http_clients:
            # Endpoint drifted (registry updated between cache creation and this call)
            self._invalidate_client(kio_id)

        if kio_id not in self._http_clients:
            logger.debug("[kio_client] Creating HTTP client for {} at {}:{}", kio_id, host, port)
            self._http_clients[kio_id] = httpx.AsyncClient(
                base_url=f"http://{host}:{port}",
                timeout=float(cfg.kio_client_timeout),
            )
            self._endpoint_keys[kio_id] = current_key

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
