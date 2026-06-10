"""Unit tests for OrchestratorStateMachine (orchestrator_state.py)."""
import pytest

from apps.orchestrator.src.engine.orchestrator_state import (
    OrchestratorState,
    OrchestratorStateMachine,
)


@pytest.fixture()
def sm():
    return OrchestratorStateMachine()


# ── Initial state ─────────────────────────────────────────────────────────────

def test_initial_state_is_initializing(sm):
    assert sm.state == OrchestratorState.INITIALIZING


def test_initializing_does_not_accept_workflows(sm):
    assert sm.accepts_workflows() is False


# ── INITIALIZING → IDLE via first agent registration ─────────────────────────

def test_first_agent_registered_transitions_to_idle(sm):
    sm.agent_registered("kio2")
    assert sm.state == OrchestratorState.IDLE


def test_accepts_workflows_after_idle(sm):
    sm.agent_registered("kio2")
    assert sm.accepts_workflows() is True


# ── IDLE → ACTIVE ─────────────────────────────────────────────────────────────

def test_workflow_submitted_from_idle_transitions_to_active(sm):
    sm.agent_registered("kio2")
    sm.workflow_submitted()
    assert sm.state == OrchestratorState.ACTIVE


def test_active_accepts_workflows(sm):
    sm.agent_registered("kio2")
    sm.workflow_submitted()
    assert sm.accepts_workflows() is True


# ── ACTIVE → IDLE when last workflow finishes ─────────────────────────────────

def test_all_workflows_done_transitions_to_idle(sm):
    sm.agent_registered("kio2")
    sm.workflow_submitted()
    sm.workflow_finished()
    assert sm.state == OrchestratorState.IDLE


def test_partial_workflow_finish_stays_active(sm):
    sm.agent_registered("kio2")
    sm.workflow_submitted()
    sm.workflow_submitted()
    sm.workflow_finished()
    assert sm.state == OrchestratorState.ACTIVE


def test_workflow_counter_never_goes_negative(sm):
    sm.agent_registered("kio2")
    sm.workflow_submitted()
    sm.workflow_finished()
    sm.workflow_finished()  # extra call — should not crash
    assert sm._active_workflows == 0


# ── ACTIVE → DEGRADED on agent failure ───────────────────────────────────────

def test_agent_failure_from_active_transitions_to_degraded(sm):
    sm.agent_registered("kio2")
    sm.workflow_submitted()
    sm.agent_failure_detected("kio3")
    assert sm.state == OrchestratorState.DEGRADED


def test_degraded_still_accepts_workflows(sm):
    sm.agent_registered("kio2")
    sm.workflow_submitted()
    sm.agent_failure_detected("kio3")
    assert sm.accepts_workflows() is True


# ── DEGRADED → ACTIVE/IDLE when agent recovers ───────────────────────────────

def test_single_agent_recovery_from_degraded_goes_active(sm):
    sm.agent_registered("kio2")
    sm.workflow_submitted()
    sm.agent_failure_detected("kio3")
    sm.agent_recovered("kio3")
    assert sm.state == OrchestratorState.ACTIVE


def test_multiple_failures_need_all_recovered(sm):
    sm.agent_registered("kio2")
    sm.workflow_submitted()
    sm.agent_failure_detected("kio3")
    sm.agent_failure_detected("kio4")
    sm.agent_recovered("kio3")
    assert sm.state == OrchestratorState.DEGRADED  # kio4 still degraded
    sm.agent_recovered("kio4")
    assert sm.state == OrchestratorState.ACTIVE


def test_recovery_goes_idle_when_no_workflows(sm):
    sm.agent_registered("kio2")
    # No workflow submitted — agent failure while idle doesn't flip to DEGRADED
    # (DEGRADED only triggered from ACTIVE)
    sm.agent_failure_detected("kio3")
    assert sm.state == OrchestratorState.IDLE


# ── DEGRADED → RECOVERY on critical failure ───────────────────────────────────

def test_critical_failure_enters_recovery(sm):
    sm.agent_registered("kio2")
    sm.workflow_submitted()
    sm.agent_failure_detected("kio3")
    sm.critical_failure("all agents down")
    assert sm.state == OrchestratorState.RECOVERY


def test_recovery_rejects_workflows(sm):
    sm.agent_registered("kio2")
    sm.critical_failure("test")
    assert sm.accepts_workflows() is False


def test_system_restored_from_recovery_goes_idle(sm):
    sm.agent_registered("kio2")
    sm.critical_failure("test")
    sm.system_restored()
    assert sm.state == OrchestratorState.IDLE


def test_system_restored_from_recovery_goes_active_when_workflows_pending(sm):
    sm.agent_registered("kio2")
    sm.workflow_submitted()
    sm.critical_failure("test")
    sm.system_restored()
    assert sm.state == OrchestratorState.ACTIVE


# ── SHUTDOWN ──────────────────────────────────────────────────────────────────

def test_shutdown_is_terminal(sm):
    sm.agent_registered("kio2")
    sm.shutdown()
    assert sm.state == OrchestratorState.SHUTDOWN
    assert sm.accepts_workflows() is False


def test_shutdown_not_overridden_by_agent_registration(sm):
    sm.agent_registered("kio2")
    sm.shutdown()
    sm.agent_registered("kio3")
    assert sm.state == OrchestratorState.SHUTDOWN


# ── Callbacks ─────────────────────────────────────────────────────────────────

def test_state_change_callback_fires(sm):
    fired = []
    sm.on_state_change(lambda s: fired.append(s))
    sm.agent_registered("kio2")
    assert OrchestratorState.IDLE in fired


def test_callback_exception_does_not_crash_sm(sm):
    def bad_cb(s):
        raise RuntimeError("cb boom")

    sm.on_state_change(bad_cb)
    sm.agent_registered("kio2")  # should not raise
    assert sm.state == OrchestratorState.IDLE


# ── Summary dict ─────────────────────────────────────────────────────────────

def test_summary_contains_required_keys(sm):
    s = sm.summary()
    assert "state" in s
    assert "active_workflows" in s
    assert "degraded_agents" in s
    assert "accepts_workflows" in s


def test_summary_degraded_agents_sorted(sm):
    sm.agent_registered("kio2")
    sm.workflow_submitted()
    sm.agent_failure_detected("kio5")
    sm.agent_failure_detected("kio3")
    s = sm.summary()
    assert s["degraded_agents"] == ["kio3", "kio5"]
