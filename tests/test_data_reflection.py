"""Tests for data reflection — the between-step adequacy / prune check."""

from apps.orchestrator.src.engine.data_reflection import reflect_on_result


def test_no_bugs_prunes_patch_and_retest():
    r = reflect_on_result("kio5", {"bugs": [], "confirmed_count": 0}, ["kio6", "kio7", "kio8"])
    assert r.adequate is False
    assert r.remaining == ["kio8"]  # report kept
    assert r.pruned == ["kio6", "kio7"]
    assert any("no bugs" in n for n in r.notes)


def test_bugs_found_keeps_full_downstream():
    r = reflect_on_result(
        "kio5",
        {"bugs": [{"bug_id": "BUG-001"}], "confirmed_count": 1},
        ["kio6", "kio7", "kio8"],
    )
    assert r.adequate is True
    assert r.remaining == ["kio6", "kio7", "kio8"]
    assert r.pruned == []


def test_no_bugs_but_nothing_to_prune():
    # kio5 is last (or only a report follows) → nothing bug-dependent to remove.
    r = reflect_on_result("kio5", {"bugs": []}, ["kio8"])
    assert r.adequate is True
    assert r.remaining == ["kio8"]
    assert r.pruned == []


def test_confirmed_count_missing_falls_back_to_len():
    r = reflect_on_result("kio5", {"bugs": []}, ["kio6"])
    assert r.pruned == ["kio6"]


def test_other_kio_is_passthrough():
    r = reflect_on_result("kio3", {"findings": []}, ["kio5", "kio6"])
    assert r.adequate is True
    assert r.remaining == ["kio5", "kio6"]
    assert r.pruned == []


def test_partner_kio_never_prunes():
    r = reflect_on_result("kio14", {}, ["kio6", "kio7"])
    assert r.adequate is True
    assert r.pruned == []


def test_missing_artifact_data_is_safe():
    r = reflect_on_result("kio5", None, ["kio6", "kio7"])
    # No data → treated as no confirmed bugs → patch/retest pruned.
    assert r.pruned == ["kio6", "kio7"]
