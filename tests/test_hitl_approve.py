"""Integration tests for the HITL checkpoint + approve flow — Phase 9.2.

Scenario tested:
  kio2 → [HITL pause] → approve(feedback) → kio8 → COMPLETED

Technical notes:
- MemorySaver checkpointer (no PostgreSQL) — interrupt() persists state in-memory.
- kio execute mock yields (asyncio.sleep(0)) so the event loop stays cooperative.
- Terminal state detected via sm.update_status call tracking — no SSE subscription
  needed (avoids subscribe-before-publish race).
- HITL pause state detected via runner._active polling (status == BLOCKED/PENDING_REVIEW).
"""
from __future__ import annotations

import asyncio
import uuid
from typing import Any
from unittest.mock import AsyncMock, call

import pytest

from apps.orchestrator.src.engine.event_bus import EventBus
from apps.orchestrator.src.engine.workflow_runner import WorkflowRunner


# ── Factory helpers ──────────────────────────────────────────────────────────

def _make_sm(checkpoint_id: str | None = None) -> AsyncMock:
    sm = AsyncMock()
    sid = str(uuid.uuid4())
    ckpt = checkpoint_id or str(uuid.uuid4())
    sm.create_session.return_value = {"session_id": sid}
    sm.update_status.return_value = {}
    sm.update_progress.return_value = {}
    sm.register_artifact.return_value = {"artifact_id": str(uuid.uuid4())}
    sm.create_hitl_checkpoint.return_value = {"checkpoint_id": ckpt}
    sm.resolve_checkpoint.return_value = {}
    sm.get_artifacts.return_value = []
    return sm


def _make_kio_with_hitl(hitl_on_step: int = 0) -> AsyncMock:
    """KIO that returns REVIEW_REQUIRED on step hitl_on_step, DONE otherwise."""
    kio = AsyncMock()
    call_count = 0

    async def _execute(*args, **kwargs):
        nonlocal call_count
        await asyncio.sleep(0)
        resp: dict[str, Any]
        if call_count == hitl_on_step:
            resp = {
                "status": "DONE",  # KIO itself succeeds
                "artifact_id": str(uuid.uuid4()),
                "artifact_data": {"hitl_step": call_count},
                "message": "Step done — HITL triggered by hitl_after config.",
            }
        else:
            resp = {
                "status": "DONE",
                "artifact_id": str(uuid.uuid4()),
                "artifact_data": {"step": call_count},
                "message": f"Step {call_count} done.",
            }
        call_count += 1
        return {"payload": resp}

    kio.execute.side_effect = _execute
    return kio


def _make_runner(sm: AsyncMock, kio: AsyncMock) -> tuple[WorkflowRunner, EventBus]:
    from langgraph.checkpoint.memory import MemorySaver
    from apps.orchestrator.src.engine.workflow_graph import build_workflow_graph

    bus = EventBus()
    runner = WorkflowRunner(session_client=sm, kio_client=kio, lm_client=AsyncMock(), event_bus=bus)
    runner._graph = build_workflow_graph(
        sm, kio, AsyncMock(), bus, runner._active,
        checkpointer=MemorySaver(),
    )
    return runner, bus


async def _wait_sm_status(sm: AsyncMock, session_id: str, target: set[str], timeout: float = 8.0) -> str | None:
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        for c in sm.update_status.await_args_list:
            if len(c.args) >= 2 and c.args[0] == session_id and c.args[1] in target:
                return c.args[1]
        await asyncio.sleep(0.05)
    return None


async def _wait_hitl_pause(runner: WorkflowRunner, session_id: str, timeout: float = 5.0) -> dict:
    """Poll _active until BLOCKED or PENDING_REVIEW (set by hitl_node/_check_graph_state)."""
    deadline = asyncio.get_event_loop().time() + timeout
    hitl_states = {"BLOCKED", "PENDING_REVIEW"}
    while asyncio.get_event_loop().time() < deadline:
        state = runner._active.get(session_id)
        if state and state.get("status") in hitl_states:
            return dict(state)
        await asyncio.sleep(0.05)
    return {}


# ── Core HITL flow ────────────────────────────────────────────────────────────

async def test_hitl_pauses_then_resumes_to_completed():
    """Full HITL flow: run → pause → approve → complete."""
    sm = _make_sm()
    kio = _make_kio_with_hitl(hitl_on_step=0)
    runner, _ = _make_runner(sm, kio)

    session_id = await runner.run(
        workflow_id=str(uuid.uuid4()),
        kio_sequence=["kio2", "kio8"],
        hitl_after=["kio2"],  # pause after first KIO
        owner="test",
    )

    # Wait for graph to pause at HITL
    paused_state = await _wait_hitl_pause(runner, session_id)
    assert paused_state, "Workflow should be in HITL pause state"
    assert paused_state.get("status") in ("BLOCKED", "PENDING_REVIEW")

    # Approve — resumes the graph
    approve_result = await runner.approve(
        session_id,
        actor="human_operator",
        feedback="output looks good, proceed",
    )
    assert approve_result is not None
    assert approve_result["status"] == "APPROVED"

    # Wait for completion after resume
    status = await _wait_sm_status(sm, session_id, {"COMPLETED", "FAILED"})
    assert status == "COMPLETED"


