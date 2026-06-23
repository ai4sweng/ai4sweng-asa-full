"""Plan-stage eval harness — scoring logic, exercised against a stub planner.

These tests do NOT call a live model; they verify the harness scores
clarification / include / exclude / report behavior correctly. The live run is
`scripts/run_plan_stage_eval.py`.
"""

import pytest

from apps.orchestrator.src.eval.plan_stage_eval import (
    PlanCase,
    evaluate_plan_stage,
)
from apps.orchestrator.src.services.lm_client import Clarification


class StubPlanner:
    """Returns canned clarify/plan results keyed by description."""

    def __init__(self, clarify: dict[str, bool], plans: dict[str, list[str]]):
        self._clarify = clarify
        self._plans = plans

    async def assess_clarification(self, description, session_id):
        return Clarification("what do you mean?", []) if self._clarify.get(description) else None

    async def plan_workflow(self, description, session_id, *, intent=None, **_):
        return list(self._plans.get(description, [])), "reason", False, []


_OFFTOPIC = "I want you to cook fish"
_CODEGEN = "build me a CLI"
_AUDIT = "audit this repo for security and report"


def _dataset():
    return [
        PlanCase(_OFFTOPIC, expect_clarification=True, note="offtopic"),
        PlanCase(_CODEGEN, expect_clarification=False, must_include=("kio9",),
                 must_exclude=("kio3",), note="codegen"),
        PlanCase(_AUDIT, must_include=("kio3", "kio12"), require_report=True, note="audit"),
    ]


@pytest.mark.asyncio
async def test_all_correct_passes():
    planner = StubPlanner(
        clarify={_OFFTOPIC: True, _CODEGEN: False, _AUDIT: False},
        plans={_CODEGEN: ["kio9"], _AUDIT: ["kio3", "kio12", "kio8"]},
    )
    report = await evaluate_plan_stage(planner, _dataset())
    assert report.pass_rate == 1.0
    assert report.n == 3


@pytest.mark.asyncio
async def test_dropped_report_fails_report_check():
    planner = StubPlanner(
        clarify={_OFFTOPIC: True, _CODEGEN: False, _AUDIT: False},
        plans={_CODEGEN: ["kio9"], _AUDIT: ["kio3", "kio12"]},  # kio8 dropped
    )
    report = await evaluate_plan_stage(planner, _dataset())
    audit = next(r for r in report.results if r.case.note == "audit")
    assert audit.report_ok is False
    assert audit.passed is False
    assert "report (kio8) missing" in audit.failure_reasons()


@pytest.mark.asyncio
async def test_missing_clarification_and_forbidden_agent_fail():
    planner = StubPlanner(
        clarify={_OFFTOPIC: False, _CODEGEN: False, _AUDIT: False},  # offtopic NOT clarified
        plans={_CODEGEN: ["kio9", "kio3"], _AUDIT: ["kio3", "kio12", "kio8"]},  # kio3 forbidden
    )
    report = await evaluate_plan_stage(planner, _dataset())
    offtopic = next(r for r in report.results if r.case.note == "offtopic")
    codegen = next(r for r in report.results if r.case.note == "codegen")
    assert offtopic.clarification_ok is False
    assert codegen.exclude_ok is False
    assert report.pass_rate == pytest.approx(1 / 3)
