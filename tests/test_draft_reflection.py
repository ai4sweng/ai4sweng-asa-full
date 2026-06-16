"""Tests for draft reflection — the pre-completion outcome check."""

from apps.orchestrator.src.engine.draft_reflection import (
    OUTCOME_OK,
    OUTCOME_TESTS_FAILING,
    assess_step,
)


def test_kio7_failing_tests_flags_unresolved():
    assert assess_step("kio7", {"passed": 2, "failed": 1, "total": 3}) == OUTCOME_TESTS_FAILING


def test_kio7_all_pass_is_ok():
    assert assess_step("kio7", {"passed": 3, "failed": 0, "total": 3}) == OUTCOME_OK


def test_kio7_no_tests_collected_yields_no_signal():
    # total == 0 (e.g. collection failure) → no completion signal either way.
    assert assess_step("kio7", {"passed": 0, "failed": 0, "total": 0}) is None


def test_kio7_total_inferred_from_passed_failed():
    assert assess_step("kio7", {"passed": 1, "failed": 0}) == OUTCOME_OK


def test_other_kio_returns_none():
    assert assess_step("kio5", {"bugs": [{"bug_id": "BUG-001"}]}) is None


def test_partner_kio_returns_none():
    assert assess_step("kio14", {"failed": 5}) is None


def test_missing_artifact_data_is_safe():
    assert assess_step("kio7", None) is None
