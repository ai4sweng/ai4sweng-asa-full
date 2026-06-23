"""Deterministic capability bidding: rank online agents by fit to the task."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from apps.orchestrator.src.engine.graph_nodes import make_nodes
from apps.orchestrator.src.services.capability_bidder import Bid, format_bids, rank_bids

_REGISTRY = "apps.orchestrator.src.engine.agent_registry.get_agent_registry"


def _agent(kio_id, desc, alive=True):
    return {"kio_id": kio_id, "alive": alive, "supported_tasks": [{"description": desc}]}


def _registry_with(agents):
    reg = MagicMock()
    reg.list_agents = MagicMock(return_value=agents)
    return reg


def test_rank_bids_scores_and_orders_by_fit():
    agents = [
        _agent("kio12", "security OWASP vulnerability audit"),
        _agent("kio4", "generate pytest unit tests"),
        _agent("kio9", "generate brand new source modules"),
    ]
    with patch(_REGISTRY, return_value=_registry_with(agents)):
        bids = rank_bids("run a security audit for OWASP vulnerabilities")

    assert bids[0].kio_id == "kio12"  # best fit ranks first
    assert bids[0].score > 0
    assert "security" in bids[0].why
    assert "kio4" not in [b.kio_id for b in bids]  # no token overlap → no bid


def test_rank_bids_ignores_offline_agents():
    agents = [_agent("kio12", "security audit owasp", alive=False)]
    with patch(_REGISTRY, return_value=_registry_with(agents)):
        assert rank_bids("security audit owasp") == []


def test_rank_bids_empty_when_registry_unavailable():
    with patch(_REGISTRY, side_effect=RuntimeError("no NATS")):
        assert rank_bids("anything meaningful here") == []


def test_format_bids_empty_and_rendered():
    assert format_bids([]) == ""
    block = format_bids([Bid(kio_id="kio12", score=0.5, why="security, owasp")])
    assert "Capability bids" in block
    assert "kio12 (0.50): security, owasp" in block


@pytest.mark.asyncio
async def test_plan_node_feeds_bids_to_planner():
    lm = AsyncMock()
    lm.assess_clarification = AsyncMock(return_value=None)
    lm.plan_workflow = AsyncMock(return_value=(["kio12"], "reason", False, []))
    lm.critique_plan = AsyncMock(return_value=None)
    bus = MagicMock()
    nodes = make_nodes(AsyncMock(), AsyncMock(), lm, bus, {"s1": {"status": "QUEUED"}})

    agents = [_agent("kio12", "security owasp vulnerability audit")]
    state = {
        "session_id": "s1",
        "description": "please run a security owasp audit",
        "working_directory": "",
        "kio_sequence": [],
        "initial_context": {},
        "clarify_attempts": 0,
    }
    with patch(_REGISTRY, return_value=_registry_with(agents)):
        await nodes["plan"](state)

    assert "CAPABILITY_BIDS" in str(bus.method_calls)
    kwargs = lm.plan_workflow.call_args.kwargs
    assert kwargs.get("bids")
    assert "kio12" in kwargs["bids"]
