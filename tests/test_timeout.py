"""Tests for WorkflowRunner.cancel() and TimeoutMonitor._sweep() — Phase 9.3.1."""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from apps.orchestrator.src.engine.event_bus import EventBus
from apps.orchestrator.src.engine.timeout_monitor import TimeoutMonitor
from apps.orchestrator.src.engine.workflow_runner import WorkflowRunner


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_sm() -> AsyncMock:
    sm = AsyncMock()
    sm.create_session.return_value = {"session_id": str(uuid.uuid4())}
    sm.update_status.return_value = {}
    sm.update_progress.return_value = {}
    sm.register_artifact.return_value = {"artifact_id": str(uuid.uuid4())}
    sm.get_artifacts.return_value = []
    return sm


def _make_runner(sm: AsyncMock) -> WorkflowRunner:
    bus = EventBus()
    return WorkflowRunner(
        session_client=sm, kio_client=AsyncMock(),
        lm_client=AsyncMock(), event_bus=bus,
    )


def _active_session(session_id: str, status: str = "RUNNING", deadline: str | None = None) -> dict:
    return {
        "workflow_id": "wf-test",
        "kio_sequence": ["kio2"],
        "hitl_after": [],
        "status": status,
        "progress": 0,
        "total": 1,
        "active_kio": "kio2",
        "pending_checkpoint_id": None,
        "artifacts": [],
        "owner": "test",
        "deadline": deadline,
    }


# ── WorkflowRunner.cancel() — CANCELLED path ─────────────────────────────────

async def test_cancel_transitions_to_cancelled():
    sm = _make_sm()
    runner = _make_runner(sm)
    session_id = str(uuid.uuid4())
    runner._active[session_id] = _active_session(session_id)

    await runner.cancel(session_id)

    sm.update_status.assert_any_await(session_id, "CANCELLED")


async def test_cancel_cleans_up_active_state():
    sm = _make_sm()
    runner = _make_runner(sm)
    session_id = str(uuid.uuid4())
    runner._active[session_id] = _active_session(session_id)

    await runner.cancel(session_id)

    assert runner.get_state(session_id) is None


async def test_cancel_emits_workflow_cancelled_event():
    sm = _make_sm()
    bus = EventBus()
    runner = WorkflowRunner(session_client=sm, kio_client=AsyncMock(), lm_client=AsyncMock(), event_bus=bus)
    session_id = str(uuid.uuid4())
    runner._active[session_id] = _active_session(session_id)

    received: list[str] = []

    async def collect():
        async for event in bus.subscribe():
            received.append(event.event_type)
            if event.event_type == "WORKFLOW_CANCELLED":
                return

    task = asyncio.create_task(collect())
    await asyncio.sleep(0)  # let collector subscribe
    await runner.cancel(session_id)
    await asyncio.wait_for(task, timeout=2.0)

    assert "WORKFLOW_CANCELLED" in received


async def test_cancel_unknown_session_is_noop():
    sm = _make_sm()
    runner = _make_runner(sm)
    # Should not raise
    await runner.cancel("non-existent-session")
    sm.update_status.assert_not_awaited()


# ── WorkflowRunner.cancel() — TASK_TIMEOUT path ──────────────────────────────

async def test_cancel_task_timeout_transitions_to_failed():
    sm = _make_sm()
    runner = _make_runner(sm)
    session_id = str(uuid.uuid4())
    runner._active[session_id] = _active_session(session_id)

    await runner.cancel(session_id, reason="TASK_TIMEOUT")

    statuses = [c.args[1] for c in sm.update_status.await_args_list]
    assert "TIMEOUT" in statuses
    assert "FAILED" in statuses


async def test_cancel_task_timeout_emits_timeout_then_failed_events():
    sm = _make_sm()
    bus = EventBus()
    runner = WorkflowRunner(session_client=sm, kio_client=AsyncMock(), lm_client=AsyncMock(), event_bus=bus)
    session_id = str(uuid.uuid4())
    runner._active[session_id] = _active_session(session_id)

    received: list[str] = []

    async def collect():
        async for event in bus.subscribe():
            received.append(event.event_type)
            if event.event_type == "WORKFLOW_FAILED":
                return

    task = asyncio.create_task(collect())
    await asyncio.sleep(0)
    await runner.cancel(session_id, reason="TASK_TIMEOUT")
    await asyncio.wait_for(task, timeout=2.0)

    assert "WORKFLOW_TIMEOUT" in received
    assert "WORKFLOW_FAILED" in received
    # TIMEOUT event must come before FAILED
    assert received.index("WORKFLOW_TIMEOUT") < received.index("WORKFLOW_FAILED")


