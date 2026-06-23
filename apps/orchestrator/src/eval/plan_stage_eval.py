"""Plan-stage evaluation — behavioral checks for large / ambiguous prompts.

``routing_eval`` scores small prompts by exact pipeline match. That's too brittle
for large multi-goal requests, where many pipelines are valid and the things that
actually matter are *behavioral*:

  • does the clarification gate fire when (and only when) it should?
  • does the produced plan **include** the agents the request demands
    (e.g. a security audit → kio12, an audit trail → the kio8 report)?
  • does it **exclude** agents that don't apply (e.g. the repo analyzer on a
    pure code-generation request)?
  • is the report (kio8) never dropped on a big pipeline?

The planner is injected (duck-typed: anything with ``assess_clarification`` and
``plan_workflow``), so the same harness runs against the live Haiku-backed
``LmEngineClient`` or a deterministic stub in tests.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class PlanCase:
    description: str
    has_repo: bool = False
    has_code: bool = False
    security_sensitive: bool = False
    # None = don't assert; True/False = require the clarification gate to (not) fire.
    expect_clarification: bool | None = None
    must_include: tuple[str, ...] = ()
    must_exclude: tuple[str, ...] = ()
    require_report: bool = False  # kio8 must be in the pipeline
    note: str = ""


@dataclass
class PlanCaseResult:
    case: PlanCase
    clarified: bool
    predicted: list[str]
    used_fallback: bool
    clarification_ok: bool
    include_ok: bool
    exclude_ok: bool
    report_ok: bool

    @property
    def passed(self) -> bool:
        return self.clarification_ok and self.include_ok and self.exclude_ok and self.report_ok

    def failure_reasons(self) -> list[str]:
        out: list[str] = []
        if not self.clarification_ok:
            out.append(f"clarification: expected={self.case.expect_clarification} got={self.clarified}")
        if not self.include_ok:
            missing = sorted(set(self.case.must_include) - set(self.predicted))
            out.append(f"missing required: {missing}")
        if not self.exclude_ok:
            present = sorted(set(self.case.must_exclude) & set(self.predicted))
            out.append(f"forbidden present: {present}")
        if not self.report_ok:
            out.append("report (kio8) missing")
        return out


@dataclass
class PlanEvalReport:
    results: list[PlanCaseResult] = field(default_factory=list)

    @property
    def n(self) -> int:
        return len(self.results)

    @property
    def pass_rate(self) -> float:
        return (sum(1 for r in self.results if r.passed) / self.n) if self.n else 0.0

    @property
    def fallback_rate(self) -> float:
        return (sum(1 for r in self.results if r.used_fallback) / self.n) if self.n else 0.0

    def summary(self) -> str:
        passed = sum(1 for r in self.results if r.passed)
        lines = [
            f"Plan-stage eval — {self.n} cases",
            f"  pass_rate     : {self.pass_rate:6.1%} ({passed}/{self.n})",
            f"  fallback_rate : {self.fallback_rate:6.1%}",
        ]
        fails = [r for r in self.results if not r.passed]
        if fails:
            lines.append("  failures:")
            for r in fails:
                lines.append(f"    - {r.case.note or r.case.description[:48]!r}")
                lines.append(f"        got={r.predicted}")
                for reason in r.failure_reasons():
                    lines.append(f"        · {reason}")
        return "\n".join(lines)


async def evaluate_plan_stage(
    planner,
    dataset: list[PlanCase],
    *,
    pace_seconds: float = 0.0,
    apply_reflection: bool = False,
) -> PlanEvalReport:
    """Run the clarify + plan chain over ``dataset`` and score behavior.

    ``apply_reflection`` scores the *effective* plan the orchestrator would
    dispatch (raw plan → deterministic ``validate_and_repair_plan``), not the
    planner's raw guess — so guarantees the reflection gate adds (e.g. a
    requested-but-omitted report) are credited, matching production. Left False
    for the stub-backed unit tests that assert raw-plan scoring mechanics.

    ``planner`` must expose ``assess_clarification(description, session_id)`` and
    ``plan_workflow(description, session_id, *, intent=...)``. Both stages are run
    for every case (in production a clarification short-circuits planning, but the
    eval scores the plan it *would* produce so plan quality stays measurable).

    ``pace_seconds`` sleeps between cases — each case makes 2 model calls, so a
    low hosted rate limit (e.g. Haiku at 5 RPM) requires ~24s/case to avoid
    falling back. Left at 0 for the stub-backed unit tests.
    """
    import asyncio

    from ..engine.process_reflection import validate_and_repair_plan
    from ..services.prompt_intent import extract_intent, format_intent

    results: list[PlanCaseResult] = []
    for i, case in enumerate(dataset):
        if pace_seconds and i:
            await asyncio.sleep(pace_seconds)
        clar = await planner.assess_clarification(case.description, "eval")
        clarified = clar is not None

        intent = format_intent(
            extract_intent(
                case.description,
                has_code=case.has_code,
                has_repo=case.has_repo,
                security_sensitive=case.security_sensitive,
            )
        )
        seq, _reasoning, used_fallback, _rejected = await planner.plan_workflow(
            case.description, "eval", intent=intent
        )
        predicted = list(seq)
        if apply_reflection:
            predicted = validate_and_repair_plan(
                predicted,
                has_repo=case.has_repo,
                has_code=case.has_code,
                report_requested=case.require_report,
            ).sequence or predicted

        clar_ok = case.expect_clarification is None or clarified == case.expect_clarification
        inc_ok = set(case.must_include) <= set(predicted)
        exc_ok = not (set(case.must_exclude) & set(predicted))
        rep_ok = (not case.require_report) or ("kio8" in predicted)

        results.append(
            PlanCaseResult(
                case=case,
                clarified=clarified,
                predicted=predicted,
                used_fallback=used_fallback,
                clarification_ok=clar_ok,
                include_ok=inc_ok,
                exclude_ok=exc_ok,
                report_ok=rep_ok,
            )
        )
    return PlanEvalReport(results=results)


# A small starter dataset spanning the behaviors that matter for the plan stage.
# Extend with real traffic over time.
DEFAULT_DATASET: list[PlanCase] = [
    PlanCase(
        "I want you to cook fish",
        expect_clarification=True,
        note="off-topic → must clarify",
    ),
    PlanCase(
        "build me a CLI that converts CSV to JSON",
        expect_clarification=False,
        must_include=("kio9",),
        must_exclude=("kio3",),
        note="pure codegen, no existing code",
    ),
    PlanCase(
        "analyze this repository for OWASP security issues and give me an audit report",
        has_repo=True,
        security_sensitive=True,
        expect_clarification=False,
        must_include=("kio3", "kio12", "kio8"),
        require_report=True,
        note="security audit + report",
    ),
    PlanCase(
        # The large multi-goal initiative — clarification is stochastic, so we
        # don't assert it; we DO assert the structural guarantees (report kept,
        # security + tests + new build all routed).
        "Analyze our existing FastAPI monolith for bugs and security problems, patch the "
        "high-severity ones after human approval, generate a pytest suite, build a brand-new "
        "usage-metering microservice from scratch, optimise a small edge ML model for energy, "
        "and produce an executive audit report at the end.",
        has_repo=True,
        has_code=True,
        security_sensitive=True,
        must_include=("kio3", "kio5", "kio12", "kio4", "kio9", "kio8"),
        require_report=True,
        note="large multi-goal initiative (report must survive the cap)",
    ),
]
