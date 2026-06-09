"""
Shared KIO shell factory.

Every KIO shell calls make_kio_app() to get a FastAPI app with:
  GET  /health/  — liveness
  POST /execute  — JOB_REQUEST → JOB_RESULT  (HTTP, always available)
  NATS kio.{kio_id}.request → JOB_RESULT    (JetStream, when USE_NATS=true)

Transport selection
-------------------
  HTTP:  orchestrator calls POST /execute directly (default in dev without NATS)
  NATS:  orchestrator publishes to JetStream; KIO subscribes with durable consumer
         and replies on the ephemeral `_reply_to` subject embedded in the envelope.
"""
from __future__ import annotations

import asyncio
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

from fastapi import FastAPI
from loguru import logger
from pydantic import BaseModel


class MessageEnvelope(BaseModel):
    message_id: str
    correlation_id: str = ""      # ties retries of the same logical request together
    step_id: str = ""             # "step_{n}_{kio_id}" — position in the pipeline
    protocol_version: str = "1.0.0"
    project_id: str = ""          # platform project identifier (Slide 7)
    session_id: str
    workflow_id: str
    source: str
    target: str
    timestamp: str
    message_type: str
    payload: dict[str, Any] = {}


HandlerFn = Callable[[MessageEnvelope], Awaitable[dict[str, Any]]]

_HEARTBEAT_INTERVAL = 30   # seconds
_CAPABILITY_INTERVAL = 60  # seconds — re-announce so orchestrator registry stays fresh


