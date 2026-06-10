"""Tests for WorkflowRunner.rehydrate() — Phase 9.3.2."""
from __future__ import annotations

import asyncio
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from apps.orchestrator.src.engine.event_bus import EventBus
from apps.orchestrator.src.engine.workflow_runner import WorkflowRunner


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_sm(**overrides) -> AsyncMock:
    sm = AsyncMock()
    sm.create_session.return_value = {"session_id": str(uuid.uuid4())}
    sm.update_status.return_value = {}
    sm.list_sessions.return_value = []
    for k, v in overrides.items():
        setattr(sm, k, v)
    return sm


def _make_runner(sm: AsyncMock) -> WorkflowRunner:
    bus = EventBus()
    return WorkflowRunner(
        session_client=sm, kio_client=AsyncMock(),
        lm_client=AsyncMock(), event_bus=bus,
    )


def _make_graph_state(session_id: str, pending_ckpt: str | None = None) -> MagicMock:
    """Return a mock LangGraph state object with a minimal values dict."""
    gs = MagicMock()
    gs.values = {
        "session_id": session_id,
        "workflow_id": "wf-test",
        "kio_sequence": ["kio2", "kio8"],
        "hitl_after": ["kio2"],
        "description": "Fix the codebase",
        "working_directory": "/tmp/repo",
        "current_step": 1,
        "artifacts": ["art-abc"],
        "pending_checkpoint_id": pending_ckpt,
    }
    return gs


def _wire_graph(runner: WorkflowRunner, gs: MagicMock | None) -> AsyncMock:
    """Inject a mock compiled graph into the runner."""
    mock_graph = AsyncMock()
    mock_graph.aget_state.return_value = gs
    runner._graph = mock_graph
    return mock_graph


# ── PENDING_REVIEW session restored ──────────────────────────────────────────

async def test_rehydrate_restores_pending_review_session():
    session_id = str(uuid.uuid4())
    sm = _make_sm()
    sm.list_sessions.return_value = [
        {"session_id": session_id, "status": "PENDING_REVIEW", "workflow_id": "wf-test"}
    ]
    runner = _make_runner(sm)
    _wire_graph(runner, _make_graph_state(session_id, pending_ckpt="ckpt-123"))

    await runner.rehydrate()

    assert session_id in runner._active
    state = runner._active[session_id]
    assert state["status"] == "PENDING_REVIEW"
    assert state["pending_checkpoint_id"] == "ckpt-123"


async def test_rehydrate_restores_kio_sequence():
    session_id = str(uuid.uuid4())
    sm = _make_sm()
    sm.list_sessions.return_value = [
        {"session_id": session_id, "status": "RUNNING", "workflow_id": "wf-x"}
    ]
    runner = _make_runner(sm)
    _wire_graph(runner, _make_graph_state(session_id))

    await runner.rehydrate()

    assert runner._active[session_id]["kio_sequence"] == ["kio2", "kio8"]


async def test_rehydrate_creates_session_lock():
    session_id = str(uuid.uuid4())
    sm = _make_sm()
    sm.list_sessions.return_value = [
        {"session_id": session_id, "status": "PENDING_REVIEW", "workflow_id": "wf-test"}
    ]
    runner = _make_runner(sm)
    _wire_graph(runner, _make_graph_state(session_id))

    await runner.rehydrate()

    # approve() needs a per-session lock — ensure it was created
    assert session_id in runner._session_locks
    assert isinstance(runner._session_locks[session_id], asyncio.Lock)


# ── Terminal sessions skipped ─────────────────────────────────────────────────

async def test_rehydrate_skips_completed_sessions():
    sid_done = str(uuid.uuid4())
    sid_fail = str(uuid.uuid4())
    sm = _make_sm()
    sm.list_sessions.return_value = [
        {"session_id": sid_done, "status": "COMPLETED"},
        {"session_id": sid_fail, "status": "FAILED"},
    ]
    runner = _make_runner(sm)
    mock_graph = AsyncMock()
    runner._graph = mock_graph

    await runner.rehydrate()

    assert sid_done not in runner._active
    assert sid_fail not in runner._active
    # aget_state should never have been called (list was empty after filtering)
    mock_graph.aget_state.assert_not_awaited()


