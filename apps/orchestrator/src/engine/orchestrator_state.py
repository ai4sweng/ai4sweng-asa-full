"""Orchestrator-level state machine — Slide 16 of the interface spec.

States
------
  INITIALIZING  → startup, waiting for first agent to register
  IDLE          → running but no active workflows
  ACTIVE        → at least one workflow is executing
  DEGRADED      → one or more agents are stale/failed, workflows still proceeding
  RECOVERY      → critical failure, halting new dispatches until restored
  SHUTDOWN      → graceful shutdown in progress

Transitions (from → event → to)
--------------------------------
  INITIALIZING  | first agent registered        | IDLE
  IDLE          | workflow submitted             | ACTIVE
  ACTIVE        | all workflows done             | IDLE
  ACTIVE        | agent failure detected         | DEGRADED
  DEGRADED      | agent recovered               | ACTIVE (or IDLE if 0 workflows)
  DEGRADED      | critical failure (all agents) | RECOVERY
  RECOVERY      | system restored               | IDLE
  ANY           | shutdown signal               | SHUTDOWN
"""
from __future__ import annotations

from enum import Enum
from typing import Callable

from loguru import logger


class OrchestratorState(str, Enum):
    INITIALIZING = "INITIALIZING"
    IDLE = "IDLE"
    ACTIVE = "ACTIVE"
    DEGRADED = "DEGRADED"
    RECOVERY = "RECOVERY"
    SHUTDOWN = "SHUTDOWN"


class OrchestratorStateMachine:
    def __init__(self) -> None:
        self._state = OrchestratorState.INITIALIZING
        self._active_workflows: int = 0
        self._degraded_agents: set[str] = set()
        self._state_change_cbs: list[Callable[[OrchestratorState], None]] = []

    # ------------------------------------------------------------------
    # Subscription
    # ------------------------------------------------------------------

    def on_state_change(self, cb: Callable[[OrchestratorState], None]) -> None:
        """Register a callback invoked on every state transition."""
        self._state_change_cbs.append(cb)

    # ------------------------------------------------------------------
    # Events (called by other components)
    # ------------------------------------------------------------------

    def agent_registered(self, kio_id: str) -> None:
        """Called by AgentRegistry when a new capability announcement arrives."""
        self._degraded_agents.discard(kio_id)  # recovery side-effect
        if self._state == OrchestratorState.INITIALIZING:
            target = OrchestratorState.ACTIVE if self._active_workflows > 0 else OrchestratorState.IDLE
            self._transition(target)
        elif self._state == OrchestratorState.DEGRADED and not self._degraded_agents:
            target = OrchestratorState.ACTIVE if self._active_workflows > 0 else OrchestratorState.IDLE
            self._transition(target)

    def workflow_submitted(self) -> None:
        """Called by WorkflowRunner when a new workflow starts."""
        self._active_workflows += 1
        if self._state in (OrchestratorState.IDLE,):
            self._transition(OrchestratorState.ACTIVE)

    def workflow_finished(self) -> None:
        """Called by WorkflowRunner when a workflow reaches COMPLETED or FAILED."""
        self._active_workflows = max(0, self._active_workflows - 1)
        if self._active_workflows == 0 and self._state == OrchestratorState.ACTIVE:
            self._transition(OrchestratorState.IDLE)

    def agent_failure_detected(self, kio_id: str) -> None:
        """Called by AgentRegistry when a KIO goes stale (no heartbeat > threshold)."""
        self._degraded_agents.add(kio_id)
        if self._state == OrchestratorState.ACTIVE:
            self._transition(OrchestratorState.DEGRADED)
            logger.warning("[orchestrator_sm] {} went stale → DEGRADED", kio_id)

    def agent_recovered(self, kio_id: str) -> None:
        """Called when a previously-stale KIO announces again."""
        self._degraded_agents.discard(kio_id)
        if self._state == OrchestratorState.DEGRADED and not self._degraded_agents:
            target = OrchestratorState.ACTIVE if self._active_workflows > 0 else OrchestratorState.IDLE
            self._transition(target)
            logger.info("[orchestrator_sm] {} recovered → {}", kio_id, target.value)

    def critical_failure(self, reason: str = "") -> None:
        """Trigger RECOVERY mode — halts new workflow dispatches."""
        if self._state not in (OrchestratorState.RECOVERY, OrchestratorState.SHUTDOWN):
            logger.error("[orchestrator_sm] Critical failure: {} → RECOVERY", reason)
            self._transition(OrchestratorState.RECOVERY)

    def system_restored(self) -> None:
        """Called after manual or automatic recovery from RECOVERY state."""
        if self._state == OrchestratorState.RECOVERY:
            target = OrchestratorState.ACTIVE if self._active_workflows > 0 else OrchestratorState.IDLE
            self._transition(target)

    def shutdown(self) -> None:
        """Signal graceful shutdown — terminal state."""
        self._transition(OrchestratorState.SHUTDOWN)

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    @property
    def state(self) -> OrchestratorState:
        return self._state

    def accepts_workflows(self) -> bool:
        """Return False when the orchestrator should reject new workflow submissions."""
        return self._state not in (OrchestratorState.RECOVERY, OrchestratorState.SHUTDOWN,
                                   OrchestratorState.INITIALIZING)

    def summary(self) -> dict:
        return {
            "state": self._state.value,
            "active_workflows": self._active_workflows,
            "degraded_agents": sorted(self._degraded_agents),
            "accepts_workflows": self.accepts_workflows(),
        }

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _transition(self, new_state: OrchestratorState) -> None:
        if new_state == self._state:
            return
        logger.info(
            "[orchestrator_sm] {} → {}  (workflows={} degraded={})",
            self._state.value, new_state.value,
            self._active_workflows, list(self._degraded_agents),
        )
        self._state = new_state
        for cb in self._state_change_cbs:
            try:
                cb(new_state)
            except Exception:
                pass


# ------------------------------------------------------------------
# Singleton
# ------------------------------------------------------------------

_sm: OrchestratorStateMachine | None = None


def get_orchestrator_sm() -> OrchestratorStateMachine:
    global _sm
    if _sm is None:
        _sm = OrchestratorStateMachine()
    return _sm
