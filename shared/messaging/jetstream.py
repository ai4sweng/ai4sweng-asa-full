"""NATS JetStream manager — durable KIO job queue with at-least-once delivery.

Stream topology
---------------
  Stream  : KIO_JOBS
  Subjects: kio.*.request   (orchestrator → KIO, durable work queue)
  Retention: WorkQueue — message removed after explicit ack

Request-reply pattern
---------------------
  1. Orchestrator publishes to  kio.{kio_id}.request  via JetStream (durable).
  2. Message carries   _reply_to = "_kio_reply.{corr_id}"  in the payload.
  3. KIO processes the envelope, publishes result to   _reply_to   (core NATS).
  4. Orchestrator receives result on the ephemeral subscription, KIO acks JetStream.

  The request survives KIO restarts (not acked → redelivered).
  The reply is ephemeral: orchestrator must be alive to receive it.
  Phase 8 (PostgreSQL checkpointer) will make the orchestrator crash-safe.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any, Awaitable, Callable

import nats
import nats.errors
import nats.js.errors
from nats.aio.client import Client as NatsClient
from nats.js import JetStreamContext
from nats.js.api import RetentionPolicy, StorageType, StreamConfig
from loguru import logger

from shared.config import get_settings

HandlerFn = Callable[[dict[str, Any], Any], Awaitable[None]]


class JetStreamManager:
    """Process-wide NATS + JetStream connection; one instance per service."""

    def __init__(self) -> None:
        self._nc: NatsClient | None = None
        self._js: JetStreamContext | None = None

    async def connect(self) -> None:
        """Connect to NATS and ensure the KIO_JOBS stream exists."""
        cfg = get_settings()
        self._nc = await nats.connect(
            cfg.nats_url,
            reconnect_time_wait=cfg.nats_reconnect_wait,
            max_reconnect_attempts=cfg.nats_max_reconnect_attempts,
        )
        self._js = self._nc.jetstream()
        await self._ensure_stream(cfg.nats_kio_stream)
        logger.info("JetStream ready — stream={} nats={}", cfg.nats_kio_stream, cfg.nats_url)

    async def _ensure_stream(self, name: str) -> None:
        """Create the KIO work-queue stream if it does not already exist."""
        try:
            await self._js.stream_info(name)
            logger.debug("JetStream stream '{}' already exists", name)
        except nats.js.errors.NotFoundError:
            cfg = get_settings()
            stream_config = StreamConfig(
                name=name,
                subjects=["kio.*.request"],
                retention=RetentionPolicy.WORK_QUEUE,
                storage=StorageType.FILE,
                max_age=cfg.nats_stream_max_age,
                # Note: max_deliver and ack_wait are consumer properties, not stream properties.
                # They are set when consumers subscribe via subscribe_requests().
            )
            await self._js.add_stream(config=stream_config)
            logger.info("Created JetStream stream: {}", name)

    # ------------------------------------------------------------------
    # Orchestrator side — publish request, wait for reply
    # ------------------------------------------------------------------

    async def request_reply(
        self,
        kio_id: str,
        envelope: dict[str, Any],
        timeout: float | None = None,
    ) -> dict[str, Any]:
        """Publish a JOB_REQUEST to JetStream and await the KIO's direct reply.

        The request is durable (survives KIO restart).
        The reply arrives on an ephemeral core-NATS subscription.

        Raises
        ------
        asyncio.TimeoutError  — KIO did not reply within *timeout* seconds.
        RuntimeError          — NATS not connected.
        """
        if self._nc is None or self._js is None:
            raise RuntimeError("JetStreamManager not connected — call connect() first")

        cfg = get_settings()
        effective_timeout = timeout if timeout is not None else float(cfg.nats_request_timeout)
        corr_id = envelope.get("message_id", str(uuid.uuid4()))
        reply_subject = f"_kio_reply.{corr_id}"

        loop = asyncio.get_running_loop()
        reply_future: asyncio.Future[dict[str, Any]] = loop.create_future()

        async def _on_reply(msg: Any) -> None:
            if not reply_future.done():
                try:
                    reply_future.set_result(json.loads(msg.data.decode()))
                except Exception as exc:
                    reply_future.set_exception(exc)

        # Subscribe for the reply BEFORE publishing (avoid race)
        sub = await self._nc.subscribe(reply_subject, cb=_on_reply)
        try:
            payload = json.dumps({**envelope, "_reply_to": reply_subject}).encode()
            await self._js.publish(f"kio.{kio_id}.request", payload)
            logger.debug("→ JetStream kio.{}.request corr={}", kio_id, corr_id[:8])
            return await asyncio.wait_for(reply_future, timeout=effective_timeout)
        finally:
            await sub.unsubscribe()

    # ------------------------------------------------------------------
    # KIO side — subscribe to requests, publish result
    # ------------------------------------------------------------------

    async def subscribe_requests(self, kio_id: str, handler: HandlerFn) -> None:
        """Start a durable pull-consumer for kio.{kio_id}.request.

        Pull consumers are fully compatible with WorkQueue retention streams in
        nats-py 2.x (push consumers require a deliver subject which conflicts
        with the WorkQueue stream in some server/client combinations).

        *handler* receives ``(envelope_dict, nats_msg)`` — it must call
        ``await nats_msg.ack()`` after successful processing.

        Consumer properties:
          max_deliver=cfg.nats_max_deliver  — retry N× if the KIO crashes before acking
          ack_wait=300s  — matches kio_client_timeout so we never wait longer
        """
        if self._js is None:
            raise RuntimeError("JetStreamManager not connected")

        durable = f"{kio_id}-worker"
        cfg = get_settings()
        ack_wait_s = int(cfg.kio_client_timeout)  # nats-py 2.x uses seconds

        psub = await self._js.pull_subscribe(
            f"kio.{kio_id}.request",
            durable=durable,
            config=nats.js.api.ConsumerConfig(
                max_deliver=cfg.nats_max_deliver,
                ack_wait=ack_wait_s,
                filter_subject=f"kio.{kio_id}.request",
            ),
        )
        logger.info(
            "[{}] JetStream pull consumer '{}' active (max_deliver={} ack_wait={}s)",
            kio_id,
            durable,
            cfg.nats_max_deliver,
            ack_wait_s,
        )

        async def _poll_loop() -> None:
            while True:
                try:
                    msgs = await psub.fetch(batch=1, timeout=cfg.nats_fetch_timeout)
                    for msg in msgs:
                        asyncio.create_task(_dispatch(msg))
                except (nats.js.errors.FetchTimeoutError, nats.errors.TimeoutError):
                    pass  # no messages — keep polling
                except asyncio.CancelledError:
                    break
                except Exception as exc:
                    logger.warning("[{}] Pull fetch error: {}", kio_id, exc)
                    await asyncio.sleep(1)

        async def _dispatch(msg: Any) -> None:
            try:
                data = json.loads(msg.data.decode())
                await handler(data, msg)
            except Exception as exc:
                logger.exception("[{}] JetStream handler error: {}", kio_id, exc)
                await msg.nak()

        asyncio.create_task(_poll_loop())

    async def publish_reply(self, reply_subject: str, result: dict[str, Any]) -> None:
        """Send the JOB_RESULT back to the orchestrator via core NATS (fast path)."""
        if self._nc is None:
            raise RuntimeError("JetStreamManager not connected")
        await self._nc.publish(reply_subject, json.dumps(result).encode())

    async def publish(self, subject: str, data: dict[str, Any]) -> None:
        """Fire-and-forget publish to a core NATS subject (heartbeats, events)."""
        if self._nc is None:
            raise RuntimeError("JetStreamManager not connected")
        await self._nc.publish(subject, json.dumps(data).encode())

    async def subscribe_core(self, subject: str, cb) -> Any:
        """Subscribe to a core NATS subject (supports * and > wildcards).

        Used by the orchestrator to receive CAPABILITY_ANNOUNCEMENT and HEARTBEAT
        messages from KIO shells without going through JetStream.
        """
        if self._nc is None:
            raise RuntimeError("JetStreamManager not connected")
        return await self._nc.subscribe(subject, cb=cb)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def close(self) -> None:
        """Drain subscriptions and close the NATS connection."""
        if self._nc:
            await self._nc.drain()
            await self._nc.close()
            self._nc = None
            self._js = None
            logger.info("JetStream connection closed")

    @property
    def connected(self) -> bool:
        return self._nc is not None and self._nc.is_connected


# ------------------------------------------------------------------
# Process-wide singleton
# ------------------------------------------------------------------

_manager: JetStreamManager | None = None
_lock = asyncio.Lock()


async def get_jetstream() -> JetStreamManager:
    """Return the process-wide JetStreamManager, connecting on first call."""
    global _manager
    async with _lock:
        if _manager is None or not _manager.connected:
            _manager = JetStreamManager()
            await _manager.connect()
    return _manager
