"""Retry Manager — Slide 17 / 18 of the interface spec.

Tracks per-session retry state (attempt counter, backoff timing) and decides
whether a failed KIO task should be retried or escalated to FAILED.

Retry policy
------------
  max_retries:      from the ``retry_policy`` field in the task payload (Slide 8)
  backoff_strategy: "exponential" (default) — wait 2^attempt seconds, cap 60s
                    "linear"                — wait attempt * base_delay seconds

Integration
-----------
  graph_nodes.py run_kio_node catches retryable exceptions, calls
  RetryManager.should_retry(session_id, ...) and, if True, sleeps the backoff
  delay before re-raising (causing LangGraph to re-invoke the node).
"""

from __future__ import annotations

import asyncio
import math
from dataclasses import dataclass
from typing import Any

from loguru import logger

_BASE_DELAY: float = 1.0  # seconds — first retry delay for linear strategy
_MAX_DELAY: float = 60.0  # seconds — cap on any single backoff sleep


@dataclass
class _SessionRetry:
    attempts: int = 0
    max_retries: int = 1
    backoff_strategy: str = "exponential"


class RetryManager:
    """Tracks retry state per session and computes backoff delays.

    All public methods are thread/coroutine safe (dict operations are GIL-atomic
    in CPython; asyncio.sleep does not block other coroutines).
    """

    def __init__(self) -> None:
        self._state: dict[str, _SessionRetry] = {}

    # ------------------------------------------------------------------
    # Public API called from graph_nodes.run_kio_node
    # ------------------------------------------------------------------

    def register(self, session_id: str, retry_policy: dict[str, Any]) -> None:
        """Register (or refresh) the retry policy for a session.

        Called once per KIO dispatch from run_kio_node so the policy from
        the current step's payload is always up to date.
        """
        max_retries = int(retry_policy.get("max_retries", 1))
        strategy = str(retry_policy.get("backoff_strategy", "exponential"))
        if session_id not in self._state:
            self._state[session_id] = _SessionRetry(
                max_retries=max_retries, backoff_strategy=strategy
            )
        else:
            # Update policy without resetting attempt counter (step may retry)
            self._state[session_id].max_retries = max_retries
            self._state[session_id].backoff_strategy = strategy

    def should_retry(self, session_id: str) -> bool:
        """Return True if this session has remaining retry attempts."""
        rs = self._state.get(session_id)
        if rs is None:
            return False
        return rs.attempts < rs.max_retries

    async def wait_and_increment(self, session_id: str) -> int:
        """Sleep the backoff delay and increment the attempt counter.

        Returns the new attempt count (1-based, useful for log messages).
        """
        rs = self._state.get(session_id)
        if rs is None:
            return 0
        rs.attempts += 1
        delay = self._backoff(rs.attempts, rs.backoff_strategy)
        logger.info(
            "[retry_manager] session={} attempt={}/{} backoff={:.1f}s",
            session_id[:8],
            rs.attempts,
            rs.max_retries,
            delay,
        )
        await asyncio.sleep(delay)
        return rs.attempts

    def reset(self, session_id: str) -> None:
        """Reset attempt counter on successful completion of a step."""
        if session_id in self._state:
            self._state[session_id].attempts = 0

    def cleanup(self, session_id: str) -> None:
        """Remove all retry state for a terminal session."""
        self._state.pop(session_id, None)

    def get_attempts(self, session_id: str) -> int:
        """Return how many retry attempts have been made for this session."""
        rs = self._state.get(session_id)
        return rs.attempts if rs else 0

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    @staticmethod
    def _backoff(attempt: int, strategy: str) -> float:
        if strategy == "linear":
            delay = attempt * _BASE_DELAY
        else:  # exponential
            delay = math.pow(2, attempt - 1) * _BASE_DELAY
        return min(delay, _MAX_DELAY)


# ------------------------------------------------------------------
# Process-wide singleton
# ------------------------------------------------------------------

_rm: RetryManager | None = None


def get_retry_manager() -> RetryManager:
    global _rm
    if _rm is None:
        _rm = RetryManager()
    return _rm
