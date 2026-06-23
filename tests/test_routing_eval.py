"""Tests for the routing eval harness (metric logic, with a stub router)."""

import pytest

from apps.orchestrator.src.eval.routing_eval import (
    RoutingCase,
    evaluate,
)


def _router_from(table: dict[str, tuple[list[str], bool]]):
    async def router(description: str) -> tuple[list[str], bool]:
        return table[description]

    return router


@pytest.mark.asyncio
async def test_all_correct_scores_perfect():
    dataset = [
        RoutingCase("a", ["kio9"]),
        RoutingCase("b", ["kio3", "kio5"], has_repo=True),
    ]
    router = _router_from({"a": (["kio9"], False), "b": (["kio3", "kio5"], False)})
    report = await evaluate(router, dataset)
    assert report.n == 2
    assert report.exact_match == 1.0
    assert report.first_step_acc == 1.0
    assert report.fallback_rate == 0.0


@pytest.mark.asyncio
async def test_first_step_credited_even_when_sequence_differs():
    dataset = [RoutingCase("a", ["kio3", "kio5"], has_repo=True)]
    # Right first step, wrong tail → exact miss but first-step hit.
    router = _router_from({"a": (["kio3", "kio4", "kio5"], False)})
    report = await evaluate(router, dataset)
    assert report.exact_match == 0.0
    assert report.first_step_acc == 1.0


@pytest.mark.asyncio
async def test_fallback_rate_counts_low_confidence():
    dataset = [RoutingCase("a", ["kio9"]), RoutingCase("b", ["kio9"])]
    router = _router_from({"a": (["kio9"], False), "b": (["kio3", "kio5"], True)})
    report = await evaluate(router, dataset)
    assert report.fallback_rate == 0.5


@pytest.mark.asyncio
async def test_reflection_is_applied_to_predicted_route():
    # Router proposes patching with no bug step; reflection inserts kio5,
    # making the effective route match the expected one.
    dataset = [RoutingCase("a", ["kio3", "kio5", "kio6"], has_repo=True)]
    router = _router_from({"a": (["kio3", "kio6"], False)})
    report = await evaluate(router, dataset, apply_reflection=True)
    assert report.results[0].predicted == ["kio3", "kio5", "kio6"]
    assert report.exact_match == 1.0


@pytest.mark.asyncio
async def test_no_reflection_scores_raw_output():
    dataset = [RoutingCase("a", ["kio3", "kio5", "kio6"], has_repo=True)]
    router = _router_from({"a": (["kio3", "kio6"], False)})
    report = await evaluate(router, dataset, apply_reflection=False)
    assert report.results[0].predicted == ["kio3", "kio6"]
    assert report.exact_match == 0.0


@pytest.mark.asyncio
async def test_summary_renders_misses():
    dataset = [RoutingCase("build a thing", ["kio9"])]
    router = _router_from({"build a thing": (["kio3", "kio5"], True)})
    report = await evaluate(router, dataset)
    text = report.summary()
    assert "fallback_rate" in text
    assert "[fallback]" in text
