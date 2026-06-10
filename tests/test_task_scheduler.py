"""Unit tests for TaskScheduler (task_scheduler.py)."""
import pytest
from unittest.mock import MagicMock, patch
from datetime import datetime, timezone

from apps.orchestrator.src.engine.task_scheduler import TaskScheduler


@pytest.fixture()
def scheduler():
    return TaskScheduler()


def _make_registry(agents: dict | None = None):
    """Build a minimal mock AgentRegistry."""
    reg = MagicMock()
    reg._agents = agents or {}
    return reg


def _alive_agent(kio_id: str, supported_tasks: list | None = None):
    """Return a dict as stored in AgentRegistry._agents."""
    return {
        "kio_id": kio_id,
        "host": "localhost",
        "port": 8012,
        "supported_tasks": supported_tasks if supported_tasks is not None else [{"task_type": kio_id}],
        "last_seen": datetime.now(timezone.utc).isoformat(),
    }


# ── Empty registry (HTTP mode) ────────────────────────────────────────────────

def test_empty_registry_skips_capability_check(scheduler):
    reg = _make_registry({})
    result = scheduler.schedule(["kio2", "kio3"], 0, reg)
    assert result == "kio2"


def test_empty_registry_any_step(scheduler):
    reg = _make_registry({})
    result = scheduler.schedule(["kio2", "kio3", "kio8"], 2, reg)
    assert result == "kio8"


# ── Step out of range ─────────────────────────────────────────────────────────

def test_step_out_of_range_returns_none(scheduler):
    reg = _make_registry({})
    result = scheduler.schedule(["kio2"], 5, reg)
    assert result is None


# ── Registry has agents — alive and capable ───────────────────────────────────

def test_alive_capable_agent_dispatched(scheduler):
    reg = _make_registry({"kio3": _alive_agent("kio3")})
    reg.is_alive.return_value = True
    result = scheduler.schedule(["kio3"], 0, reg)
    assert result == "kio3"


def test_alive_empty_supported_tasks_is_capable(scheduler):
    """An agent with no supported_tasks declared is treated as universally capable."""
    reg = _make_registry({"kio3": _alive_agent("kio3", supported_tasks=[])})
    reg.is_alive.return_value = True
    result = scheduler.schedule(["kio3"], 0, reg)
    assert result == "kio3"


# ── Registry has agents — stale/dead ─────────────────────────────────────────

def test_stale_agent_returns_none(scheduler):
    reg = _make_registry({"kio3": _alive_agent("kio3")})
    reg.is_alive.return_value = False
    result = scheduler.schedule(["kio3"], 0, reg)
    assert result is None


def test_unregistered_kio_in_non_empty_registry_returns_none(scheduler):
    reg = _make_registry({"kio2": _alive_agent("kio2")})
    reg.is_alive.return_value = False  # kio3 not in registry
    result = scheduler.schedule(["kio3"], 0, reg)
    assert result is None


# ── Capability mismatch ───────────────────────────────────────────────────────

def test_alive_but_wrong_task_type_returns_none(scheduler):
    agent_data = _alive_agent("kio3", supported_tasks=[{"task_type": "kio99"}])
    reg = _make_registry({"kio3": agent_data})
    reg.is_alive.return_value = True
    result = scheduler.schedule(["kio3"], 0, reg)
    assert result is None


def test_correct_task_type_among_multiple_passes(scheduler):
    agent_data = _alive_agent("kio3", supported_tasks=[
        {"task_type": "something_else"},
        {"task_type": "kio3"},
    ])
    reg = _make_registry({"kio3": agent_data})
    reg.is_alive.return_value = True
    result = scheduler.schedule(["kio3"], 0, reg)
    assert result == "kio3"


# ── Multi-step pipeline ───────────────────────────────────────────────────────

def test_schedule_second_step(scheduler):
    reg = _make_registry({"kio3": _alive_agent("kio3")})
    reg.is_alive.return_value = True
    result = scheduler.schedule(["kio2", "kio3", "kio8"], 1, reg)
    assert result == "kio3"


# ── find_capable_agent ────────────────────────────────────────────────────────

def test_find_capable_agent_returns_matching_agent(scheduler):
    reg = MagicMock()
    reg.list_agents.return_value = [
        {"kio_id": "kio3", "alive": True, "supported_tasks": [{"task_type": "kio3"}]},
    ]
    result = scheduler.find_capable_agent("kio3", reg)
    assert result == "kio3"


def test_find_capable_agent_skips_dead_agents(scheduler):
    reg = MagicMock()
    reg.list_agents.return_value = [
        {"kio_id": "kio3", "alive": False, "supported_tasks": [{"task_type": "kio3"}]},
    ]
    result = scheduler.find_capable_agent("kio3", reg)
    assert result is None


def test_find_capable_agent_returns_none_when_no_match(scheduler):
    reg = MagicMock()
    reg.list_agents.return_value = [
        {"kio_id": "kio3", "alive": True, "supported_tasks": [{"task_type": "kio99"}]},
    ]
    result = scheduler.find_capable_agent("kio3", reg)
    assert result is None


def test_find_capable_agent_empty_registry(scheduler):
    reg = MagicMock()
    reg.list_agents.return_value = []
    result = scheduler.find_capable_agent("kio3", reg)
    assert result is None
