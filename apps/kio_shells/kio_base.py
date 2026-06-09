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
    session_id: str
    workflow_id: str
    source: str
    target: str
    timestamp: str
    message_type: str
    payload: dict[str, Any] = {}


HandlerFn = Callable[[MessageEnvelope], Awaitable[dict[str, Any]]]


def make_kio_app(kio_id: str, title: str, handler: HandlerFn) -> FastAPI:
    """Build a FastAPI app for a KIO shell.

    handler(envelope) must return a dict that becomes the JOB_RESULT payload::

        {
          "status":       "DONE" | "REVIEW_REQUIRED",
          "artifact_id":  str,
          "artifact_data": dict,
          "message":       str,
          "hitl_question": str,   # only when status=REVIEW_REQUIRED
        }

    If ``USE_NATS=true`` in the environment (default), the app also subscribes
    to ``kio.{kio_id}.request`` via JetStream on startup.
    """

    @asynccontextmanager
    async def _lifespan(app: FastAPI):
        from shared.config import get_settings
        cfg = get_settings()
        if cfg.use_nats:
            try:
                from shared.messaging.jetstream import get_jetstream
                js = await get_jetstream()
                await js.subscribe_requests(kio_id, _make_nats_handler(kio_id, handler, js))
                logger.info("[{}] JetStream consumer active", kio_id)
            except Exception as exc:
                logger.warning("[{}] NATS unavailable — HTTP only ({})", kio_id, exc)
        yield
        # JetStream subscriptions are cleaned up by the manager's drain() on process exit.

    app = FastAPI(title=f"{kio_id.upper()} — {title}", version="0.1.0", lifespan=_lifespan)

    @app.get("/health/")
    async def health():
        return {"status": "ok", "service": kio_id, "title": title}

    @app.post("/execute")
    async def execute(envelope: MessageEnvelope) -> MessageEnvelope:
        """HTTP execution endpoint — always available for direct testing."""
        logger.info("[{}] JOB_REQUEST (HTTP) from {} session={}",
                    kio_id, envelope.source, envelope.session_id)
        try:
            result_payload = await handler(envelope)
        except Exception as exc:
            logger.exception("[{}] execution failed: {}", kio_id, exc)
            result_payload = {
                "status": "FAILED",
                "artifact_id": str(uuid.uuid4()),
                "artifact_data": {},
                "message": f"{kio_id} failed: {exc}",
            }
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
        reply_to: str = data.pop("_reply_to", "")
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
            result_payload = await handler(envelope)
        except Exception as exc:
            logger.exception("[{}] NATS handler error: {}", kio_id, exc)
            result_payload = {
                "status": "FAILED",
                "artifact_id": str(uuid.uuid4()),
                "artifact_data": {},
                "message": f"{kio_id} failed: {exc}",
            }

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

        # Ack the JetStream message first, then reply
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
