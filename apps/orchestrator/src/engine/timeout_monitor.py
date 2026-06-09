"""Per-task timeout monitor — Slide 8 / 18 of the interface spec.

Each workflow run can carry a ``timeout_seconds`` value.  The monitor wakes
every few seconds, checks all active sessions, and cancels any that have
exceeded their deadline by emitting WORKFLOW_FAILED and updating Session Manager.

Integration
-----------
  WorkflowRunner.run() stores ``deadline`` in ``_active[session_id]`` when
  ``timeout_seconds`` is provided.  TimeoutMonitor reads that key.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

from loguru import logger
from shared.config import get_settings


class TimeoutMonitor:
    """Background task that cancels sessions that exceed their deadline."""

    def __init__(self, active: dict[str, dict[str, Any]]) -> None:
        self._active = active
        self._task: asyncio.Task | None = None
        # Injected after construction to avoid circular imports
        self._cancel_cb = None  # async (session_id: str, reason: str) → None

    def set_cancel_callback(self, cb) -> None:
        """Register the async coroutine called when a session times out."""
        self._cancel_cb = cb

    def start(self) -> None:
        self._task = asyncio.create_task(self._loop())
        logger.info("[timeout_monitor] started (poll every {}s)", get_settings().timeout_monitor_interval)

    def stop(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()

    async def _loop(self) -> None:
        interval = get_settings().timeout_monitor_interval
        while True:
            try:
                await asyncio.sleep(interval)
                await self._sweep()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.warning("[timeout_monitor] sweep error: {}", exc)

    async def _sweep(self) -> None:
        now = datetime.now(timezone.utc)
        for session_id, state in list(self._active.items()):
            deadline = state.get("deadline")
            if not deadline:
                continue
            if state.get("status") in ("COMPLETED", "FAILED", "PENDING_REVIEW", "BLOCKED"):
                continue
            try:
                dl = datetime.fromisoformat(deadline) if isinstance(deadline, str) else deadline
                if now > dl:
                    elapsed = (now - dl).total_seconds()
                    logger.warning(
                        "[timeout_monitor] session {} exceeded deadline by {:.0f}s → cancelling",
                        session_id[:8], elapsed,
                    )
                    if self._cancel_cb:
                        await self._cancel_cb(session_id, "TASK_TIMEOUT")
            except Exception as exc:
                logger.debug("[timeout_monitor] deadline parse error for {}: {}", session_id[:8], exc)


# ------------------------------------------------------------------
# Singleton
# ------------------------------------------------------------------

_monitor: TimeoutMonitor | None = None


def get_timeout_monitor(active: dict | None = None) -> TimeoutMonitor:
    global _monitor
    if _monitor is None:
        if active is None:
            raise RuntimeError("TimeoutMonitor must be initialised with active dict on first call")
        _monitor = TimeoutMonitor(active)
    return _monitor