async def test_cancel_task_timeout_cleans_up_active_state():
    sm = _make_sm()
    runner = _make_runner(sm)
    session_id = str(uuid.uuid4())
    runner._active[session_id] = _active_session(session_id)

    await runner.cancel(session_id, reason="TASK_TIMEOUT")

    assert runner.get_state(session_id) is None


# ── TimeoutMonitor._sweep() ───────────────────────────────────────────────────

async def test_sweep_cancels_overdue_session():
    active: dict = {}
    session_id = str(uuid.uuid4())
    overdue = (datetime.now(timezone.utc) - timedelta(seconds=10)).isoformat()
    active[session_id] = _active_session(session_id, deadline=overdue)

    cancelled: list[str] = []

    async def mock_cancel(sid: str, reason: str):
        cancelled.append(sid)

    monitor = TimeoutMonitor(active)
    monitor.set_cancel_callback(mock_cancel)
    await monitor._sweep()

    assert session_id in cancelled


async def test_sweep_ignores_session_without_deadline():
    active: dict = {}
    session_id = str(uuid.uuid4())
    active[session_id] = _active_session(session_id, deadline=None)

    cancelled: list[str] = []
    monitor = TimeoutMonitor(active)
    monitor.set_cancel_callback(lambda sid, r: cancelled.append(sid))
    await monitor._sweep()

    assert cancelled == []


async def test_sweep_ignores_future_deadline():
    active: dict = {}
    session_id = str(uuid.uuid4())
    future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    active[session_id] = _active_session(session_id, deadline=future)

    cancelled: list[str] = []
    monitor = TimeoutMonitor(active)
    monitor.set_cancel_callback(lambda sid, r: cancelled.append(sid))
    await monitor._sweep()

    assert cancelled == []


async def test_sweep_skips_terminal_statuses():
    active: dict = {}
    overdue = (datetime.now(timezone.utc) - timedelta(seconds=5)).isoformat()

    for terminal_status in ("COMPLETED", "FAILED", "TIMEOUT", "CANCELLED", "PENDING_REVIEW", "BLOCKED"):
        sid = str(uuid.uuid4())
        active[sid] = _active_session(sid, status=terminal_status, deadline=overdue)

    cancelled: list[str] = []
    monitor = TimeoutMonitor(active)
    monitor.set_cancel_callback(lambda sid, r: cancelled.append(sid))
    await monitor._sweep()

    assert cancelled == []


async def test_sweep_passes_task_timeout_reason():
    active: dict = {}
    session_id = str(uuid.uuid4())
    overdue = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
    active[session_id] = _active_session(session_id, deadline=overdue)

    reasons: list[str] = []

    async def mock_cancel(sid: str, reason: str):
        reasons.append(reason)

    monitor = TimeoutMonitor(active)
    monitor.set_cancel_callback(mock_cancel)
    await monitor._sweep()

    assert "TASK_TIMEOUT" in reasons


async def test_sweep_handles_malformed_deadline_gracefully():
    active: dict = {}
    session_id = str(uuid.uuid4())
    active[session_id] = _active_session(session_id, deadline="not-a-date")

    cancelled: list[str] = []
    monitor = TimeoutMonitor(active)
    monitor.set_cancel_callback(lambda sid, r: cancelled.append(sid))

    # Must not raise
    await monitor._sweep()
    assert cancelled == []


async def test_sweep_without_cancel_callback_does_not_raise():
    active: dict = {}
    session_id = str(uuid.uuid4())
    overdue = (datetime.now(timezone.utc) - timedelta(seconds=5)).isoformat()
    active[session_id] = _active_session(session_id, deadline=overdue)

    monitor = TimeoutMonitor(active)
    # No cancel callback set
    await monitor._sweep()  # must not raise
