"""The planner surfaces its dynamic few-shot choice as a PLAN_FEWSHOT event."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from apps.orchestrator.src.engine.graph_nodes import make_nodes


@pytest.mark.asyncio
async def test_plan_node_emits_fewshot_event_and_passes_examples():
    bus = MagicMock()
    sm = AsyncMock()
    kio = AsyncMock()
    lm = AsyncMock()
    lm.plan_workflow = AsyncMock(return_value=(["kio3", "kio5"], "reason", False, []))
    # Clear, actionable task → intake gate does not request clarification.
    lm.assess_clarification = AsyncMock(return_value=None)
    lm.critique_plan = AsyncMock(return_value=None)  # critic approves silently
    active = {"s1": {"status": "QUEUED"}}

    nodes = make_nodes(sm, kio, lm, bus, active)
    state = {
        "session_id": "s1",
        "workflow_id": "w",
        "owner": "o",
        "description": "find bugs in this repository",
        "working_directory": "",
        "kio_sequence": [],  # empty → LLM planning branch
        "hitl_after": [],
        "current_step": 0,
        "initial_context": {},
        "artifacts": [],
    }

    await nodes["plan"](state)

    emitted = str(bus.method_calls)
    # The event fired and named the most-similar exemplar for this task.
    assert "PLAN_FEWSHOT" in emitted
    assert "find security bugs in this repository" in emitted
    # The retrieved exemplars were handed to the planner as the few-shot block.
    assert "examples" in str(lm.plan_workflow.call_args)