async def test_rehydrate_skips_cancelled_sessions():
    sid = str(uuid.uuid4())
    sm = _make_sm()
    sm.list_sessions.return_value = [
        {"session_id": sid, "status": "CANCELLED"},
    ]
    runner = _make_runner(sm)
    mock_graph = AsyncMock()
    runner._graph = mock_graph

    await runner.rehydrate()

    assert sid not in runner._active


# ── Missing checkpoint ────────────────────────────────────────────────────────

async def test_rehydrate_skips_session_with_no_checkpoint():
    """Session listed in SM but graph has no checkpoint yet — should skip gracefully."""
    session_id = str(uuid.uuid4())
    sm = _make_sm()
    sm.list_sessions.return_value = [
        {"session_id": session_id, "status": "RUNNING"}
    ]
    runner = _make_runner(sm)
    # aget_state returns None → no checkpoint
    _wire_graph(runner, None)

    await runner.rehydrate()

    assert session_id not in runner._active


async def test_rehydrate_skips_session_with_empty_values():
    """graph.aget_state returns a state with empty .values — treat as no checkpoint."""
    session_id = str(uuid.uuid4())
    sm = _make_sm()
    sm.list_sessions.return_value = [
        {"session_id": session_id, "status": "RUNNING"}
    ]
    runner = _make_runner(sm)
    gs = MagicMock()
    gs.values = {}  # empty — treated as no checkpoint
    _wire_graph(runner, gs)

    await runner.rehydrate()

    assert session_id not in runner._active


async def test_rehydrate_tolerates_aget_state_exception():
    """If graph.aget_state raises, the session is skipped — process should not crash."""
    session_id = str(uuid.uuid4())
    sm = _make_sm()
    sm.list_sessions.return_value = [
        {"session_id": session_id, "status": "RUNNING"}
    ]
    runner = _make_runner(sm)
    mock_graph = AsyncMock()
    mock_graph.aget_state.side_effect = RuntimeError("checkpointer unavailable")
    runner._graph = mock_graph

    # Must not raise
    await runner.rehydrate()

    assert session_id not in runner._active


# ── Empty session list ────────────────────────────────────────────────────────

async def test_rehydrate_empty_session_list():
    sm = _make_sm()
    sm.list_sessions.return_value = []
    runner = _make_runner(sm)
    mock_graph = AsyncMock()
    runner._graph = mock_graph

    await runner.rehydrate()

    assert runner._active == {}
    mock_graph.aget_state.assert_not_awaited()


async def test_rehydrate_tolerates_list_sessions_exception():
    """SM.list_sessions() raises — rehydrate logs and returns without crash."""
    sm = _make_sm()
    sm.list_sessions.side_effect = RuntimeError("DB connection lost")
    runner = _make_runner(sm)

    await runner.rehydrate()  # must not raise

    assert runner._active == {}


# ── Already tracked sessions not re-added ────────────────────────────────────

async def test_rehydrate_skips_already_active_session():
    """If a session is already in _active (same process), rehydrate skips it."""
    session_id = str(uuid.uuid4())
    sm = _make_sm()
    sm.list_sessions.return_value = [
        {"session_id": session_id, "status": "PENDING_REVIEW"}
    ]
    runner = _make_runner(sm)
    # Pre-populate _active as if run() was called in this process
    runner._active[session_id] = {"status": "RUNNING", "kio_sequence": ["kio3"]}
    mock_graph = AsyncMock()
    runner._graph = mock_graph

    await runner.rehydrate()

    # Pre-existing entry must not be overwritten
    assert runner._active[session_id]["kio_sequence"] == ["kio3"]
    mock_graph.aget_state.assert_not_awaited()
