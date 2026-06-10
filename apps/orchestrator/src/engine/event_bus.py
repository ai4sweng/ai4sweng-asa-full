"""In-process SSE event bus — one asyncio.Queue per subscriber."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, AsyncGenerator


@dataclass
class WorkflowEvent:
    """A single workflow event emitted by the orchestrator."""

    event_type: str
    session_id: str
    message: str
    data: dict[str, Any] | None = None
    owner: str = ""  # stamped by WorkflowRunner._emit; used for per-user SSE filtering
    ts: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_sse(self) -> str:
        """Serialise to Server-Sent Events wire format."""
        payload = {
            "event_type": self.event_type,
            "session_id": self.session_id,
            "message": self.message,
            "timestamp": self.ts,
            **(self.data or {}),
        }
        return f"data: {json.dumps(payload)}\n\n"


class EventBus:
    """
    Broadcast event bus for SSE streaming.

    publish() snapshots the subscriber list before iterating, so
    concurrent subscribe()/unsubscribe() operations are safe.
    """

    def __init__(self) -> None:
        self._queues: list[asyncio.Queue] = []
        self._lock = asyncio.Lock()

    def publish(self, event: WorkflowEvent) -> None:
        """Broadcast an event to all current subscribers (snapshot-safe)."""
        # Take a snapshot so that concurrent subscribe/unsubscribe during
        # iteration cannot cause RuntimeError or lost-event bugs.
        for q in list(self._queues):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                pass  # slow subscriber — drop rather than block the publisher

    async def subscribe(self, owner: str | None = None) -> AsyncGenerator[WorkflowEvent, None]:
        """Yield events until the client disconnects.

        Parameters
        ----------
        owner:
            When set, only events belonging to this user are yielded.
            Pass ``None`` (or omit) to receive all events (admin/debug use).

        A heartbeat is sent every ``sse_heartbeat_interval`` seconds (from config) so that
        FastAPI's StreamingResponse can detect disconnected browsers promptly
        rather than holding a zombie queue entry indefinitely.
        """
        from shared.config import get_settings

        cfg = get_settings()
        q: asyncio.Queue[WorkflowEvent | None] = asyncio.Queue(maxsize=cfg.sse_queue_maxsize)

        async def _heartbeat() -> None:
            while True:
                await asyncio.sleep(cfg.sse_heartbeat_interval)
                try:
                    q.put_nowait(None)  # None sentinel = keepalive
                except asyncio.QueueFull:
                    pass

        async with self._lock:
            self._queues.append(q)
        hb_task = asyncio.create_task(_heartbeat())
        try:
            while True:
                event = await q.get()
                if event is None:
                    # Heartbeat — yield a comment line to keep the TCP connection alive.
                    yield WorkflowEvent("HEARTBEAT", "", "keepalive")
                elif owner is None or not event.owner or event.owner == owner:
                    # Deliver only events that belong to this subscriber's user.
                    # Events with no owner stamp (legacy / system events) are always delivered.
                    yield event
        finally:
            hb_task.cancel()
            async with self._lock:
                try:
                    self._queues.remove(q)
                except ValueError:
                    pass  # already removed


_bus: EventBus | None = None


def get_event_bus() -> EventBus:
    """Return the process-wide EventBus singleton (synchronous — no race risk)."""
    global _bus
    if _bus is None:
        _bus = EventBus()
    return _bus
