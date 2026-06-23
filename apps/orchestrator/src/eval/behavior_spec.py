"""Behavior spec — one declarative source of truth for *how the platform should
behave*, plus a single categorized scorecard for *how close the live system gets*.

The idea: stop hand-checking prompts. Encode the expected behavior of every
interesting case once, here, then let :func:`run_full_eval` score the running
system against it and report a per-category closeness number.

Two layers, run together but scored separately:

  • pure   — deterministic guarantees (sanitize + reflection repair: drops,
             auto-inserts, length cap, kio8 preservation). No model, always runs.
  • live   — model-dependent behavior (routing exactness, the clarification gate,
             multi-goal plan shape). Needs a reachable planner; skipped (not
             failed) when one isn't supplied.

Each category yields ``(n, passed, skipped)``; the scorecard rolls these into a
single ``spec_closeness`` over everything that was actually scorable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Awaitable, Callable

from .plan_stage_eval import PlanCase, evaluate_plan_stage
from .reflection_eval import ReflectionCase, evaluate_reflection
from .routing_eval import RoutingCase, evaluate

# ---------------------------------------------------------------------------
# The spec: every interesting case, grouped by the behavior it pins down.
# ---------------------------------------------------------------------------

# pure / deterministic — sanitize + reflection repair guarantees.
REFLECTION_CASES: list[ReflectionCase] = [
    # drops: a step whose inputs cannot exist is removed (nothing fabricated).
    ReflectionCase(
        "analyze the repo (no working directory)",
        proposed=["kio3", "kio5"],
        has_repo=False,
        must_exclude=("kio3",),
        note="kio3 needs a repo on disk → dropped",
    ),
    ReflectionCase(
        "generate tests with no code provided",
        proposed=["kio4"],
        has_code=False,
        expect=[],
        note="kio4 needs code → dropped, nothing fabricated",
    ),
    # auto-inserts: safe producers are inserted ahead of a step that needs them.
    ReflectionCase(
        "re-run the tests on this snippet",
        proposed=["kio7"],
        has_code=True,
        must_include=("kio5", "kio6"),
        note="kio7 needs a patch → pulls kio6 → pulls kio5",
    ),
    ReflectionCase(
        "patch this bug (kio5 skipped by planner)",
        proposed=["kio6", "kio7"],
        has_code=True,
        must_include=("kio5",),
        note="kio6 needs confirmed bugs → insert kio5",
    ),
    # length cap + report preservation.
    ReflectionCase(
        "oversized plan must cap at 12 and keep kio8 last",
        proposed=[f"kio{n}" for n in range(2, 13)] + ["kio8"],  # 12 then report
        has_repo=True,
        has_code=True,
        require_report=True,
        note="cap to 12 but never drop the report",
    ),
    # sanitize: invalid / duplicate ids filtered before repair.
    ReflectionCase(
        "invalid + duplicate ids are filtered",
        proposed=["kio99", "kio_security", "kio5", "kio5"],
        has_code=True,
        expect=["kio5"],
        note="unknown ids dropped, dupes collapsed",
    ),
    # requested report re-instated when the planner forgets it.
    ReflectionCase(
        "report requested but planner omitted kio8",
        proposed=["kio3", "kio5", "kio6", "kio7"],
        has_repo=True,
        report_requested=True,
        require_report=True,
        must_include=("kio8",),
        note="kio8 appended as terminal deliverable",
    ),
]

# live / routing — clear, single-intent prompts must hit an *exact* route.
# Cases where extra (defensible) agents may legitimately be added — e.g. a
# security ask pulling in kio12 — belong in the behavioral set below, not here,
# because exact match would wrongly penalise a correct-but-richer plan.
ROUTING_CASES: list[RoutingCase] = [
    RoutingCase("build me a tool that converts CSV to JSON", ["kio9"], note="codegen"),
    RoutingCase(
        "write a python function that reverses a string", ["kio9"], note="codegen"
    ),
    RoutingCase(
        "check this code snippet for bugs",
        ["kio5"],
        has_code=True,
        note="bug detect on snippet",
    ),
]

# live / clarify — gate must fire on off-topic / vague, and NOT on clear asks.
CLARIFY_CASES: list[PlanCase] = [
    PlanCase("I want you to cook fish", expect_clarification=True, note="off-topic"),
    PlanCase("asdfgh qwerty zxcv", expect_clarification=True, note="gibberish"),
    PlanCase("make it better", expect_clarification=True, note="too vague, no context"),
    PlanCase(
        "build me a CLI that converts CSV to JSON",
        expect_clarification=False,
        must_include=("kio9",),
        must_exclude=("kio3",),
        note="clear codegen → must NOT clarify",
    ),
]

# live / long-prompt — multi-goal & behavioral routing: assert the *shape*
# (right agents present/absent, report kept) rather than an exact sequence, so a
# correct-but-richer plan (e.g. a security ask that also pulls in kio12) passes.
LONG_PROMPT_CASES: list[PlanCase] = [
    PlanCase(
        "find security bugs in this repository",
        has_repo=True,
        security_sensitive=True,
        expect_clarification=False,
        must_include=("kio3", "kio5"),
        note="security ask: kio3+kio5 required, kio12 allowed (not forbidden)",
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
    PlanCase(
        "Here is a long story about our team, our deadlines, and our quarterly goals. "
        "We have shipped a lot. Anyway, all I actually need right now is a function that "
        "validates IBAN numbers. Thanks for reading all of this.",
        expect_clarification=False,
        must_include=("kio9",),
        must_exclude=("kio3",),
        note="intent buried in a wall of text → still codegen",
    ),
]


# ---------------------------------------------------------------------------
# Scorecard
# ---------------------------------------------------------------------------


@dataclass
class CategoryScore:
    name: str
    layer: str  # "pure" | "live"
    n: int
    passed: int
    skipped: bool = False  # live category with no planner available

    @property
    def score(self) -> float | None:
        if self.skipped or self.n == 0:
            return None
        return self.passed / self.n

    def line(self) -> str:
        if self.skipped:
            return f"  {self.name:<14} [{self.layer}] : SKIPPED (no live planner)"
        return (
            f"  {self.name:<14} [{self.layer}] : "
            f"{self.score:6.1%} ({self.passed}/{self.n})"
        )


@dataclass
class Scorecard:
    categories: list[CategoryScore] = field(default_factory=list)

    @property
    def scored(self) -> list[CategoryScore]:
        return [c for c in self.categories if not c.skipped and c.n]

    @property
    def spec_closeness(self) -> float | None:
        """Single number: passed / scorable across every non-skipped case."""
        total = sum(c.n for c in self.scored)
        passed = sum(c.passed for c in self.scored)
        return (passed / total) if total else None

    def summary(self) -> str:
        lines = ["=" * 56, "Behavior-spec scorecard", "=" * 56]
        lines += [c.line() for c in self.categories]
        lines.append("-" * 56)
        closeness = self.spec_closeness
        if closeness is None:
            lines.append("  spec_closeness : n/a (nothing scorable)")
        else:
            total = sum(c.n for c in self.scored)
            passed = sum(c.passed for c in self.scored)
            lines.append(f"  spec_closeness : {closeness:6.1%} ({passed}/{total})")
        return "\n".join(lines)

    def to_dict(self) -> dict:
        return {
            "spec_closeness": self.spec_closeness,
            "categories": [
                {
                    "name": c.name,
                    "layer": c.layer,
                    "n": c.n,
                    "passed": c.passed,
                    "score": c.score,
                    "skipped": c.skipped,
                }
                for c in self.categories
            ],
        }


# A planner is duck-typed: needs assess_clarification + plan_workflow.
Planner = object


async def run_full_eval(
    planner: Planner | None = None,
    *,
    pace_seconds: float = 0.0,
) -> tuple[Scorecard, dict[str, object]]:
    """Run the full behavior spec and return ``(scorecard, raw_reports)``.

    The pure (reflection) layer always runs. The live layers (routing, clarify,
    long-prompt) run only when ``planner`` is supplied; otherwise they are marked
    SKIPPED so the scorecard stays honest about what was actually measured.

    ``raw_reports`` carries the underlying per-layer report objects so a caller
    can print detailed failures.
    """
    categories: list[CategoryScore] = []
    raw: dict[str, object] = {}

    # ---- pure layer (always) ----
    refl = evaluate_reflection(REFLECTION_CASES)
    raw["reflection"] = refl
    categories.append(
        CategoryScore(
            "reflection",
            "pure",
            n=refl.n,
            passed=sum(1 for r in refl.results if r.passed),
        )
    )

    # ---- live layers ----
    if planner is None:
        categories.append(CategoryScore("routing", "live", n=len(ROUTING_CASES), passed=0, skipped=True))
        categories.append(CategoryScore("clarify", "live", n=len(CLARIFY_CASES), passed=0, skipped=True))
        categories.append(CategoryScore("long_prompt", "live", n=len(LONG_PROMPT_CASES), passed=0, skipped=True))
        return Scorecard(categories=categories), raw

    async def router(description: str) -> tuple[list[str], bool]:
        kios, _reasoning, used_fallback, _rejected = await planner.plan_workflow(
            description, "eval"
        )
        return kios, used_fallback

    routing = await evaluate(router, ROUTING_CASES)
    raw["routing"] = routing
    categories.append(
        CategoryScore(
            "routing",
            "live",
            n=routing.n,
            passed=sum(1 for r in routing.results if r.exact_match),
        )
    )

    clarify = await evaluate_plan_stage(
        planner, CLARIFY_CASES, pace_seconds=pace_seconds, apply_reflection=True
    )
    raw["clarify"] = clarify
    categories.append(
        CategoryScore(
            "clarify",
            "live",
            n=clarify.n,
            passed=sum(1 for r in clarify.results if r.passed),
        )
    )

    long_prompt = await evaluate_plan_stage(
        planner, LONG_PROMPT_CASES, pace_seconds=pace_seconds, apply_reflection=True
    )
    raw["long_prompt"] = long_prompt
    categories.append(
        CategoryScore(
            "long_prompt",
            "live",
            n=long_prompt.n,
            passed=sum(1 for r in long_prompt.results if r.passed),
        )
    )

    return Scorecard(categories=categories), raw