async def publish_progress(
    kio_id: str,
    session_id: str,
    progress_pct: int,
    message: str,
    js=None,
) -> None:
    """Publish an intermediate TASK_STATUS message (Slide 9).

    KIO handlers call this optionally during long-running work to emit progress
    updates.  The orchestrator subscribes to ``kio.*.status`` and re-emits them
    as TASK_PROGRESS SSE events to connected clients.

    Example (inside a KIO handler)::

        from kio_base import publish_progress
        from shared.messaging.jetstream import get_jetstream

        async def handle(envelope):
            js = await get_jetstream()
            await publish_progress("kio5", envelope.session_id, 30, "Scanning...", js)
            ...analyse...
            await publish_progress("kio5", envelope.session_id, 80, "Building report...", js)
            return {"status": "DONE", ...}
    """
    status_msg = {
        "message_type": "TASK_STATUS",
        "agent_id": kio_id,
        "task_id": session_id,
        "status": "RUNNING",
        "progress": min(100, max(0, progress_pct)),
        "message": message,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    if js is not None:
        try:
            await js.publish(f"kio.{kio_id}.status", status_msg)
            logger.debug("[{}] TASK_STATUS {}% — {}", kio_id, progress_pct, message)
        except Exception as exc:
            logger.debug("[{}] Progress publish failed: {}", kio_id, exc)


def _make_error(error_code: str, message: str, *, retryable: bool) -> dict[str, Any]:
    """Return a structured error payload compatible with the KIO interface spec."""
    return {
        "status": "FAILED",
        "artifact_id": str(uuid.uuid4()),
        "artifact_data": {},
        "message": message,
        "error": {
            "error_code": error_code,
            "error_message": message,
            "retryable": retryable,
        },
    }


async def _heartbeat_loop(kio_id: str, js) -> None:
    """Publish a HEARTBEAT message every 30 s so the orchestrator can detect dead agents."""
    while True:
        await asyncio.sleep(_HEARTBEAT_INTERVAL)
        msg = {
            "message_type": "HEARTBEAT",
            "agent_id": kio_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "status": "HEALTHY",
        }
        if js is not None:
            try:
                await js.publish(f"kio.{kio_id}.heartbeat", msg)
            except Exception as exc:
                logger.debug("[{}] Heartbeat publish failed: {}", kio_id, exc)
        else:
            logger.debug("[{}] HEARTBEAT", kio_id)


async def _capability_loop(
    kio_id: str, title: str, port: int, supported_tasks: list, js
) -> None:
    """Publish CAPABILITY_ANNOUNCEMENT on startup then every 60 s.

    The orchestrator AgentRegistry subscribes to kio.*.capability and uses
    the announced host/port for HTTP transport, eliminating the need to
    hard-code new KIOs in kio_port_map.
    """
    import os
    kio_base_host = os.environ.get("KIO_BASE_HOST", "localhost")
    # Empty string in Docker → use service name (kio_id) as Docker DNS hostname
    host = kio_base_host if kio_base_host else kio_id

    announcement = {
        "message_type": "CAPABILITY_ANNOUNCEMENT",
        "agent_id": kio_id,
        "version": "0.1.0",
        "protocol_version": "1.0.0",
        "endpoint": {"host": host, "port": port},
        "supported_tasks": supported_tasks,
        "hardware_requirements": {"gpu": False, "memory_gb": 2},
    }

    while True:
        announcement["timestamp"] = datetime.now(timezone.utc).isoformat()
        if js is not None:
            try:
                await js.publish(f"kio.{kio_id}.capability", announcement)
                logger.debug("[{}] CAPABILITY_ANNOUNCEMENT → {}:{}", kio_id, host, port)
            except Exception as exc:
                logger.debug("[{}] Capability announce failed: {}", kio_id, exc)
        else:
            logger.debug("[{}] CAPABILITY_ANNOUNCEMENT (no NATS) {}:{}", kio_id, host, port)
        await asyncio.sleep(_CAPABILITY_INTERVAL)


def make_kio_app(
    kio_id: str,
    title: str,
    handler: HandlerFn,
    supported_tasks: list | None = None,
) -> FastAPI:
    """Build a FastAPI app for a KIO shell.

    handler(envelope) must return a dict that becomes the JOB_RESULT payload::

        {
          "status":       "DONE" | "REVIEW_REQUIRED",
          "artifact_id":  str,
          "artifact_data": dict,
          "message":       str,
          "hitl_question": str,   # only when status=REVIEW_REQUIRED
        }

    supported_tasks: list of task descriptors for the CAPABILITY_ANNOUNCEMENT.
    Defaults to a single entry derived from kio_id and title.

    If ``USE_NATS=true`` in the environment (default), the app also subscribes
    to ``kio.{kio_id}.request`` via JetStream on startup and publishes
    CAPABILITY_ANNOUNCEMENT messages so the orchestrator can discover it
    dynamically without needing a static port-map entry.
    """
    import os
    _tasks = supported_tasks or [
        {"task_type": kio_id, "description": title, "input_schema": "envelope_v1", "output_schema": "artifact_v1"}
    ]

    @asynccontextmanager
    async def _lifespan(app: FastAPI):
        from shared.config import get_settings
        cfg = get_settings()
        port = int(os.environ.get("KIO_PORT", "8000"))
        js = None
        if cfg.use_nats:
            try:
                from shared.messaging.jetstream import get_jetstream
                js = await get_jetstream()
                await js.subscribe_requests(kio_id, _make_nats_handler(kio_id, handler, js))
                logger.info("[{}] JetStream consumer active", kio_id)
            except Exception as exc:
                logger.warning("[{}] NATS unavailable — HTTP only ({})", kio_id, exc)

        hb_task = asyncio.create_task(_heartbeat_loop(kio_id, js))
        cap_task = asyncio.create_task(_capability_loop(kio_id, title, port, _tasks, js))
        yield

        for task in (hb_task, cap_task):
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        # JetStream subscriptions are cleaned up by the manager's drain() on process exit.

    app = FastAPI(title=f"{kio_id.upper()} — {title}", version="0.1.0", lifespan=_lifespan)

    @app.get("/health/")
    async def health():
        return {"status": "ok", "service": kio_id, "title": title}

    @app.post("/execute")
    async def execute(envelope: MessageEnvelope) -> MessageEnvelope:
        """HTTP execution endpoint — always available for direct testing."""
        from shared.config import get_settings as _cfg
        logger.info("[{}] JOB_REQUEST (HTTP) from {} session={}",
                    kio_id, envelope.source, envelope.session_id)
        cfg_timeout = float(_cfg().kio_client_timeout) - 10  # headroom for reply
        # Per-task timeout_seconds from envelope payload takes precedence (Slide 8)
        task_timeout = envelope.payload.get("timeout_seconds")
        timeout_s = float(task_timeout) if task_timeout else cfg_timeout
        try:
            result_payload = await asyncio.wait_for(handler(envelope), timeout=timeout_s)
        except asyncio.TimeoutError:
            logger.error("[{}] handler timed out after {}s", kio_id, timeout_s)
            result_payload = _make_error(
                "HANDLER_TIMEOUT",
                f"{kio_id} handler timed out after {timeout_s:.0f}s",
                retryable=True,
            )
        except Exception as exc:
            logger.exception("[{}] execution failed: {}", kio_id, exc)
            result_payload = _make_error(
                "EXECUTION_ERROR",
                f"{kio_id} failed: {exc}",
                retryable=False,
            )
        return MessageEnvelope(
            message_id=str(uuid.uuid4()),
            session_id=envelope.session_id,
            workflow_id=envelope.workflow_id,
            source=kio_id,
            target=envelope.source,
            timestamp=datetime.now(timezone.utc).isoformat(),
            message_type="JOB_RESULT",
            payload=result_payload,
        )

    return app


def _make_nats_handler(kio_id: str, handler: HandlerFn, js) -> Any:
    """Return an async NATS message handler that calls the KIO handler and replies."""

    async def _handle(data: dict[str, Any], msg: Any) -> None:
        from shared.config import get_settings as _cfg
        reply_to: str = data.pop("_reply_to", "")
        cfg_timeout = float(_cfg().kio_client_timeout) - 10
        # Per-task timeout from envelope payload (Slide 8)
        task_timeout = data.get("payload", {}).get("timeout_seconds")
        timeout_s = float(task_timeout) if task_timeout else cfg_timeout
        try:
            envelope = MessageEnvelope(
                message_id=data.get("message_id", str(uuid.uuid4())),
                session_id=data.get("session_id", ""),
                workflow_id=data.get("workflow_id", ""),
                source=data.get("source", "orchestrator"),
                target=kio_id,
                timestamp=data.get("timestamp", datetime.now(timezone.utc).isoformat()),
                message_type=data.get("message_type", "JOB_REQUEST"),
                payload=data.get("payload", {}),
            )
            logger.info("[{}] JOB_REQUEST (NATS) session={}", kio_id, envelope.session_id)
            result_payload = await asyncio.wait_for(handler(envelope), timeout=timeout_s)
        except asyncio.TimeoutError:
            logger.error("[{}] NATS handler timed out after {}s", kio_id, timeout_s)
            result_payload = _make_error(
                "HANDLER_TIMEOUT",
                f"{kio_id} handler timed out after {timeout_s:.0f}s",
                retryable=True,
            )
        except Exception as exc:
            logger.exception("[{}] NATS handler error: {}", kio_id, exc)
            result_payload = _make_error(
                "EXECUTION_ERROR",
                f"{kio_id} failed: {exc}",
                retryable=False,
            )

        result_envelope = {
            "message_id": str(uuid.uuid4()),
            "session_id": data.get("session_id", ""),
            "workflow_id": data.get("workflow_id", ""),
            "source": kio_id,
            "target": data.get("source", "orchestrator"),
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "message_type": "JOB_RESULT",
            "payload": result_payload,
        }

        # ACK before reply (intentional ordering):
        # If we replied first and the ACK failed, NATS would redeliver the
        # message causing a duplicate LLM call — expensive and potentially
        # harmful.  If we ack first and the reply fails, the orchestrator
        # times out and marks the session FAILED, which is visible and
        # recoverable.  For slow LLM workloads, ack-first is the lesser evil.
        await msg.ack()

        if reply_to:
            await js.publish_reply(reply_to, result_envelope)
        else:
            logger.warning("[{}] No _reply_to in message — result discarded", kio_id)

    return _handle


def placeholder_handler(kio_id: str, task_label: str, delay: float = 1.0) -> HandlerFn:
    """Async handler that simulates work and returns a dummy artifact."""

    async def _handle(envelope: MessageEnvelope) -> dict[str, Any]:
        await asyncio.sleep(delay)
        description = envelope.payload.get("description", "N/A")
        return {
            "status": "DONE",
            "artifact_id": str(uuid.uuid4()),
            "artifact_data": {
                "kio": kio_id,
                "task": task_label,
                "description": description[:200],
                "produced_at": datetime.now(timezone.utc).isoformat(),
                "note": "Placeholder — real implementation pending.",
            },
            "message": f"{kio_id.upper()} placeholder complete.",
        }

    return _handle
