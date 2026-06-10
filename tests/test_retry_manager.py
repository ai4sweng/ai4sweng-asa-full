"""Unit tests for RetryManager (retry_manager.py)."""
import math
import pytest
from unittest.mock import AsyncMock, patch

from apps.orchestrator.src.engine.retry_manager import RetryManager


@pytest.fixture()
def rm():
    return RetryManager()


# ── register ──────────────────────────────────────────────────────────────────

def test_register_creates_session_entry(rm):
    rm.register("s1", {"max_retries": 3, "backoff_strategy": "exponential"})
    assert rm.get_attempts("s1") == 0


def test_register_updates_policy_without_resetting_counter(rm):
    rm.register("s1", {"max_retries": 1, "backoff_strategy": "linear"})
    rm._state["s1"].attempts = 1
    rm.register("s1", {"max_retries": 5, "backoff_strategy": "exponential"})
    assert rm._state["s1"].max_retries == 5
    assert rm._state["s1"].attempts == 1  # counter preserved


def test_register_defaults_max_retries_to_one(rm):
    rm.register("s1", {})
    assert rm._state["s1"].max_retries == 1


def test_register_defaults_strategy_to_exponential(rm):
    rm.register("s1", {})
    assert rm._state["s1"].backoff_strategy == "exponential"


# ── should_retry ──────────────────────────────────────────────────────────────

def test_should_retry_true_when_attempts_below_max(rm):
    rm.register("s1", {"max_retries": 3})
    assert rm.should_retry("s1") is True


def test_should_retry_false_when_attempts_equal_max(rm):
    rm.register("s1", {"max_retries": 1})
    rm._state["s1"].attempts = 1
    assert rm.should_retry("s1") is False


def test_should_retry_false_for_unknown_session(rm):
    assert rm.should_retry("unknown") is False


# ── backoff calculation ───────────────────────────────────────────────────────

@pytest.mark.parametrize("attempt,expected", [
    (1, 1.0),   # 2^0 = 1
    (2, 2.0),   # 2^1 = 2
    (3, 4.0),   # 2^2 = 4
    (4, 8.0),   # 2^3 = 8
    (7, 60.0),  # 2^6 = 64, capped to 60
])
def test_exponential_backoff(attempt, expected):
    result = RetryManager._backoff(attempt, "exponential")
    assert result == pytest.approx(expected)


@pytest.mark.parametrize("attempt,expected", [
    (1, 1.0),
    (2, 2.0),
    (5, 5.0),
    (61, 60.0),  # capped
])
def test_linear_backoff(attempt, expected):
    result = RetryManager._backoff(attempt, "linear")
    assert result == pytest.approx(expected)


def test_unknown_strategy_falls_back_to_exponential(rm):
    # unknown strategy treated as exponential
    result = RetryManager._backoff(2, "fibonacci")
    assert result == pytest.approx(2.0)


# ── wait_and_increment ────────────────────────────────────────────────────────

async def test_wait_and_increment_returns_new_attempt_count(rm):
    rm.register("s1", {"max_retries": 3})
    with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        count = await rm.wait_and_increment("s1")
    assert count == 1
    assert rm.get_attempts("s1") == 1


async def test_wait_and_increment_sleeps_correct_duration(rm):
    rm.register("s1", {"max_retries": 3, "backoff_strategy": "linear"})
    with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        await rm.wait_and_increment("s1")  # attempt=1 → 1.0s
    mock_sleep.assert_awaited_once_with(pytest.approx(1.0))


async def test_wait_and_increment_returns_zero_for_unknown_session(rm):
    with patch("asyncio.sleep", new_callable=AsyncMock):
        count = await rm.wait_and_increment("ghost")
    assert count == 0


async def test_sequential_increments_accumulate(rm):
    rm.register("s1", {"max_retries": 3})
    with patch("asyncio.sleep", new_callable=AsyncMock):
        await rm.wait_and_increment("s1")
        await rm.wait_and_increment("s1")
    assert rm.get_attempts("s1") == 2


# ── reset ─────────────────────────────────────────────────────────────────────

async def test_reset_clears_attempt_counter(rm):
    rm.register("s1", {"max_retries": 3})
    with patch("asyncio.sleep", new_callable=AsyncMock):
        await rm.wait_and_increment("s1")
    rm.reset("s1")
    assert rm.get_attempts("s1") == 0


def test_reset_nonexistent_session_is_noop(rm):
    rm.reset("ghost")  # should not raise


# ── cleanup ───────────────────────────────────────────────────────────────────

def test_cleanup_removes_session(rm):
    rm.register("s1", {"max_retries": 2})
    rm.cleanup("s1")
    assert rm.get_attempts("s1") == 0  # 0 because session gone
    assert "s1" not in rm._state


def test_cleanup_nonexistent_session_is_noop(rm):
    rm.cleanup("ghost")  # should not raise


# ── get_attempts ─────────────────────────────────────────────────────────────

def test_get_attempts_returns_zero_for_unknown(rm):
    assert rm.get_attempts("x") == 0


def test_get_attempts_tracks_correctly(rm):
    rm.register("s1", {"max_retries": 5})
    rm._state["s1"].attempts = 3
    assert rm.get_attempts("s1") == 3


# ── retry gate interaction ────────────────────────────────────────────────────

async def test_retry_gate_exhausted_after_max_retries(rm):
    rm.register("s1", {"max_retries": 2})
    with patch("asyncio.sleep", new_callable=AsyncMock):
        await rm.wait_and_increment("s1")
        assert rm.should_retry("s1") is True
        await rm.wait_and_increment("s1")
        assert rm.should_retry("s1") is False