async def test_hitl_sets_pending_checkpoint_id():
    """After HITL pause, pending_checkpoint_id is set in active state."""
    ckpt_id = str(uuid.uuid4())
    sm = _make_sm(checkpoint_id=ckpt_id)
    kio = _make_kio_with_hitl()
    runner, _ = _make_runner(sm, kio)

    session_id = await runner.run(
        workflow_id=str(uuid.uuid4()),
        kio_sequence=["kio2", "kio8"],
        hitl_after=["kio2"],
        owner="test",
    )

    paused = await _wait_hitl_pause(runner, session_id)
    assert paused.get("pending_checkpoint_id") is not None


async def test_hitl_approve_calls_resolve_checkpoint():
    """approve() must call sm.resolve_checkpoint."""
    sm = _make_sm()
    runner, _ = _make_runner(sm, _make_kio_with_hitl())

    session_id = await runner.run(
        workflow_id=str(uuid.uuid4()),
        kio_sequence=["kio2", "kio8"],
        hitl_after=["kio2"],
        owner="test",
    )

    await _wait_hitl_pause(runner, session_id)
    await runner.approve(session_id, actor="operator", feedback="ok")

    sm.resolve_checkpoint.assert_awaited_once()
    ckwargs = sm.resolve_checkpoint.await_args
    assert ckwargs.args[0] == session_id


async def test_hitl_both_kios_execute():
    """After HITL approve both kio2 and kio8 execute (total = 2 calls)."""
    sm = _make_sm()
    kio = _make_kio_with_hitl()
    runner, _ = _make_runner(sm, kio)

    session_id = await runner.run(
        workflow_id=str(uuid.uuid4()),
        kio_sequence=["kio2", "kio8"],
        hitl_after=["kio2"],
        owner="test",
    )

    await _wait_hitl_pause(runner, session_id)
    await runner.approve(session_id, actor="operator", feedback="approved")
    await _wait_sm_status(sm, session_id, {"COMPLETED", "FAILED"})

    assert kio.execute.call_count == 2


async def test_double_approve_returns_none():
    """A second approve() on an already-resumed session returns None."""
    sm = _make_sm()
    runner, _ = _make_runner(sm, _make_kio_with_hitl())

    session_id = await runner.run(
        workflow_id=str(uuid.uuid4()),
        kio_sequence=["kio2", "kio8"],
        hitl_after=["kio2"],
        owner="test",
    )

    await _wait_hitl_pause(runner, session_id)
    first = await runner.approve(session_id, actor="op", feedback="ok")
    second = await runner.approve(session_id, actor="op", feedback="ok again")

    assert first is not None
    assert second is None  # no pending_checkpoint_id after first approve


async def test_approve_unknown_session_returns_none():
    """approve() returns None for a session that doesn't exist."""
    sm = _make_sm()
    runner, _ = _make_runner(sm, _make_kio_with_hitl())

    result = await runner.approve("non-existent-session", actor="op", feedback="ok")
    assert result is None


# ── HITL on different positions ───────────────────────────────────────────────

async def test_hitl_after_last_kio_completes():
    """hitl_after last KIO: pause then approve → COMPLETED."""
    sm = _make_sm()
    kio = _make_kio_with_hitl(hitl_on_step=1)  # second step (index 1)
    runner, _ = _make_runner(sm, kio)

    session_id = await runner.run(
        workflow_id=str(uuid.uuid4()),
        kio_sequence=["kio2", "kio8"],
        hitl_after=["kio8"],  # HITL after second (last) KIO
        owner="test",
    )

    paused = await _wait_hitl_pause(runner, session_id)
    assert paused, "Should pause after kio8"

    await runner.approve(session_id, actor="op", feedback="all good")
    status = await _wait_sm_status(sm, session_id, {"COMPLETED", "FAILED"})
    assert status == "COMPLETED"


# ── No-HITL baseline ─────────────────────────────────────────────────────────

async def test_no_hitl_completes_without_approve():
    """hitl_after=[] means the graph completes without any human interaction."""
    sm = _make_sm()
    runner, _ = _make_runner(sm, _make_kio_with_hitl())

    session_id = await runner.run(
        workflow_id=str(uuid.uuid4()),
        kio_sequence=["kio2", "kio8"],
        hitl_after=[],  # no HITL
        owner="test",
    )

    status = await _wait_sm_status(sm, session_id, {"COMPLETED", "FAILED"})
    assert status == "COMPLETED"
