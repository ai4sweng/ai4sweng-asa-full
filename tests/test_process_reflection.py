"""Tests for process reflection — the pre-dispatch plan precondition gate."""

from apps.orchestrator.src.engine.process_reflection import validate_and_repair_plan


def test_valid_plan_unchanged_with_repo():
    # analyze → detect → patch → retest is well-ordered on an existing repo.
    r = validate_and_repair_plan(["kio3", "kio5", "kio6", "kio7"], has_repo=True, has_code=False)
    assert r.sequence == ["kio3", "kio5", "kio6", "kio7"]
    assert r.changed is False
    assert r.notes == []


def test_patch_without_bug_detection_inserts_kio5():
    # kio6 (patch) needs confirmed bugs → kio5 must be inserted before it.
    r = validate_and_repair_plan(["kio3", "kio6", "kio7"], has_repo=True, has_code=False)
    assert r.sequence == ["kio3", "kio5", "kio6", "kio7"]
    assert r.changed is True
    assert any("inserted kio5" in n for n in r.notes)


def test_retest_without_patch_pulls_in_patch_and_detection():
    # kio7 needs a patch → insert kio6, which itself needs bugs → insert kio5.
    r = validate_and_repair_plan(["kio3", "kio7"], has_repo=True, has_code=False)
    assert r.sequence == ["kio3", "kio5", "kio6", "kio7"]
    assert r.changed is True


def test_analyze_repo_dropped_when_no_repo():
    # kio3 needs a repo on disk; with none present it is unsatisfiable → dropped.
    r = validate_and_repair_plan(["kio3", "kio5"], has_repo=False, has_code=False)
    assert "kio3" not in r.sequence
    assert r.changed is True
    assert any("dropped kio3" in n for n in r.notes)


def test_bug_detect_on_code_snippet_is_allowed():
    # A pasted code snippet satisfies 'code' for kio5 without needing a repo.
    r = validate_and_repair_plan(["kio5"], has_repo=False, has_code=True)
    assert r.sequence == ["kio5"]
    assert r.changed is False


def test_build_then_check_is_valid_codegen_first():
    # "build me X and check bugs" → kio9 produces code, satisfying kio5.
    r = validate_and_repair_plan(["kio9", "kio5"], has_repo=False, has_code=False)
    assert r.sequence == ["kio9", "kio5"]
    assert r.changed is False


def test_bug_detect_without_any_code_is_dropped():
    # No repo, no snippet, no upstream generator → nothing to inspect → dropped.
    r = validate_and_repair_plan(["kio5"], has_repo=False, has_code=False)
    assert r.sequence == []
    assert r.changed is True


def test_duplicates_removed_preserving_order():
    r = validate_and_repair_plan(["kio3", "kio3", "kio5", "kio5"], has_repo=True, has_code=False)
    assert r.sequence == ["kio3", "kio5"]


def test_unknown_partner_kio_passes_through():
    # A capability-announced partner KIO has no rule → must not be blocked.
    r = validate_and_repair_plan(["kio14"], has_repo=False, has_code=False)
    assert r.sequence == ["kio14"]
    assert r.changed is False


def test_length_capped_at_eight():
    r = validate_and_repair_plan(
        ["kio2", "kio3", "kio4", "kio5", "kio6", "kio7", "kio8", "kio9", "kio10"],
        has_repo=True,
        has_code=False,
    )
    assert len(r.sequence) <= 8
