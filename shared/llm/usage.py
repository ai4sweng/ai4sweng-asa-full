"""Per-request LLM token accounting.

ObservedLLMProvider.complete() records every call's token counts here; the
kio_base handler wrapper calls begin() before invoking a KIO handler and
snapshot() after, then stamps the totals onto the JOB_RESULT payload so the
orchestrator can surface "tokens used" per KIO and per workflow.

ContextVar-scoped (like observed.py's attribution vars) so concurrent async
handlers in one process each accumulate independently.
"""

from __future__ import annotations

import contextvars

# Holds a mutable [tokens_in, tokens_out] accumulator for the current request,
# or None when no request scope is active.
_usage: contextvars.ContextVar[list[int] | None] = contextvars.ContextVar(
    "llm_usage", default=None
)


def begin() -> None:
    """Start a fresh accounting scope for the current request."""
    _usage.set([0, 0])


def record(tokens_in: int, tokens_out: int) -> None:
    """Add one LLM call's token counts to the active scope (no-op if none)."""
    acc = _usage.get()
    if acc is not None:
        acc[0] += tokens_in or 0
        acc[1] += tokens_out or 0


def snapshot() -> tuple[int, int]:
    """Return (tokens_in, tokens_out) accumulated since begin()."""
    acc = _usage.get()
    return (acc[0], acc[1]) if acc is not None else (0, 0)
