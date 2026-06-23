"""Confidence-gated plan critic: a second opinion on the proposed pipeline.

Runs only on CONFIDENT plans (a fallback plan already escalates to plan_review).
The critic may auto-adjust the pipeline, or flag 'uncertain' to escalate to the
existing plan_review HITL.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from apps.orchestrator.src.engine.graph_nodes import make_nodes
from apps.orchestrator.src.services.lm_client import LmEngineClient, PlanCritique


def _nodes(lm):
    return make_nodes(AsyncMock(), AsyncMock(), lm, MagicMock(), {"s1": {"status": "QUEUED"}})


def _planning_state(description="add tests to my repo"):
    return {
        "session_id": "s1",
        "description": description,
        "working_directory": "",  # no recon — keep these FS-free
        "kio_sequence": [],
        "initial_context": {},
        "clarify_attempts": 0,
    }


def _lm(seq, used_fallback=False):
    lm = AsyncMock()
    lm.assess_clarification = AsyncMock(return_value=None)
    lm.plan_workflow = AsyncMock(return_value=(seq, "reason", used_fallback, []))
    return lm


# --- critique_plan parsing --------------------------------------------------


def _client_returning(content: str) -> LmEngineClient:
    client = LmEngineClient()
    client._client = MagicMock()
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.json = MagicMock(return_value={"content": content})
    client._client.post = AsyncMock(return_value=resp)
    client._client.aclose = AsyncMock()
    return client


@pytest.mark.asyncio
async def test_critique_plan_adjust_filters_invalid_ids():
    client = _client_returning(
        '{"verdict": "adjust", "revised_sequence": ["kio3", "kio4", "bogus", "kio4"], '
        '"reason": "needs tests"}'
    )
    crit = await client.critique_plan("task", ["kio3"], None, "sess")
    assert crit.verdict == "adjust"
    assert crit.revised_sequence == ["kio3", "kio4"]  # invalid + dup dropped
    await client.close()


@pytest.mark.asyncio
async def test_critique_plan_uncertain_passthrough():
    client = _client_returning('{"verdict": "uncertain", "reason": "ambiguous"}')
    crit = await client.critique_plan("task", ["kio9"], None, "sess")
    assert crit.verdict == "uncertain"
    assert crit.revised_sequence is None
    await client.close()


@pytest.mark.asyncio
async def test_critique_plan_fails_open_on_error():
    client = LmEngineClient()
    client._client = MagicMock()
    client._client.post = AsyncMock(side_effect=RuntimeError("LM down"))
    client._client.aclose = AsyncMock()
    assert await client.critique_plan("task", ["kio3"], None, "sess") is None
    await client.close()


# --- plan_node integration --------------------------------------------------


@pytest.mark.asyncio
async def test_plan_node_applies_critic_adjustment():
    lm = _lm(["kio3", "kio5"])
    lm.critique_plan = AsyncMock(
        return_value=PlanCritique(
            verdict="adjust", revised_sequence=["kio3", "kio4", "kio5"], reason="no tests"
        )
    )
    nodes = make_nodes(AsyncMock(), AsyncMock(), lm, (bus := MagicMock()), {"s1": {"status": "QUEUED"}})

    update = await nodes["plan"](_planning_state())

    assert "kio4" in update["kio_sequence"]  # critic's addition survived
    assert "PLAN_CRITIC" in str(bus.method_calls)


@pytest.mark.asyncio
async def test_plan_node_escalates_on_uncertain_critic():
    lm = _lm(["kio9"])
    lm.critique_plan = AsyncMock(
        return_value=PlanCritique(verdict="uncertain", revised_sequence=None, reason="ambiguous")
    )
    nodes = _nodes(lm)

    update = await nodes["plan"](_planning_state())

    # Uncertain critic gates the existing low-confidence plan_review HITL.
    assert update["plan_confidence_low"] is True


@pytest.mark.asyncio
async def test_plan_node_skips_critic_on_fallback_plan():
    lm = _lm(["kio3", "kio5"], used_fallback=True)
    lm.critique_plan = AsyncMock()
    nodes = _nodes(lm)

    await nodes["plan"](_planning_state())

    lm.critique_plan.assert_not_called()  # fallback already escalates
