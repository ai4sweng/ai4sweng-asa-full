"""
Bug kind enum and normalization for LLM bug findings.
"""

from __future__ import annotations

import re
from enum import Enum


class BugKind(str, Enum):
    OFF_BY_ONE = "OFF_BY_ONE"
    NULL_POINTER = "NULL_POINTER"
    SQL_INJECTION = "SQL_INJECTION"
    PERFORMANCE = "PERFORMANCE"
    TEST_GAP = "TEST_GAP"
    SECURITY = "SECURITY"
    STYLE = "STYLE"
    OTHER = "OTHER"


# Highest-priority kind wins when LLM returns pipe-separated categories.
_KIND_PRIORITY: list[BugKind] = [
    BugKind.SQL_INJECTION,
    BugKind.SECURITY,
    BugKind.NULL_POINTER,
    BugKind.OFF_BY_ONE,
    BugKind.PERFORMANCE,
    BugKind.TEST_GAP,
    BugKind.STYLE,
    BugKind.OTHER,
]

_TOKEN_ALIASES: dict[str, BugKind] = {
    "off_by_one": BugKind.OFF_BY_ONE,
    "off-by-one": BugKind.OFF_BY_ONE,
    "offbyone": BugKind.OFF_BY_ONE,
    "pagination": BugKind.OFF_BY_ONE,
    "null_pointer": BugKind.NULL_POINTER,
    "null-pointer": BugKind.NULL_POINTER,
    "nullpointer": BugKind.NULL_POINTER,
    "null_access": BugKind.NULL_POINTER,
    "null": BugKind.NULL_POINTER,
    "sql_injection": BugKind.SQL_INJECTION,
    "sql-injection": BugKind.SQL_INJECTION,
    "sqli": BugKind.SQL_INJECTION,
    "sql injection": BugKind.SQL_INJECTION,
    "performance": BugKind.PERFORMANCE,
    "n_plus_one": BugKind.PERFORMANCE,
    "n+1": BugKind.PERFORMANCE,
    "test": BugKind.TEST_GAP,
    "test_gap": BugKind.TEST_GAP,
    "missing_test": BugKind.TEST_GAP,
    "coverage": BugKind.TEST_GAP,
    "security": BugKind.SECURITY,
    "logging": BugKind.SECURITY,
    "credential": BugKind.SECURITY,
    "credential_log": BugKind.SECURITY,
    "style": BugKind.STYLE,
    "bug": BugKind.OTHER,
    "other": BugKind.OTHER,
}

for kind in BugKind:
    _TOKEN_ALIASES.setdefault(kind.value.lower(), kind)
    _TOKEN_ALIASES.setdefault(kind.value, kind)


def _tokenize_kind(raw: str) -> list[str]:
    text = raw.strip()
    if not text:
        return []
    if "|" in text:
        return [part.strip() for part in text.split("|") if part.strip()]
    if "," in text:
        return [part.strip() for part in text.split(",") if part.strip()]
    return [text]


def _map_token(token: str) -> BugKind:
    cleaned = token.strip().lower().replace("-", "_").replace(" ", "_")
    cleaned = re.sub(r"_+", "_", cleaned)

    if cleaned in _TOKEN_ALIASES:
        return _TOKEN_ALIASES[cleaned]

    upper = token.strip().upper().replace("-", "_").replace(" ", "_")
    try:
        return BugKind(upper)
    except ValueError:
        pass

    for alias, kind in _TOKEN_ALIASES.items():
        if alias in cleaned or cleaned in alias:
            return kind

    return BugKind.OTHER


def _pick_primary(mapped: list[BugKind]) -> BugKind:
    if not mapped:
        return BugKind.OTHER
    best = mapped[0]
    best_rank = len(_KIND_PRIORITY)
    for kind in mapped:
        try:
            rank = _KIND_PRIORITY.index(kind)
        except ValueError:
            rank = len(_KIND_PRIORITY)
        if rank < best_rank:
            best = kind
            best_rank = rank
    return best


def normalize_kind(raw: str | BugKind | None) -> tuple[BugKind, list[str], bool]:
    """
    Normalize a raw LLM kind string.

    Returns (primary_kind, tags as enum value strings, was_normalized).
    Never raises — unknown tokens map to OTHER.
    """
    if isinstance(raw, BugKind):
        return raw, [raw.value], False

    original = str(raw or "").strip()
    if not original:
        return BugKind.OTHER, [BugKind.OTHER.value], True

    tokens = _tokenize_kind(original)
    mapped: list[BugKind] = []
    seen: set[BugKind] = set()
    for token in tokens:
        kind = _map_token(token)
        if kind not in seen:
            seen.add(kind)
            mapped.append(kind)

    if not mapped:
        mapped = [BugKind.OTHER]

    primary = _pick_primary(mapped)
    tags = [k.value for k in mapped]

    canonical = original.upper().replace("-", "_").replace(" ", "_")
    was_normalized = len(tokens) > 1 or canonical != primary.value

    return primary, tags, was_normalized


def enrich_bug_dict(bug: dict) -> dict:
    """Apply kind normalization to a plain bug dict (fallback / legacy paths)."""
    primary, tags, normalized = normalize_kind(bug.get("kind"))
    bug["kind"] = primary.value
    bug["tags"] = tags
    bug["normalized_kind"] = normalized
    return bug
