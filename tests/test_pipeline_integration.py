"""Integration tests for the WorkflowRunner pipeline — Phase 9.2.

Uses MemorySaver (no PostgreSQL) and AsyncMock service clients — no
infrastructure needed.  Exercises the full LangGraph path:
  plan → run_kio (×N) → complete

Detection strategy
------------------
Terminal state is detected by polling sm.update_status.await_args_list
(complete_node calls update_status(session_id, "COMPLETED")).  This is
reliable because asyncio.sleep(0.05) in the polling loop yields to the
graph task each iteration.

The AgentRegistry is empty in tests → TaskScheduler skips capability
check and dispatches kio_sequence[step] directly.
"""
from __future__ import annotations

import asyncio
import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from apps.orchestrator.src.engine.event_bus import EventBus
from apps.orchestrator.src.engine.workflow_runner import WorkflowRunner


# ── Factory helpers ──────────────────────────────────────────────────────────

def _make_sm() -> AsyncMock:
    sm = AsyncMock()
    sm.create_session.return_value = {"session_id": str(uuid.uuid4())}
    sm.update_status.return_value = {}
    sm.update_progress.return_value = {}
    sm.register_artifact.return_value = {"artifact_id": str(uuid.uuid4())}
    sm.create_hitl_checkpoint.return_value = {"checkpoint_id": str(uuid.uuid4())}
    sm.resolve_checkpoint.return_value = {}
    sm.get_artifacts.return_value = []
    return sm


def _make_kio(responses: list[dict[str, Any]] | None = None) -> AsyncMock:
    """KIO mock that returns sequential JOB_RESULT payloads.

    Includes a yield point (asyncio.sleep(0)) so the event loop can run
    other tasks (e.g. the test's polling loop) between KIO steps.
    """
    kio = AsyncMock()
    defaults = responses or []
    call_count = 0

    async def _execute(*args, **kwargs):
        nonlocal call_count
        await asyncio.sleep(0)  # cooperative yield — lets polling loop run
        if call_count < len(defaults):
            resp = defaults[call_count]
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
    """Build a runner with a MemorySaver-backed graph (no PostgreSQL)."""
    from langgraph.checkpoint.memory import MemorySaver
    from apps.orchestrator.src.engine.workflow_graph import build_workflow_graph

    bus = EventBus()
    runner = WorkflowRunner(session_client=sm, kio_client=kio, lm_client=AsyncMock(), event_bus=bus)
    runner._graph = build_workflow_graph(
        sm, kio, AsyncMock(), bus, runner._active,
        checkpointer=MemorySaver(),
    )
    return runner, bus


async def _wait_sm_status(
    sm: AsyncMock,
    session_id: str,
    target: set[str],
    timeout: float = 5.0,
) -> str | None:
    """Poll sm.update_status calls until one matches (session_id, target_status)."""
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        for call in sm.update_status.await_args_list:
            args = call.args
            if len(args) >= 2 and args[0] == session_id and args[1] in target:
                return args[1]
        await asyncio.sleep(0.05)
    return None


async def _wait_active_status(
    runner: WorkflowRunner,
    session_id: str,
    target: set[str],
    timeout: float = 5.0,
) -> dict:
    """Poll runner._active until status matches one of the targets."""
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        state = runner._active.get(session_id)
        if state and state.get("status") in target:
            return dict(state)
        await asyncio.sleep(0.05)
    return {}


# ── Happy-path pipeline tests ─────────────────────────────────────────────────

async def test_two_step_pipeline_completes():
    sm = _make_sm()
    runner, _ = _make_runner(sm, _make_kio())

    session_id = await runner.run(
        workflow_id=str(uuid.uuid4()),
        kio_sequence=["kio2", "kio8"],
        hitl_after=[],
        owner="test",
        description="integration test",
    )

    status = await _wait_sm_status(sm, session_id, {"COMPLETED", "FAILED"})
    assert status == "COMPLETED"


async def test_three_step_pipeline_calls_all_kios():
    sm = _make_sm()
    kio = _make_kio()
    runner, _ = _make_runner(sm, kio)

    session_id = await runner.run(
        workflow_id=str(uuid.uuid4()),
        kio_sequence=["kio2", "kio3", "kio8"],
        hitl_after=[],
        owner="test",
    )

    await _wait_sm_status(sm, session_id, {"COMPLETED", "FAILED"})
    assert kio.execute.call_count == 3


