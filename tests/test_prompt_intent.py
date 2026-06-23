"""Deterministic intent extraction from the user's prompt."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from apps.orchestrator.src.engine.graph_nodes import make_nodes
from apps.orchestrator.src.services.prompt_intent import extract_intent, format_intent


def test_intent_detects_security_and_high_risk():
    intent = extract_intent("run an OWASP audit", has_code=True, has_repo=True)
    assert intent.task_type == "security"
    assert intent.risk_level == "high"


def test_intent_security_priority_over_bug_fix():
    # "fix the security hole" → the higher-stakes 'security' framing wins.
    assert extract_intent("fix the security hole", has_code=True, has_repo=True).task_type == "security"


def test_intent_code_generation_and_normal_risk():
    intent = extract_intent("build a new CLI tool", has_code=False, has_repo=False)
    assert intent.task_type == "code_generation"
    assert intent.risk_level == "normal"


def test_intent_unknown_for_vague_text():
    assert extract_intent("do the thing", has_code=False, has_repo=False).task_type == "unknown"


def test_intent_high_risk_from_security_sensitive_repo():
    intent = extract_intent("review this code", has_code=True, has_repo=True, security_sensitive=True)
    assert intent.risk_level == "high"


def test_format_intent_block():
    block = format_intent(extract_intent("optimize the model", has_code=True, has_repo=True))
    assert "Task intent" in block
    assert "type: optimization" in block
    assert "existing code available: true" in block


@pytest.mark.asyncio
async def test_plan_node_emits_intent_and_passes_to_planner():
    lm = AsyncMock()
    lm.assess_clarification = AsyncMock(return_value=None)
    lm.plan_workflow = AsyncMock(return_value=(["kio12"], "reason", False, []))
    lm.critique_plan = AsyncMock(return_value=None)
    bus = MagicMock()
    nodes = make_nodes(AsyncMock(), AsyncMock(), lm, bus, {"s1": {"status": "QUEUED"}})

    state = {
        "session_id": "s1",
        "description": "run a security audit",
        "working_directory": "",
        "kio_sequence": [],
        "initial_context": {},
        "clarify_attempts": 0,
    }
    await nodes["plan"](state)

    assert "PLAN_INTENT" in str(bus.method_calls)
    intent_arg = lm.plan_workflow.call_args.kwargs.get("intent")
    assert intent_arg and "type: security" in intent_arg
