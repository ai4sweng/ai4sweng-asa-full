"""Workflow execution engine — LangGraph-backed.

Replaces the old for-loop runner with a compiled StateGraph:
  • plan → run_kio → [hitl →] advance → [loop] → complete
  • HITL pauses the graph via interrupt(); approve() resumes with Command(resume=…).
  • AsyncPostgresSaver checkpointer — graph state survives process restarts.
    Falls back to MemorySaver if PostgreSQL is unavailable.

Graph lazy-initialisation
--------------------------
The compiled graph must be built AFTER the event loop is running (so the async
checkpointer can be awaited).  ``_get_graph()`` initialises on first call and is
protected by an asyncio.Lock to guard against concurrent first calls.
"""
from __future__ import annotations

import asyncio
from typing import Any

from loguru import logger

from .event_bus import EventBus, WorkflowEvent
from .workflow_graph import build_workflow_graph
from ..services.kio_client import KioClient
from ..services.lm_client import LmEngineClient
from ..services.session_client import SessionClient


class WorkflowRunner:
    """Orchestrates KIO pipelines via LangGraph."""

    def __init__(
        self,
        session_client: SessionClient,
        kio_client: KioClient,
        lm_client: LmEngineClient,
        event_bus: EventBus,
    ) -> None:
        self._sm = session_client
        self._kio = kio_client
        self._lm = lm_client
        self._bus = event_bus
        # In-process status cache (durable state lives in PostgreSQL via SM)
        self._active: dict[str, dict[str, Any]] = {}
        # Per-session lock — prevents double-invocation on the same session
        # (e.g. double-click approve, or concurrent run() calls with same session).
        # Different sessions use different locks so they remain fully concurrent.
        self._session_locks: dict[str, asyncio.Lock] = {}
        # Compiled LangGraph — initialised lazily on first run() call
        self._graph = None
        self._graph_lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _get_session_lock(self, session_id: str) -> asyncio.Lock:
        """Return (creating if needed) the per-session asyncio.Lock."""
        if session_id not in self._session_locks:
            self._session_locks[session_id] = asyncio.Lock()
        return self._session_locks[session_id]

    # ------------------------------------------------------------------
    # Graph lazy initialisation
    # ------------------------------------------------------------------

    async def _get_graph(self):
        """Return the compiled graph, building it on first call (thread-safe)."""
        if self._graph is not None:
            return self._graph
        async with self._graph_lock:
            if self._graph is not None:  # double-checked locking
                return self._graph
            from .checkpointer import get_checkpointer
            checkpointer = await get_checkpointer()
            self._graph = build_workflow_graph(
                self._sm, self._kio, self._lm, self._bus, self._active,
                checkpointer=checkpointer,
            )
            logger.info(
                "WorkflowGraph compiled with checkpointer={}",
                type(checkpointer).__name__,
            )
        return self._graph

    # ------------------------------------------------------------------
    # Public API (same surface as old runner — router unchanged)
    # ------------------------------------------------------------------

    async def run(
        self,
        *,
        workflow_id: str,
        kio_sequence: list[str],
        hitl_after: list[str] | None,
        owner: str,
        description: str = "",
        working_directory: str = "",
        initial_context: dict | None = None,
        timeout_seconds: int | None = None,
        priority: int = 5,
    ) -> str:
        """Create a session and start async graph execution. Returns session_id."""
        import uuid as _uuid
        from datetime import datetime, timezone, timedelta
        correlation_id = str(_uuid.uuid4())

        session = await self._sm.create_session(owner=owner, workflow_id=workflow_id)
        session_id: str = session["session_id"]

        # Compute deadline for TimeoutMonitor
        deadline = None
        if timeout_seconds:
            deadline = (datetime.now(timezone.utc) + timedelta(seconds=timeout_seconds)).isoformat()

        self._active[session_id] = {
            "workflow_id": workflow_id,
            "kio_sequence": kio_sequence or [],
            "hitl_after": hitl_after or [],
            "description": description,
            "working_directory": working_directory,
            "owner": owner,
            "progress": 0,
            "total": len(kio_sequence),
            "active_kio": None,
            "pending_checkpoint_id": None,
            "artifacts": [],
            "status": "QUEUED",  # Slide 18: initial task state
            "correlation_id": correlation_id,
            "deadline": deadline,
            "timeout_seconds": timeout_seconds,
            "priority": priority,
        }
        self._emit("SESSION_CREATED", session_id,
                   f"Session {session_id[:8]}… queued",
                   {"workflow_id": workflow_id, "description": description,
                    "status": "QUEUED", "timeout_seconds": timeout_seconds})

        # Notify orchestrator state machine
        try:
            from .orchestrator_state import get_orchestrator_sm
            get_orchestrator_sm().workflow_submitted()
        except Exception:
            pass

        initial_input = {
            "session_id": session_id,
            "workflow_id": workflow_id,
            "owner": owner,
            "description": description,
            "working_directory": working_directory,
            "kio_sequence": kio_sequence or [],
            "hitl_after": hitl_after or [],
            "current_step": 0,
            "last_result": {},
            "artifacts": [],
            "feedback": "",
            "status": "QUEUED",
            "error": None,
            "pending_checkpoint_id": None,
            "initial_context": initial_context or {},
            "llm_provider_override": "",
            "llm_retry_pending": False,
            "correlation_id": correlation_id,
            "timeout_seconds": timeout_seconds,
            "priority": priority,
        }
        config = {"configurable": {"thread_id": session_id}}
        asyncio.create_task(self._run_graph(session_id, initial_input, config))
        return session_id

    def get_state(self, session_id: str) -> dict[str, Any] | None:
        """Return in-process runtime state, or None if session is unknown."""
        return self._active.get(session_id)

    async def rehydrate(self) -> None:
        """Rebuild _active from PostgreSQL after a process restart.

        Queries SessionManager for non-terminal sessions, then loads each
        session's graph state from the LangGraph PostgreSQL checkpointer so
        approve() and get_state() work correctly without a new run() call.
        """
        try:
            all_sessions = await self._sm.list_sessions()
        except Exception as exc:
            logger.warning("[rehydrate] Could not list sessions: {}", exc)
            return

        non_terminal = [
            s for s in all_sessions
            if s.get("status") in (
                "ACTIVE", "PENDING_REVIEW",
                "QUEUED", "VALIDATING", "READY", "RUNNING", "BLOCKED",
            )
        ]
        if not non_terminal:
            logger.info("[rehydrate] No in-flight sessions to restore")
            return

        graph = await self._get_graph()
        restored = 0

        for session in non_terminal:
            session_id = session["session_id"]
            if session_id in self._active:
                continue  # already tracked (e.g. started in this process lifetime)

            config = {"configurable": {"thread_id": session_id}}
            try:
                gs = await graph.aget_state(config)
            except Exception as exc:
                logger.warning("[rehydrate] Cannot load graph state for {}: {}", session_id[:8], exc)
                continue

            if not gs or not gs.values:
                logger.debug("[rehydrate] No checkpoint found for session {}", session_id[:8])
                continue

            sv: dict[str, Any] = gs.values
            status = session.get("status", "ACTIVE")

            # pending_checkpoint_id lives in the graph state (set by hitl_node)
            pending_ckpt = sv.get("pending_checkpoint_id")

            self._active[session_id] = {
                "workflow_id": sv.get("workflow_id", session.get("workflow_id", "")),
                "kio_sequence": sv.get("kio_sequence", []),
                "hitl_after": sv.get("hitl_after", []),
                "description": sv.get("description", ""),
                "working_directory": sv.get("working_directory", ""),
                "progress": sv.get("current_step", 0),
                "total": len(sv.get("kio_sequence", [])),
                "active_kio": None,
                "pending_checkpoint_id": pending_ckpt,
                "artifacts": sv.get("artifacts", []),
                "status": status,
            }
            # Ensure a lock exists so approve() can resume safely
            self._get_session_lock(session_id)
            restored += 1
            logger.info(
                "[rehydrate] Restored session {} status={} ckpt={}",
                session_id[:8], status, pending_ckpt,
            )

        logger.info("[rehydrate] Restored {}/{} in-flight session(s)", restored, len(non_terminal))

    async def approve(
        self, session_id: str, *, actor: str, feedback: str
    ) -> dict[str, Any] | None:
        """Resume the interrupted graph after human approval.

        Resolves the HITL checkpoint in Session Manager, then feeds the human
        feedback into the graph via Command(resume=feedback).
        """
        state = self._active.get(session_id)
        if not state:
            return None

        # Pop atomically so a second concurrent approve() call sees None immediately
        # and returns None, preventing a double-resume race.
        checkpoint_id = state.pop("pending_checkpoint_id", None)
        if not checkpoint_id:
            return None

        state["status"] = "RUNNING"
        await self._sm.resolve_checkpoint(
            session_id, checkpoint_id, action="APPROVED", actor=actor, feedback=feedback
        )
        self._emit("HITL_APPROVED", session_id,
                   "Approved — resuming workflow.",
                   {"checkpoint_id": checkpoint_id, "actor": actor})

        config = {"configurable": {"thread_id": session_id}}
        asyncio.create_task(self._resume_graph(session_id, feedback, config))
        return {"status": "APPROVED", "checkpoint_id": checkpoint_id}

    # ------------------------------------------------------------------
    # Internal graph execution
    # ------------------------------------------------------------------

    async def _run_graph(
        self, session_id: str, initial_input: dict, config: dict
    ) -> None:
        """Invoke the graph from the start; handle interrupt vs completion.

        The per-session lock ensures only one ainvoke() runs at a time for a
        given session — different sessions run fully concurrently.
        """
        lock = self._get_session_lock(session_id)
        if lock.locked():
            logger.warning("[{}] _run_graph called while session already running — skipped", session_id[:8])
            return
        async with lock:
            try:
                graph = await self._get_graph()
                await graph.ainvoke(initial_input, config=config)
                await self._check_graph_state(session_id, config)
            except Exception as exc:
                await self._handle_failure(session_id, exc)

    async def _resume_graph(
        self, session_id: str, feedback: str, config: dict
    ) -> None:
        """Resume a previously interrupted graph with human feedback.

        The per-session lock prevents a double-approve race (e.g. two concurrent
        approve() calls for the same session would run the graph twice).
        """
        from langgraph.types import Command  # local import — keeps cold-start fast
        lock = self._get_session_lock(session_id)
        if lock.locked():
            logger.warning("[{}] _resume_graph called while session already running — skipped", session_id[:8])
            return
        async with lock:
            try:
                graph = await self._get_graph()
                await graph.ainvoke(
                    Command(resume=feedback or "approved"), config=config
                )
                await self._check_graph_state(session_id, config)
            except Exception as exc:
                await self._handle_failure(session_id, exc)

    async def _check_graph_state(self, session_id: str, config: dict) -> None:
        """After ainvoke returns, detect whether the graph is paused or done."""
        graph = await self._get_graph()
        gs = await graph.aget_state(config)
        if not gs.next:
            return  # completed — complete_node already emitted the event

        # Still paused at a pending interrupt (e.g. second HITL in revision flow)
        for task in gs.tasks:
            for intr in task.interrupts:
                iv = intr.value if isinstance(intr.value, dict) else {}
                state = self._active.get(session_id, {})
                state["status"] = "PENDING_REVIEW"
                state["pending_checkpoint_id"] = iv.get("checkpoint_id")
                break

    async def cancel(self, session_id: str, reason: str = "CANCELLED") -> None:
        """Cancel or time-out an in-flight session.

        reason=="TASK_TIMEOUT" — emits WORKFLOW_TIMEOUT then WORKFLOW_FAILED;
                                  sets status TIMEOUT then FAILED (Slide 18).
        reason=="CANCELLED"    — emits WORKFLOW_CANCELLED; sets status CANCELLED.
        """
        state = self._active.get(session_id)
        if not state:
            return

        if reason == "TASK_TIMEOUT":
            logger.warning("[{}] Session timed out", session_id[:8])
            state["status"] = "TIMEOUT"
            try:
                await self._sm.update_status(session_id, "TIMEOUT")
            except Exception:
                pass
            self._emit("WORKFLOW_TIMEOUT", session_id,
                       "Workflow timed out — deadline exceeded",
                       {"error": "TASK_TIMEOUT", "status": "TIMEOUT"})
            # Transition immediately to FAILED (terminal)
            state["status"] = "FAILED"
            try:
                await self._sm.update_status(session_id, "FAILED")
            except Exception:
                pass
            self._emit("WORKFLOW_FAILED", session_id,
                       "Workflow FAILED after timeout",
                       {"error": reason})
        else:
            logger.warning("[{}] Session cancelled: {}", session_id[:8], reason)
            state["status"] = "CANCELLED"
            try:
                await self._sm.update_status(session_id, "CANCELLED")
            except Exception:
                pass
            self._emit("WORKFLOW_CANCELLED", session_id,
                       "Workflow cancelled",
                       {"reason": reason, "status": "CANCELLED"})

        self._cleanup_session(session_id)
        try:
            from .orchestrator_state import get_orchestrator_sm
            get_orchestrator_sm().workflow_finished()
        except Exception:
            pass

    async def _handle_failure(self, session_id: str, exc: Exception) -> None:
        logger.exception("Workflow {} failed: {}", session_id, exc)
        state = self._active.get(session_id, {})
        state["status"] = "FAILED"
        try:
            await self._sm.update_status(session_id, "FAILED")
        except Exception:
            pass
        self._emit("WORKFLOW_FAILED", session_id,
                   f"Workflow FAILED: {exc}", {"error": str(exc)})

        # Phase 6: trigger compensation for any artifacts registered before the failure
        artifact_ids: list[str] = state.get("artifacts", [])
        if artifact_ids:
            try:
                from .compensation_engine import get_compensation_engine
                engine = get_compensation_engine(
                    session_client=self._sm,
                    emit_fn=self._emit,
                )
                await engine.compensate(session_id, artifact_ids)
            except Exception as comp_exc:
                logger.error("[compensation] unexpected error for {}: {}", session_id[:8], comp_exc)

        # Release in-process state; the durable record lives in PostgreSQL.
        self._cleanup_session(session_id)
        try:
            from .orchestrator_state import get_orchestrator_sm
            get_orchestrator_sm().workflow_finished()
        except Exception:
            pass

    def _cleanup_session(self, session_id: str) -> None:
        """Remove in-process session state after COMPLETED or FAILED.

        Called at terminal states to prevent unbounded growth of _active and
        _session_locks over the orchestrator's lifetime.
        """
        self._active.pop(session_id, None)
        self._session_locks.pop(session_id, None)

    def _emit(
        self,
        event_type: str,
        session_id: str,
        message: str,
        data: dict | None = None,
    ) -> None:
        owner = self._active.get(session_id, {}).get("owner", "")
        self._bus.publish(WorkflowEvent(event_type, session_id, message, data, owner=owner))
        logger.info("[{}] {}", event_type, message)


# ------------------------------------------------------------------
# Singleton
# ------------------------------------------------------------------

_runner: WorkflowRunner | None = None
_runner_lock = asyncio.Lock()


async def init_runner() -> WorkflowRunner:
    """Create and return the process-wide WorkflowRunner singleton (async-safe)."""
    global _runner
    if _runner is not None:
        return _runner
    async with _runner_lock:
        if _runner is not None:
            return _runner
        from ..services.kio_client import get_kio_client
        from ..services.lm_client import get_lm_client
        from ..services.session_client import get_session_client
        from .event_bus import get_event_bus
        _runner = WorkflowRunner(
            session_client=get_session_client(),
            kio_client=get_kio_client(),
            lm_client=get_lm_client(),
            event_bus=get_event_bus(),
        )
        # Restore any sessions that were in-flight before this process started
        await _runner.rehydrate()
    return _runner


def get_runner() -> WorkflowRunner:
    """Return the process-wide WorkflowRunner singleton.

    Must be called after ``init_runner()`` has completed (i.e. inside a request
    handler, not at import time).  Raises RuntimeError if called before init.
    """
    if _runner is None:
        raise RuntimeError(
            "WorkflowRunner not initialised — lifespan must call await init_runner() first"
        )
    return _runner
