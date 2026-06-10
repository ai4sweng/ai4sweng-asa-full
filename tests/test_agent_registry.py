"""Unit tests for AgentRegistry (agent_registry.py)."""
import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from apps.orchestrator.src.engine.agent_registry import AgentRegistry


@pytest.fixture()
def reg():
    return AgentRegistry()


def _announcement(kio_id: str, host: str = "localhost", port: int = 8012,
                  tasks: list | None = None) -> dict:
    return {
        "agent_id": kio_id,
        "version": "1.0.0",
        "protocol_version": "1",
        "endpoint": {"host": host, "port": port},
        "supported_tasks": tasks if tasks is not None else [{"task_type": kio_id}],
        "hardware_requirements": {},
    }


def _fresh_ts() -> str:
    return datetime.now(timezone.utc).isoformat()


def _stale_ts(seconds_ago: int = 200) -> str:
    return (datetime.now(timezone.utc) - timedelta(seconds=seconds_ago)).isoformat()


# ── handle_announcement ───────────────────────────────────────────────────────

async def test_handle_announcement_registers_agent(reg):
    await reg.handle_announcement(_announcement("kio3"))
    assert "kio3" in reg._agents


async def test_handle_announcement_ignores_missing_agent_id(reg):
    await reg.handle_announcement({"endpoint": {"host": "x", "port": 1}})
    assert reg._agents == {}


async def test_handle_announcement_fires_endpoint_change_cb(reg):
    fired = []
    reg.on_endpoint_change(lambda kio_id: fired.append(kio_id))
    await reg.handle_announcement(_announcement("kio3"))
    assert "kio3" in fired


async def test_reannouncement_same_endpoint_does_not_fire_change_cb(reg):
    fired = []
    reg.on_endpoint_change(lambda kio_id: fired.append(kio_id))
    ann = _announcement("kio3")
    await reg.handle_announcement(ann)
    fired.clear()  # reset after first registration
    await reg.handle_announcement(ann)  # same endpoint
    assert "kio3" not in fired


async def test_endpoint_change_fires_on_port_change(reg):
    fired = []
    reg.on_endpoint_change(lambda kio_id: fired.append(kio_id))
    await reg.handle_announcement(_announcement("kio3", port=8012))
    fired.clear()
    await reg.handle_announcement(_announcement("kio3", port=9999))
    assert "kio3" in fired


# ── get_endpoint ──────────────────────────────────────────────────────────────

async def test_get_endpoint_returns_host_port_for_fresh_agent(reg):
    await reg.handle_announcement(_announcement("kio3", host="kio3-host", port=8020))
    endpoint = reg.get_endpoint("kio3")
    assert endpoint == ("kio3-host", 8020)


async def test_get_endpoint_returns_none_for_unknown_agent(reg):
    assert reg.get_endpoint("kio99") is None


async def test_get_endpoint_returns_none_for_stale_agent(reg):
    await reg.handle_announcement(_announcement("kio3"))
    # Manually set last_seen far in the past
    reg._agents["kio3"]["last_seen"] = _stale_ts(200)

    mock_settings = MagicMock()
    mock_settings.agent_stale_threshold = 90

    with patch("apps.orchestrator.src.engine.agent_registry.get_settings", return_value=mock_settings):
        endpoint = reg.get_endpoint("kio3")
    assert endpoint is None


async def test_get_endpoint_notifies_sm_on_first_stale(reg):
    await reg.handle_announcement(_announcement("kio3"))
    reg._agents["kio3"]["last_seen"] = _stale_ts(200)

    mock_sm = MagicMock()
    mock_settings = MagicMock()
    mock_settings.agent_stale_threshold = 90

    with patch("apps.orchestrator.src.engine.agent_registry.get_settings", return_value=mock_settings):
        with patch("apps.orchestrator.src.engine.orchestrator_state.get_orchestrator_sm",
                   return_value=mock_sm):
            reg.get_endpoint("kio3")

    mock_sm.agent_failure_detected.assert_called_once_with("kio3")


async def test_stale_flag_not_fired_twice(reg):
    await reg.handle_announcement(_announcement("kio3"))
    reg._agents["kio3"]["last_seen"] = _stale_ts(200)
    reg._agents["kio3"]["_stale_notified"] = True  # already notified

    mock_sm = MagicMock()
    mock_settings = MagicMock()
    mock_settings.agent_stale_threshold = 90

    # get_orchestrator_sm is a deferred import inside the function body;
    # patch it on the orchestrator_state module so the local import picks it up.
    with patch("apps.orchestrator.src.engine.agent_registry.get_settings", return_value=mock_settings):
        with patch("apps.orchestrator.src.engine.orchestrator_state.get_orchestrator_sm",
                   return_value=mock_sm):
            reg.get_endpoint("kio3")

    mock_sm.agent_failure_detected.assert_not_called()


# ── is_alive ──────────────────────────────────────────────────────────────────

async def test_is_alive_true_for_fresh_agent(reg):
    await reg.handle_announcement(_announcement("kio3"))

    mock_settings = MagicMock()
    mock_settings.agent_stale_threshold = 90
    with patch("apps.orchestrator.src.engine.agent_registry.get_settings", return_value=mock_settings):
        assert reg.is_alive("kio3") is True


async def test_is_alive_false_for_stale_agent(reg):
    await reg.handle_announcement(_announcement("kio3"))
    reg._agents["kio3"]["last_seen"] = _stale_ts(200)

    mock_settings = MagicMock()
    mock_settings.agent_stale_threshold = 90
    with patch("apps.orchestrator.src.engine.agent_registry.get_settings", return_value=mock_settings):
        assert reg.is_alive("kio3") is False


# ── list_agents ───────────────────────────────────────────────────────────────

async def test_list_agents_returns_all_agents(reg):
    await reg.handle_announcement(_announcement("kio2"))
    await reg.handle_announcement(_announcement("kio3"))

    mock_settings = MagicMock()
    mock_settings.agent_stale_threshold = 90
    with patch("apps.orchestrator.src.engine.agent_registry.get_settings", return_value=mock_settings):
        agents = reg.list_agents()
    kio_ids = [a["kio_id"] for a in agents]
    assert "kio2" in kio_ids
    assert "kio3" in kio_ids


async def test_list_agents_sorted_by_kio_id(reg):
    await reg.handle_announcement(_announcement("kio5"))
    await reg.handle_announcement(_announcement("kio2"))

    mock_settings = MagicMock()
    mock_settings.agent_stale_threshold = 90
    with patch("apps.orchestrator.src.engine.agent_registry.get_settings", return_value=mock_settings):
        agents = reg.list_agents()
    assert agents[0]["kio_id"] == "kio2"
    assert agents[1]["kio_id"] == "kio5"


async def test_list_agents_marks_stale_as_not_alive(reg):
    await reg.handle_announcement(_announcement("kio3"))
    reg._agents["kio3"]["last_seen"] = _stale_ts(200)

    mock_settings = MagicMock()
    mock_settings.agent_stale_threshold = 90
    with patch("apps.orchestrator.src.engine.agent_registry.get_settings", return_value=mock_settings):
        agents = reg.list_agents()
    assert agents[0]["alive"] is False


# ── Concurrent announcements ──────────────────────────────────────────────────

async def test_concurrent_announcements_do_not_corrupt_state(reg):
    await asyncio.gather(*[
        reg.handle_announcement(_announcement(f"kio{i}")) for i in range(2, 8)
    ])
    assert len(reg._agents) == 6
