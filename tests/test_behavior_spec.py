"""Tests for the behavior-spec eval: pure reflection layer + scorecard wiring.

The pure layer is deterministic, so we assert real repair guarantees against the
shipped spec. The live layers are exercised with a stub planner so the scorecard
aggregation (skip handling, spec_closeness) is covered without a model.
"""

import pytest

from apps.orchestrator.src.eval.behavior_spec import (
    REFLECTION_CASES,
    Scorecard,
    run_full_eval,
)
from apps.orchestrator.src.eval.reflection_eval import (
    ReflectionCase,
    evaluate_reflection,
)


def test_shipped_reflection_spec_passes():
    """The deterministic gate must satisfy every case in the shipped spec."""
    report = evaluate_reflection(REFLECTION_CASES)
    assert report.pass_rate == 1.0, report.summary()


def test_reflection_drops_step_with_no_inputs():
    report = evaluate_reflection(
        [ReflectionCase("x", proposed=["kio3"], has_repo=False, expect=[])]
    )
    assert report.results[0].repaired == []


def test_reflection_autoinserts_producer_chain():
    # kio7 needs a patch → inserts kio6 → which needs bugs → inserts kio5.
    report = evaluate_reflection(
        [ReflectionCase("x", proposed=["kio7"], has_code=True, must_include=("kio5", "kio6"))]
    )
    assert report.pass_rate == 1.0
    assert set(report.results[0].repaired) >= {"kio5", "kio6", "kio7"}


def test_reflection_caps_and_keeps_report():
    proposed = [f"kio{n}" for n in range(2, 13)] + ["kio8"]
    report = evaluate_reflection(
        [ReflectionCase("x", proposed=proposed, has_repo=True, has_code=True, require_report=True)]
    )
    assert report.results[0].report_ok
    assert len(report.results[0].repaired) <= 12


# --- scorecard aggregation ---


class _StubPlanner:
    """Minimal planner: clarifies anything containing 'cook', else routes to kio9."""

    async def assess_clarification(self, description, session_id):
        return object() if "cook" in description else None

    async def plan_workflow(self, description, session_id, *, intent=None):
        return ["kio9"], "stub", False, []


@pytest.mark.asyncio
async def test_live_layers_skipped_without_planner():
    card, _raw = await run_full_eval(planner=None)
    by_name = {c.name: c for c in card.categories}
    assert by_name["reflection"].skipped is False
    assert by_name["routing"].skipped is True
    assert by_name["clarify"].skipped is True
    # spec_closeness ignores skipped categories → driven by the pure layer only.
    assert card.spec_closeness is not None


@pytest.mark.asyncio
async def test_scorecard_runs_live_layers_with_planner():
    card, raw = await run_full_eval(planner=_StubPlanner())
    names = {c.name for c in card.categories}
    assert {"reflection", "routing", "clarify", "long_prompt"} <= names
    assert not any(c.skipped for c in card.categories)
    assert "routing" in raw


def test_scorecard_closeness_excludes_skipped():
    from apps.orchestrator.src.eval.behavior_spec import CategoryScore

    card = Scorecard(
        categories=[
            CategoryScore("reflection", "pure", n=4, passed=4),
            CategoryScore("routing", "live", n=4, passed=0, skipped=True),
        ]
    )
    # Only the scored pure category counts: 4/4.
    assert card.spec_closeness == 1.0
