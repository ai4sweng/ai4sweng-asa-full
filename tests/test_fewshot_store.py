"""Tests for dynamic few-shot retrieval used by the routing planner."""

from apps.orchestrator.src.services.fewshot_store import (
    DEFAULT_EXEMPLARS,
    Exemplar,
    format_examples,
    retrieve,
)


def test_retrieve_surfaces_most_similar_exemplar_first():
    # A bug-finding query should rank the repo bug-finding exemplar at the top.
    top = retrieve("find bugs in this repository", k=1)
    assert top[0].kio_sequence == ("kio3", "kio5")


def test_retrieve_codegen_query_returns_codegen_exemplar():
    top = retrieve("build me a CSV converter tool", k=1)
    assert top[0].kio_sequence == ("kio9",)


def test_retrieve_respects_k():
    assert len(retrieve("find bugs in this repository", k=2)) == 2


def test_retrieve_no_overlap_falls_back_to_bank():
    # Gibberish with no shared tokens → still returns examples (never empty).
    out = retrieve("zzz qqq xyzzy", k=3)
    assert len(out) == 3


def test_retrieve_excludes_zero_overlap_when_some_match():
    # "snippet" should pull the snippet exemplar, not unrelated codegen ones.
    out = retrieve("check this snippet for bugs", k=3)
    assert any(e.kio_sequence == ("kio5",) for e in out)


def test_format_examples_renders_valid_json_lines():
    text = format_examples([Exemplar("do a thing", ("kio9",), "because")])
    assert text.startswith("Examples:")
    assert '"kio_sequence": ["kio9"]' in text
    assert '"reasoning": "because"' in text


def test_default_bank_is_nonempty():
    assert len(DEFAULT_EXEMPLARS) >= 5
