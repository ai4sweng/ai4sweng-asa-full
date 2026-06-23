"""Pipeline length cap + report-preservation in _normalize_kio_list.

Large multi-goal prompts can legitimately need most of the ~12 KIOs; the old
cap of 8 silently truncated the tail and often dropped kio8 (the report). The
normalizer now caps at 12 and never lets truncation drop a selected report.
"""

from apps.orchestrator.src.services.lm_client import (
    _MAX_KIO_SEQUENCE_LEN,
    _normalize_kio_list,
)

_VALID = {f"kio{n}" for n in range(2, 14)}


def test_filters_invalid_and_dedupes_preserving_order():
    out = _normalize_kio_list(["kio3", "bogus", "KIO3", "kio5", 99], _VALID)
    assert out == ["kio3", "kio5"]


def test_cap_is_twelve_not_eight():
    # A 10-agent plan (the realistic large-prompt case) must survive intact.
    ten = ["kio3", "kio5", "kio12", "kio4", "kio11", "kio6", "kio7", "kio9", "kio10", "kio8"]
    assert _normalize_kio_list(ten, _VALID) == ten
    assert _MAX_KIO_SEQUENCE_LEN == 12


def test_report_preserved_when_cap_would_drop_it():
    # 13 valid ids with kio8 last → capped to 12, but kio8 must be re-instated.
    raw = [f"kio{n}" for n in range(2, 14)] + ["kio8"]  # kio2..kio13 then dup kio8
    # de-dup leaves kio2..kio13 (12 items) — exactly at the cap, kio8 already in.
    out = _normalize_kio_list(raw, _VALID)
    assert "kio8" in out
    assert len(out) == _MAX_KIO_SEQUENCE_LEN


def test_report_reinstated_in_last_slot_when_truncated():
    # Synthetic valid set so we can build >12 ids; put kio8 PAST the cap boundary
    # (12 non-report ids first, then kio8) to exercise the re-instatement path.
    valid = {f"kio{n}" for n in range(2, 30)}
    raw = [f"kio{n}" for n in range(9, 21)] + ["kio8"]  # kio9..kio20 (12), then kio8
    out = _normalize_kio_list(raw, valid)
    assert len(out) == _MAX_KIO_SEQUENCE_LEN
    assert out[-1] == "kio8"  # report re-instated in the final slot
    assert out[:-1] == [f"kio{n}" for n in range(9, 20)]  # first 11 kept


def test_non_list_returns_empty():
    assert _normalize_kio_list(None, _VALID) == []
    assert _normalize_kio_list("kio3", _VALID) == []
