"""Reflection evaluation — pure, deterministic checks for the repair stage.

Unlike ``routing_eval`` / ``plan_stage_eval`` (which call the live planner), this
layer scores the *deterministic* part of the pipeline: given a planner's raw
``proposed`` sequence and the inputs available (a repo on disk / a code snippet),
the orchestrator runs two LLM-free transforms before dispatch —

  1. sanitize  — :func:`lm_client._normalize_kio_list`: drop invalid/duplicate
     KIO ids and cap length, preserving the report (kio8) in the final slot.
  2. repair    — :func:`process_reflection.validate_and_repair_plan`: drop steps
     whose inputs cannot exist, and auto-insert safe producers (kio5 before a
     patch, kio6 before a re-test).

Because both transforms are pure, this layer has no model dependency: it always
runs (offline, in CI) and is fully reproducible — it measures exactly the
guarantees the deterministic gate is *supposed* to enforce, independent of how
good the model's raw guess was.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..engine.process_reflection import validate_and_repair_plan
from ..services.lm_client import _VALID_KIOS, _normalize_kio_list


@dataclass
class ReflectionCase:
    """One deterministic repair expectation.

    Provide the planner's raw ``proposed`` sequence plus the available inputs,
    then assert the repaired result.  Use ``expect`` for an exact sequence, or
    the looser ``must_*`` sets when only a structural guarantee matters.
    """

    description: str
    proposed: list[str]
    has_repo: bool = False
    has_code: bool = False
    expect: list[str] | None = None  # exact repaired sequence (strongest assertion)
    must_include: tuple[str, ...] = ()  # auto-inserted producers, e.g. kio5 before kio6
    must_exclude: tuple[str, ...] = ()  # steps that must be dropped (no inputs)
    report_requested: bool = False  # user asked for a report → kio8 must be present
    require_report: bool = False  # assertion: kio8 must be in the repaired plan
    note: str = ""


@dataclass
class ReflectionCaseResult:
    case: ReflectionCase
    repaired: list[str]
    exact_ok: bool
    include_ok: bool
    exclude_ok: bool
    report_ok: bool

    @property
    def passed(self) -> bool:
        return self.exact_ok and self.include_ok and self.exclude_ok and self.report_ok

    def failure_reasons(self) -> list[str]:
        out: list[str] = []
        if not self.exact_ok:
            out.append(f"expected={self.case.expect} got={self.repaired}")
        if not self.include_ok:
            missing = sorted(set(self.case.must_include) - set(self.repaired))
            out.append(f"missing auto-insert: {missing}")
        if not self.exclude_ok:
            present = sorted(set(self.case.must_exclude) & set(self.repaired))
            out.append(f"not dropped: {present}")
        if not self.report_ok:
            out.append("report (kio8) lost to cap")
        return out


@dataclass
class ReflectionReport:
    results: list[ReflectionCaseResult] = field(default_factory=list)

    @property
    def n(self) -> int:
        return len(self.results)

    @property
    def pass_rate(self) -> float:
        return (sum(1 for r in self.results if r.passed) / self.n) if self.n else 0.0

    def summary(self) -> str:
        passed = sum(1 for r in self.results if r.passed)
        lines = [
            f"Reflection eval (deterministic) — {self.n} cases",
            f"  pass_rate : {self.pass_rate:6.1%} ({passed}/{self.n})",
        ]
        fails = [r for r in self.results if not r.passed]
        if fails:
            lines.append("  failures:")
            for r in fails:
                lines.append(f"    - {r.case.note or r.case.description[:48]!r}")
                for reason in r.failure_reasons():
                    lines.append(f"        · {reason}")
        return "\n".join(lines)


def _repair(case: ReflectionCase) -> list[str]:
    """Run the production deterministic path: sanitize → repair."""
    sanitized = _normalize_kio_list(case.proposed, _VALID_KIOS)
    result = validate_and_repair_plan(
        sanitized,
        has_repo=case.has_repo,
        has_code=case.has_code,
        report_requested=case.report_requested,
    )
    return result.sequence


def evaluate_reflection(dataset: list[ReflectionCase]) -> ReflectionReport:
    """Score the deterministic sanitize+repair gate over ``dataset``."""
    results: list[ReflectionCaseResult] = []
    for case in dataset:
        repaired = _repair(case)
        exact_ok = case.expect is None or repaired == case.expect
        include_ok = set(case.must_include) <= set(repaired)
        exclude_ok = not (set(case.must_exclude) & set(repaired))
        report_ok = (not case.require_report) or ("kio8" in repaired)
        results.append(
            ReflectionCaseResult(
                case=case,
                repaired=repaired,
                exact_ok=exact_ok,
                include_ok=include_ok,
                exclude_ok=exclude_ok,
                report_ok=report_ok,
            )
        )
    return ReflectionReport(results=results)
