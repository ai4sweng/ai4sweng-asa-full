"""Non-blocking advisory channel: KIOs raise advisories, the run reports them."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from apps.orchestrator.src.engine.graph_nodes import make_nodes, normalize_advisories


def test_normalize_advisories_filters_and_caps():
    raw = [
        {"message": "auth endpoint unprotected", "severity": "HIGH", "suggested_kio": "KIO12"},
        {"severity": "info"},  # no message → dropped
        "not a dict",  # dropped
    ] + [{"message": f"m{i}"} for i in range(20)]  # overflow → capped at 10

    out = normalize_advisories(raw, "kio3")

    assert len(out) == 10  # cap enforced
    first = out[0]
    assert first == {
        "source": "kio3",
        "severity": "high",  # lowercased
        "message": "auth endpoint unprotected",
        "suggested_kio": "kio12",  # lowercased
    }


def test_normalize_advisories_non_list():
    assert normalize_advisories(None, "kio3") == []
    assert normalize_advisories("nope", "kio3") == []


@pytest.mark.asyncio
async def test_complete_node_surfaces_advisories():
    bus = MagicMock()
    sm = AsyncMock()
    active = {"s1": {"status": "RUNNING"}}
    nodes = make_nodes(sm, AsyncMock(), AsyncMock(), bus, active)

    advisories = [{"source": "kio3", "severity": "high", "message": "possible auth gap"}]
    state = {
        "session_id": "s1",
        "kio_sequence": ["kio3", "kio8"],
        "advisories": advisories,
    }
    await nodes["complete"](state)

    calls = str(bus.method_calls)
    assert "WORKFLOW_COMPLETED" in calls
    assert "possible auth gap" in calls  # advisory rode along in the event data
