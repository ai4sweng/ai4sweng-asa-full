"""Process reflection — validate a proposed KIO plan before dispatch.

This is the "Think & Plan" reflection step: after the planner (LLM or a manual
caller) proposes a ``kio_sequence``, we check each KIO's preconditions are
satisfiable *given what precedes it* and the inputs available at the start
(a repo on disk or a code snippet).  Violations are repaired deterministically
so a wrong-first-KIO never reaches dispatch:

  • a KIO that needs confirmed bugs (kio6) but has none before it
    → insert the bug detector (kio5) ahead of it
  • a KIO that needs a patch (kio7) but none before it
    → insert the patch generator (kio6) ahead of it (which in turn pulls kio5)
  • a KIO that needs source code / a repo that simply doesn't exist
    (e.g. kio3 "analyze repo" with no working directory)
    → drop it, because there is nothing to act on

The rules are intentionally conservative and only describe the known first-party
KIOs.  Unknown (partner-provided) KIOs have no rule and pass through untouched,
so capability-announced partners are never blocked by this gate.

This module is pure and deterministic — no I/O, no LLM call — so the common case
is a cheap, testable check rather than an extra model round-trip.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# What each KIO needs to be *available* before it can run (input "products").
_REQUIRES: dict[str, frozenset[str]] = {
    "kio3": frozenset({"repo"}),  # Repo Analyzer reads an existing codebase from disk
    "kio4": frozenset({"code"}),  # Test Generator needs code to test
    "kio5": frozenset({"code"}),  # Bug Detector needs code/analysis to inspect
    "kio6": frozenset({"bugs"}),  # Patch Generator needs confirmed bugs
    "kio7": frozenset({"patch"}),  # Test Re-runner needs a patch to validate
}

# What each KIO makes available once it has run (output "products").
_PRODUCES: dict[str, frozenset[str]] = {
    "kio2": frozenset({"plan"}),
    "kio3": frozenset({"analysis", "code"}),
    "kio4": frozenset({"tests"}),
    "kio5": frozenset({"bugs"}),
    "kio6": frozenset({"patch", "code"}),
    "kio7": frozenset({"test_results"}),
    "kio8": frozenset({"report"}),
    "kio9": frozenset({"code"}),  # Code Generator writes new code
}

# Products we will *auto-insert* a producer for when missing.  We only do this
# for steps that are always safe to add and never change the user's intent:
#   - confirming bugs before patching (kio5)
#   - patching before re-testing (kio6)
# We deliberately do NOT fabricate "code"/"analysis"/"repo": inserting a code
# generator (kio9) or analyzer (kio3) would change what the user asked for, so a
# step that needs code with none available is dropped instead.
_PRODUCER_OF: dict[str, str] = {
    "bugs": "kio5",
    "patch": "kio6",
}

_MAX_PLAN_LEN = 8


@dataclass
class ReflectionResult:
    sequence: list[str]
    changed: bool
    notes: list[str] = field(default_factory=list)


def validate_and_repair_plan(
    kio_sequence: list[str],
    *,
    has_repo: bool,
    has_code: bool,
) -> ReflectionResult:
    """Check + repair a proposed KIO plan against its preconditions.

    Args:
        kio_sequence: the proposed ordered list of KIO ids.
        has_repo: True if a repository is available on disk (working_directory).
        has_code: True if a code snippet was supplied (initial_context["code"]).

    Returns a :class:`ReflectionResult` with the (possibly repaired) sequence,
    whether anything changed, and human-readable notes describing each repair.
    """
    # Products available before any KIO runs.
    available: set[str] = set()
    if has_repo:
        available |= {"repo", "code"}
    if has_code:
        available |= {"code"}

    original = list(kio_sequence)
    queue = list(kio_sequence)
    out: list[str] = []
    notes: list[str] = []
    attempted: set[str] = set()  # producers we've already tried to insert (loop guard)

    i = 0
    guard = 0
    while i < len(queue):
        guard += 1
        if guard > 100:  # pathological safety bound
            break

        kio = queue[i]
        missing = _REQUIRES.get(kio, frozenset()) - available

        if missing:
            # Try to satisfy by inserting a safe producer ahead of this KIO.
            inserted = False
            for product in sorted(missing):
                producer = _PRODUCER_OF.get(product)
                if producer and producer not in attempted and producer not in out:
                    attempted.add(producer)
                    queue.insert(i, producer)
                    notes.append(f"inserted {producer} before {kio} (it needs '{product}')")
                    inserted = True
                    break
            if inserted:
                continue  # re-process starting at the inserted producer

            # Unsatisfiable (e.g. needs a repo/code that does not exist) → drop it.
            notes.append(
                f"dropped {kio} (missing {', '.join(sorted(missing))}; nothing produces it)"
            )
            i += 1
            continue

        # Preconditions satisfied (or no rule) — accept, dedup, advance availability.
        if kio not in out:
            out.append(kio)
            available |= _PRODUCES.get(kio, frozenset())
        i += 1

    out = out[:_MAX_PLAN_LEN]
    return ReflectionResult(sequence=out, changed=(out != original), notes=notes)
