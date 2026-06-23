"""Data reflection — between-step adequacy check.

After a KIO returns, this asks "is the result good enough for the steps that come
next?"  When a step produces nothing the downstream KIOs need, the now-pointless
steps are pruned so the pipeline doesn't fabricate work on empty input — e.g.
running the Patch Generator (kio6) and Test Re-runner (kio7) after the Bug
Detector (kio5) confirmed *zero* bugs.

Like process reflection this is pure and deterministic: it inspects the result
payload with simple rules rather than calling an LLM, so the normal case is a
cheap check.  Steps with no rule (including partner-provided KIOs) are always
treated as adequate and never prune anything.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Downstream steps that only make sense once bugs are confirmed.
_BUG_DEPENDENT = ("kio6", "kio7")


@dataclass
class DataReflectionResult:
    adequate: bool
    remaining: list[str]  # downstream KIOs to keep (those scheduled AFTER this step)
    pruned: list[str]  # downstream KIOs removed as pointless
    notes: list[str] = field(default_factory=list)


def reflect_on_result(
    kio_id: str,
    artifact_data: dict | None,
    downstream: list[str],
) -> DataReflectionResult:
    """Judge a completed KIO's result against the downstream plan.

    Args:
        kio_id: the KIO that just finished.
        artifact_data: its JOB_RESULT ``artifact_data`` payload.
        downstream: the list of KIO ids scheduled *after* this step.

    Returns a :class:`DataReflectionResult`.  ``remaining`` is the downstream
    list to keep; when ``pruned`` is non-empty the caller should truncate the
    pipeline accordingly.
    """
    data = artifact_data or {}

    if kio_id == "kio5":
        bugs = data.get("bugs", [])
        bug_count = len(bugs) if isinstance(bugs, list) else 0
        confirmed = data.get("confirmed_count", bug_count)
        if not bug_count and not confirmed:
            pruned = [k for k in downstream if k in _BUG_DEPENDENT]
            if pruned:
                kept = [k for k in downstream if k not in _BUG_DEPENDENT]
                notes = [
                    f"kio5 confirmed no bugs → skipping {', '.join(pruned)} (nothing to patch)"
                ]
                return DataReflectionResult(False, kept, pruned, notes)

    return DataReflectionResult(True, list(downstream), [], [])
