"""Tests for confidence-gated HITL: low routing confidence pauses for plan review."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from apps.orchestrator.src.engine.graph_nodes import make_nodes
from apps.orchestrator.src.services.lm_client import LmEngineClient, _FALLBACK_SEQUENCE


def _nodes():
    return make_nodes(AsyncMock(), AsyncMock(), AsyncMock(), MagicMock(), {})


def test_should_review_plan_routes_to_review_when_confidence_low():
    nodes = _nodes()
    assert nodes["should_review_plan"]({"plan_confidence_low": True}) == "plan_review"


def test_should_review_plan_runs_kio_when_confident():
    nodes = _nodes()
    assert nodes["should_review_plan"]({"plan_confidence_low": False}) == "run_kio"


def test_should_review_plan_defaults_to_run_kio_when_flag_absent():
    nodes = _nodes()
    assert nodes["should_review_plan"]({}) == "run_kio"


@pytest.mark.asyncio
async def test_plan_workflow_signals_fallback_on_failure():
    client = LmEngineClient()
    client._client = MagicMock()
    client._client.post = AsyncMock(side_effect=RuntimeError("LM down"))
    client._client.aclose = AsyncMock()

    kios, reasoning, used_fallback, rejected = await client.plan_workflow("fix the bug", "sess-1")

    assert kios == list(_FALLBACK_SEQUENCE)
    assert used_fallback is True
    assert "Fallback" in reasoning
    await client.close()
