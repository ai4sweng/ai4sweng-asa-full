"""Draft reflection — pre-completion outcome check.

Before a workflow is declared COMPLETED, this asks "was the task's intent
actually satisfied?"  Its highest-value check for software engineering is the
classic failure mode of declaring victory on a broken fix: if the Test Re-runner
(kio7) reports failing tests, the run is flagged as completed *with issues*
instead of a clean success, so the final report never claims a green build over
red tests.

Like the other reflections it is pure and deterministic — it inspects the result
payload rather than calling an LLM.  Steps with no completion signal return None,
leaving the running outcome unchanged.
"""

from __future__ import annotations

# Outcome statuses surfaced to the completion gate.
OUTCOME_OK = "OK"
OUTCOME_TESTS_FAILING = "TESTS_FAILING"


def assess_step(kio_id: str, artifact_data: dict | None) -> str | None:
    """Return an outcome status when a completed step signals its result.

    Returns ``OUTCOME_TESTS_FAILING`` when kio7 re-ran tests and any failed,
    ``OUTCOME_OK`` when kio7 ran tests that all passed, and ``None`` for steps
    that carry no completion signal (so the caller keeps the running outcome).
    """
    data = artifact_data or {}
    if kio_id == "kio7":
        failed = data.get("failed", 0) or 0
        passed = data.get("passed", 0) or 0
        total = data.get("total", passed + failed) or 0
        if failed > 0:
            return OUTCOME_TESTS_FAILING
        if total > 0:
            return OUTCOME_OK
    return None
