"""Intake-gate clarification: vague/off-topic tasks ask the user before planning.

Instead of hallucinating a pipeline for an unclear request (e.g. "I want you to
cook fish"), the planner asks what the user means — with up to four suggested
options plus a free-text "Other" — then loops back to re-plan with the answer.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from apps.orchestrator.src.engine.graph_nodes import make_nodes
from apps.orchestrator.src.services.lm_client import Clarification, LmEngineClient


def _nodes(lm=None, sm=None, active=None):
    return make_nodes(
        sm or AsyncMock(),
        AsyncMock(),
        lm or AsyncMock(),
        MagicMock(),
        active if active is not None else {},
    )


# --- router -----------------------------------------------------------------


def test_route_after_plan_prefers_clarification():
    nodes = _nodes()
    state = {"needs_clarification": True, "plan_confidence_low": True}
    assert nodes["route_after_plan"](state) == "plan_clarify"


def test_route_after_plan_falls_through_to_review():
    nodes = _nodes()
    assert nodes["route_after_plan"]({"plan_confidence_low": True}) == "plan_review"


def test_route_after_plan_runs_kio_when_clear_and_confident():
    nodes = _nodes()
    assert nodes["route_after_plan"]({}) == "run_kio"


# --- plan_node gate ---------------------------------------------------------


@pytest.mark.asyncio
async def test_plan_node_requests_clarification_for_vague_task():
    lm = AsyncMock()
    lm.assess_clarification = AsyncMock(
        return_value=Clarification(question="What do you mean?", options=["A", "B"])
    )
    nodes = _nodes(lm=lm, active={"s1": {"status": "QUEUED"}})
    state = {
        "session_id": "s1",
        "description": "I want you to cook fish",
        "working_directory": "",
        "kio_sequence": [],
        "initial_context": {},
        "clarify_attempts": 0,
    }

    update = await nodes["plan"](state)

    assert update["needs_clarification"] is True
    assert update["clarification"]["question"] == "What do you mean?"
    assert update["clarification"]["options"] == ["A", "B"]
    # The gate short-circuits — it never reaches LM pipeline planning.
    lm.plan_workflow.assert_not_called()


@pytest.mark.asyncio
async def test_plan_node_stops_clarifying_after_max_attempts():
    lm = AsyncMock()
    lm.assess_clarification = AsyncMock(
        return_value=Clarification(question="?", options=[])
    )
    lm.plan_workflow = AsyncMock(return_value=(["kio3", "kio5"], "reason", False, []))
    lm.critique_plan = AsyncMock(return_value=None)
    nodes = _nodes(lm=lm, active={"s1": {"status": "QUEUED"}})
    state = {
        "session_id": "s1",
        "description": "still vague",
        "working_directory": "",
        "kio_sequence": [],
        "initial_context": {},
        "clarify_attempts": 2,  # cap reached → plan anyway
    }

    update = await nodes["plan"](state)

    assert not update.get("needs_clarification")
    lm.assess_clarification.assert_not_called()
    lm.plan_workflow.assert_awaited_once()


# --- assess_clarification ---------------------------------------------------


@pytest.mark.asyncio
async def test_assess_clarification_empty_description_is_deterministic():
    client = LmEngineClient()
    client._client = MagicMock()
    client._client.post = AsyncMock()  # must NOT be called for the short-circuit
    client._client.aclose = AsyncMock()

    clar = await client.assess_clarification("  ", "sess-1")

    assert isinstance(clar, Clarification)
    assert clar.options  # offers concrete suggestions
    client._client.post.assert_not_called()
    await client.close()


@pytest.mark.asyncio
async def test_assess_clarification_parses_llm_request():
    client = LmEngineClient()
    client._client = MagicMock()
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.json = MagicMock(
        return_value={
            "content": (
                '{"needs_clarification": true, "question": "What do you mean?", '
                '"options": ["Analyze a repo", "Generate code"]}'
            )
        }
    )
    client._client.post = AsyncMock(return_value=resp)
    client._client.aclose = AsyncMock()

    clar = await client.assess_clarification("cook fish", "sess-1")

    assert isinstance(clar, Clarification)
    assert clar.question == "What do you mean?"
    assert clar.options == ["Analyze a repo", "Generate code"]
    await client.close()


@pytest.mark.asyncio
async def test_assess_clarification_clear_task_returns_none():
    client = LmEngineClient()
    client._client = MagicMock()
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.json = MagicMock(return_value={"content": '{"needs_clarification": false}'})
    client._client.post = AsyncMock(return_value=resp)
    client._client.aclose = AsyncMock()

    assert await client.assess_clarification("find bugs in my repo", "sess-1") is None
    await client.close()


@pytest.mark.asyncio
async def test_assess_clarification_fails_open_on_error():
    client = LmEngineClient()
    client._client = MagicMock()
    client._client.post = AsyncMock(side_effect=RuntimeError("LM down"))
    client._client.aclose = AsyncMock()

    # Detector failure must never block an otherwise-valid request.
    assert await client.assess_clarification("find bugs in my repo", "sess-1") is None
    await client.close()