async def test_single_step_pipeline_completes():
    sm = _make_sm()
    runner, _ = _make_runner(sm, _make_kio())

    session_id = await runner.run(
        workflow_id=str(uuid.uuid4()),
        kio_sequence=["kio8"],
        hitl_after=[],
        owner="test",
    )

    status = await _wait_sm_status(sm, session_id, {"COMPLETED", "FAILED"})
    assert status == "COMPLETED"


async def test_pipeline_registers_artifacts_per_step():
    sm = _make_sm()
    runner, _ = _make_runner(sm, _make_kio())

    session_id = await runner.run(
        workflow_id=str(uuid.uuid4()),
        kio_sequence=["kio2", "kio3"],
        hitl_after=[],
        owner="test",
    )

    await _wait_sm_status(sm, session_id, {"COMPLETED", "FAILED"})
    assert sm.register_artifact.call_count == 2


# ── Session Manager interactions ─────────────────────────────────────────────

async def test_run_creates_session_with_owner():
    sm = _make_sm()
    runner, _ = _make_runner(sm, _make_kio())

    await runner.run(
        workflow_id="wf-test",
        kio_sequence=["kio2"],
        hitl_after=[],
        owner="alice",
    )

    sm.create_session.assert_awaited_once()
    kwargs = sm.create_session.await_args.kwargs
    assert kwargs["owner"] == "alice"


async def test_update_status_completed_called():
    sm = _make_sm()
    runner, _ = _make_runner(sm, _make_kio())

    session_id = await runner.run(
        workflow_id=str(uuid.uuid4()),
        kio_sequence=["kio2"],
        hitl_after=[],
        owner="test",
    )

    status = await _wait_sm_status(sm, session_id, {"COMPLETED", "FAILED"})
    assert status == "COMPLETED"


# ── Failure path ─────────────────────────────────────────────────────────────

async def test_kio_exception_transitions_to_failed():
    """KIO exception → retry (1×) → no fallback → FAILED.

    Two targeted patches (neither touches global asyncio.sleep):
    - graph_nodes.get_settings: llm_provider_fallback="" so retry exhaustion raises
    - RetryManager.wait_and_increment singleton: increments attempt instantly (no sleep)
    """
    from apps.orchestrator.src.engine.retry_manager import get_retry_manager

    sm = _make_sm()
    kio = AsyncMock()

    async def _boom(*args, **kwargs):
        await asyncio.sleep(0)
        raise RuntimeError("kio exploded")

    kio.execute.side_effect = _boom
    runner, _ = _make_runner(sm, kio)

    mock_cfg = MagicMock()
    mock_cfg.llm_provider_fallback = ""
    mock_cfg.llm_provider = "test"
    mock_cfg.kio_client_timeout = 60

    rm = get_retry_manager()

    async def _instant_wait(session_id):
        rs = rm._state.get(session_id)
        if rs:
            rs.attempts += 1
            return rs.attempts
        return 0

    with patch("apps.orchestrator.src.engine.graph_nodes.get_settings", return_value=mock_cfg), \
         patch.object(rm, "wait_and_increment", new=_instant_wait):

        session_id = await runner.run(
            workflow_id=str(uuid.uuid4()),
            kio_sequence=["kio2"],
            hitl_after=[],
            owner="test",
        )

        status = await _wait_sm_status(sm, session_id, {"FAILED", "COMPENSATED"}, timeout=5.0)

    assert status in ("FAILED", "COMPENSATED")


# ── get_state ─────────────────────────────────────────────────────────────────

def test_get_state_returns_none_for_unknown_session():
    sm = _make_sm()
    runner, _ = _make_runner(sm, _make_kio())
    assert runner.get_state("no-such-session") is None


async def test_get_state_returns_status_immediately_after_run():
    sm = _make_sm()
    runner, _ = _make_runner(sm, _make_kio())

    session_id = await runner.run(
        workflow_id=str(uuid.uuid4()),
        kio_sequence=["kio2"],
        hitl_after=[],
        owner="test",
    )

    # State exists right after run() — graph task hasn't completed yet
    state = runner.get_state(session_id)
    assert state is not None
    assert "status" in state
