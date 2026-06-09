"""
NATS client with persist-before-publish guarantee.

All published messages are persisted to PostgreSQL before NATS dispatch
to guarantee replayability from the System of Record.

Each publish uses its own SessionProvider scope so concurrent NATS
traffic from multiple KIOs never shares one AsyncSession.
"""

import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any

import nats
from nats.aio.client import Client as NatsClient
from nats.aio.msg import Msg

from shared.config import get_settings
from shared.contracts.kio_envelope import KIOEnvelope
from shared.persistence.session_provider import SessionProvider

logger = logging.getLogger(__name__)

MessageHandler = Callable[[dict[str, Any]], Awaitable[None]]


class NatsMessagingClient:
    """Async NATS wrapper with envelope serialization and persistence hooks."""

    def __init__(self, session_provider: SessionProvider | None = None) -> None:
        self._nc: NatsClient | None = None
        self._session_provider = session_provider
        self._subscriptions: list[Any] = []

    async def connect(self) -> None:
        settings = get_settings()
        self._nc = await nats.connect(settings.nats_url)
        logger.info("Connected to NATS at %s", settings.nats_url)

    async def close(self) -> None:
        if self._nc:
            await self._nc.drain()
            await self._nc.close()
            self._nc = None

    async def _persist_message(self, envelope: KIOEnvelope) -> bool:
        """Persist-before-publish: returns False if duplicate (idempotent skip)."""
        if self._session_provider is None:
            return True
        data = envelope.to_dict()
        async with self._session_provider.session_scope() as repo:
            if await repo.message_exists(envelope.idempotency_key):
                logger.debug(
                    "Skipping duplicate message idempotency_key=%s",
                    envelope.idempotency_key,
                )
                return False
            await repo.save_message(data)
        return True

    async def publish(self, envelope: KIOEnvelope) -> None:
        """Publish envelope — persists to PostgreSQL before NATS dispatch."""
        if self._nc is None:
            raise RuntimeError("NATS client not connected")
        saved = await self._persist_message(envelope)
        if not saved:
            return
        await self._nc.publish(envelope.subject, json.dumps(envelope.to_dict()).encode())
        logger.debug("Published %s → %s", envelope.message_type, envelope.subject)

    async def subscribe(self, subject: str, handler: MessageHandler) -> None:
        """Subscribe to a NATS subject with async handler."""

        async def _callback(msg: Msg) -> None:
            try:
                payload = json.loads(msg.data.decode())
                await handler(payload)
            except Exception:
                logger.exception("Error handling message on %s", subject)

        if self._nc is None:
            raise RuntimeError("NATS client not connected")
        sub = await self._nc.subscribe(subject, cb=_callback)
        self._subscriptions.append(sub)
        logger.info("Subscribed to %s", subject)

    async def request(
        self,
        subject: str,
        envelope: KIOEnvelope,
        timeout: float = 30.0,
    ) -> dict[str, Any]:
        """NATS request-reply for synchronous KIO dispatch."""
        if self._nc is None:
            raise RuntimeError("NATS client not connected")
        await self._persist_message(envelope)
        response = await self._nc.request(
            subject, json.dumps(envelope.to_dict()).encode(), timeout=timeout
        )
        return json.loads(response.data.decode())
