"""Routing evaluation harness — measures planner routing quality.

Given a labelled dataset of ``(description → expected kio_sequence)`` cases and a
*router* callable, this computes:

  • exact_match    — fraction where the (reflected) sequence equals the expected one
  • first_step_acc — fraction where the first KIO matches expected[0]
  • fallback_rate  — fraction where the planner fell back (low routing confidence)

The router is injected, so the same harness runs against the live LM Engine
(``LmEngineClient.plan_workflow``), a mock, or any candidate planner — which is
what makes routing changes measurable and regressions catchable.

By default the harness applies *process reflection* to each raw plan, so it
measures the *effective* route the orchestrator would actually run, not just the
planner's raw guess.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Awaitable, Callable

from ..engine.process_reflection import validate_and_repair_plan

# A router maps a task description to (kio_sequence, used_fallback).
Router = Callable[[str], Awaitable[tuple[list[str], bool]]]


@dataclass
class RoutingCase:
    description: str
    expected: list[str]
    has_repo: bool = False
    has_code: bool = False
    note: str = ""


@dataclass
class CaseResult:
    case: RoutingCase
    predicted: list[str]
    used_fallback: bool
    exact_match: bool
    first_step_match: bool


@dataclass
class EvalReport:
    results: list[CaseResult] = field(default_factory=list)

    @property
    def n(self) -> int:
        return len(self.results)

    def _frac(self, pred: Callable[[CaseResult], bool]) -> float:
        return (sum(1 for r in self.results if pred(r)) / self.n) if self.n else 0.0

    @property
    def exact_match(self) -> float:
        return self._frac(lambda r: r.exact_match)

    @property
    def first_step_acc(self) -> float:
        return self._frac(lambda r: r.first_step_match)

    @property
    def fallback_rate(self) -> float:
        return self._frac(lambda r: r.used_fallback)

    def summary(self) -> str:
        lines = [
            f"Routing eval — {self.n} cases",
            f"  exact_match    : {self.exact_match:6.1%}",
            f"  first_step_acc : {self.first_step_acc:6.1%}",
            f"  fallback_rate  : {self.fallback_rate:6.1%}",
        ]
        misses = [r for r in self.results if not r.exact_match]
        if misses:
            lines.append("  misses:")
            for r in misses:
                tag = " [fallback]" if r.used_fallback else ""
                lines.append(
                    f"    - {r.case.description[:48]!r}{tag}\n"
                    f"        expected={r.case.expected} got={r.predicted}"
                )
        return "\n".join(lines)


async def evaluate(
    router: Router,
    dataset: list[RoutingCase],
    *,
    apply_reflection: bool = True,
) -> EvalReport:
    """Run ``router`` over ``dataset`` and score the results."""
    results: list[CaseResult] = []
    for case in dataset:
        seq, used_fallback = await router(case.description)
        if apply_reflection:
            repaired = validate_and_repair_plan(
                seq, has_repo=case.has_repo, has_code=case.has_code
            )
            seq = repaired.sequence or seq
        first_match = bool(seq) and bool(case.expected) and seq[0] == case.expected[0]
        results.append(
            CaseResult(
                case=case,
                predicted=seq,
                used_fallback=used_fallback,
                exact_match=(seq == case.expected),
                first_step_match=first_match,
            )
        )
    return EvalReport(results=results)


# A small, representative starter dataset.  Extend with real traffic over time.
DEFAULT_DATASET: list[RoutingCase] = [
    RoutingCase("build me a tool that converts CSV to JSON", ["kio9"], note="codegen"),
    RoutingCase("write a python function that reverses a string", ["kio9"], note="codegen"),
    RoutingCase(
        "I need an application to find the protein from a list of foods",
        ["kio9"],
        note="codegen, no code",
    ),
    RoutingCase(
        "find security bugs in this repository",
        ["kio3", "kio5"],
        has_repo=True,
        note="analyze existing repo",
    ),
    RoutingCase(
        "review the code in this repo and tell me what's wrong",
        ["kio3", "kio5"],
        has_repo=True,
        note="analyze existing repo",
    ),
    RoutingCase(
        "fix the bugs in this repository and re-run the tests",
        ["kio3", "kio5", "kio6", "kio7"],
        has_repo=True,
        note="full fix loop",
    ),
    RoutingCase(
        "check this code snippet for bugs",
        ["kio5"],
        has_code=True,
        note="bug detect on snippet",
    ),
]
